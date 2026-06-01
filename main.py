"""
סחבק — WhatsApp Personal Assistant Bot
Production-grade Flask server for Railway deployment.

Stack:
  • WhatsApp Cloud API (Meta Graph API)  — messaging
  • Google Gemini (google-genai SDK)     — natural-language understanding
                                           via real Function / Tool Calling
  • Google Calendar API                  — event creation
  • SQLite (WAL)                          — budget / tasks / context storage

Key reliability features:
  1. Async webhook handling  — ACK Meta in milliseconds, work on a thread pool
                               (prevents WhatsApp timeouts + duplicate re-delivery).
  2. Exponential backoff     — every Gemini call is retried with jitter on
                               transient errors (rate-limit / overload / 5xx).
  3. Function Calling        — Gemini returns strict, typed tool calls instead of
                               free-text we have to parse; supports several
                               actions in a single message (parallel calls).
  4. Voice / image / docs    — handled through the same async + retry pipeline.
"""
from __future__ import annotations  # makes type hints version-proof (3.9+)

import os
import re
import json
import time
import random
import hmac
import hashlib
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests as http_requests
from flask import Flask, request, jsonify

# Google Calendar (google-api-python-client + google-auth)
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Gemini — new unified SDK. Replaces the deprecated `google-generativeai`
# package (legacy SDK deprecated 2025-11-30).
from google import genai
from google.genai import types

# Newer SDK versions expose typed error classes; fall back gracefully if not.
try:
    from google.genai import errors as genai_errors  # type: ignore
except Exception:  # pragma: no cover
    genai_errors = None

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger('sahbak')

# ─────────────────────────────────────────────
# App & Config
# ─────────────────────────────────────────────
app = Flask(__name__)

VERIFY_TOKEN         = os.getenv('VERIFY_TOKEN', 'sahbak-verify-2026')
WHATSAPP_TOKEN       = os.getenv('WHATSAPP_TOKEN')
PHONE_NUMBER_ID      = os.getenv('PHONE_NUMBER_ID')
GOOGLE_CREDENTIALS   = os.getenv('GOOGLE_CREDENTIALS')
CALENDAR_ID          = os.getenv('CALENDAR_ID', 'primary')
APP_SECRET           = os.getenv('APP_SECRET')
GEMINI_API_KEY       = os.getenv('GEMINI_API_KEY')

GEMINI_MODEL         = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')
WHATSAPP_API_VERSION = os.getenv('WHATSAPP_API_VERSION', 'v21.0')
TIMEZONE_NAME        = os.getenv('TIMEZONE', 'Asia/Jerusalem')

# DB path defaults to ./data/sahbak.db (works locally AND on Railway).
# To survive redeploys on Railway, attach a Volume and point DB_PATH at its
# mount path, e.g. DB_PATH=/app/data/sahbak.db
DB_FILE = os.getenv('DB_PATH', os.path.join('data', 'sahbak.db'))

# How many webhooks we are willing to process concurrently. A bounded pool
# protects the box from an unbounded thread explosion if many messages arrive
# at once (far safer than spawning one raw Thread per message).
MAX_WORKERS = int(os.getenv('MAX_WORKERS', '8'))
_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix='sahbak-msg')

# Gemini client (constructing it makes no network call, so module-level is fine)
_genai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


def get_genai_client():
    return _genai_client


# ─────────────────────────────────────────────
# Time — single source of truth for "now"
# ─────────────────────────────────────────────
# Railway containers run in UTC. A naive datetime.now() there makes "tomorrow
# at 9", the budget's current month, and task timestamps all 2–3 hours off from
# Israel — which silently lands events/expenses on the wrong day or month.
# Everything below goes through now_local() instead.
try:
    LOCAL_TZ = ZoneInfo(TIMEZONE_NAME)
except Exception:
    logger.warning('Could not load timezone "%s" (is the "tzdata" package '
                   'installed?). Falling back to system local time.', TIMEZONE_NAME)
    LOCAL_TZ = None

# Hebrew weekday names, indexed by datetime.weekday() (Monday=0 … Sunday=6).
HEB_WEEKDAYS = ['שני', 'שלישי', 'רביעי', 'חמישי', 'שישי', 'שבת', 'ראשון']


def now_local() -> datetime:
    """Timezone-aware current time (Israel by default)."""
    return datetime.now(LOCAL_TZ) if LOCAL_TZ else datetime.now()


# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────
BUDGET_CATEGORIES_HE = {
    'דיור': '🏠', 'רכב': '🚗', 'נופש': '✈️',
    'מזון': '🍔', 'בריאות': '💊', 'חינוך': '📚',
    'בילויים': '🎉', 'קניות': '🛒', 'הכנסה': '💰',
}
VALID_CATEGORIES = list(BUDGET_CATEGORIES_HE.keys())

DEFAULT_BUDGET_LIMITS = {
    'דיור': 5000, 'רכב': 2000, 'נופש': 1500,
    'מזון': 3000, 'בריאות': 1000, 'חינוך': 1500,
    'בילויים': 800, 'קניות': 1000,
}

TASK_QUADRANTS_EMOJI = {
    'חשוב דחוף':        '🔴',
    'חשוב לא דחוף':     '🟡',
    'דחוף לא חשוב':     '🟠',
    'לא דחוף לא חשוב':  '🟢',
}
VALID_QUADRANTS = list(TASK_QUADRANTS_EMOJI.keys())

SUPPORTED_IMAGE_TYPES  = {'image'}
SUPPORTED_AUDIO_TYPES  = {'audio'}
SUPPORTED_DOC_TYPES    = {'document'}
MEDIA_TYPES            = SUPPORTED_IMAGE_TYPES | SUPPORTED_AUDIO_TYPES | SUPPORTED_DOC_TYPES

# WhatsApp hard limit for a single text message body
WHATSAPP_TEXT_LIMIT = 4096
# Gemini inline-request size guard (~20MB ceiling; stay safely under it)
MAX_MEDIA_BYTES = 18 * 1024 * 1024

# Keep the dedup table from growing forever — drop ids older than this.
PROCESSED_TTL_DAYS = 3


# ═════════════════════════════════════════════
# Retry — Exponential backoff with jitter  (Doc §2)
# ═════════════════════════════════════════════
# Transient HTTP statuses worth retrying. 429 = rate limit / quota,
# 5xx = server overload or hiccup. Everything else (400/401/403/404) is a
# permanent error and is re-raised immediately.
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}

# Substrings that signal a transient failure when no numeric code is available.
_RETRYABLE_NEEDLES = (
    'rate limit', 'rate-limit', 'quota', 'resource exhausted', 'overload',
    'overloaded', 'unavailable', 'deadline', 'timed out', 'timeout',
    'try again', 'temporarily', 'connection', 'connection reset',
    'internal error', 'internal server', '429', '500', '502', '503', '504',
)


def _is_retryable_error(exc: Exception) -> bool:
    """Best-effort detection of transient errors across SDK versions."""
    # 1) Typed SDK errors (newer google-genai)
    if genai_errors is not None:
        if isinstance(exc, getattr(genai_errors, 'ServerError', ())):
            return True
        client_err = getattr(genai_errors, 'ClientError', None)
        if client_err and isinstance(exc, client_err):
            code = getattr(exc, 'code', None)
            return code in RETRYABLE_STATUS  # only 408/429 among 4xx

    # 2) Any object that carries a numeric status code
    for attr in ('code', 'status_code', 'http_status'):
        code = getattr(exc, attr, None)
        if isinstance(code, int) and code in RETRYABLE_STATUS:
            return True

    # 3) Fall back to message inspection
    msg = str(exc).lower()
    return any(n in msg for n in _RETRYABLE_NEEDLES)


def call_with_retry(fn, *, what: str = 'AI call',
                    max_attempts: int = 4, base_delay: float = 1.0):
    """Run `fn`, retrying transient failures with exponential backoff + jitter.

    Delays grow 1s → 2s → 4s (plus up to +30% random jitter to avoid
    thundering-herd). Permanent errors are raised on the first failure.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — we re-raise non-retryable below
            last_exc = exc
            if attempt >= max_attempts or not _is_retryable_error(exc):
                raise
            delay = base_delay * (2 ** (attempt - 1))
            delay += random.uniform(0, delay * 0.3)  # jitter
            logger.warning('%s failed (attempt %d/%d): %s — retrying in %.1fs',
                           what, attempt, max_attempts, exc, delay)
            time.sleep(delay)
    # Should be unreachable, but keeps the type checker happy.
    assert last_exc is not None
    raise last_exc


# ─────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────
def _connect() -> sqlite3.Connection:
    # timeout reduces "database is locked" under concurrent webhooks
    return sqlite3.connect(DB_FILE, timeout=10)


def _ensure_column(conn, table: str, column: str, col_def: str) -> None:
    """Adds a column to an existing table if it's missing.

    CREATE TABLE IF NOT EXISTS does NOT modify a table that already exists on
    the volume from an older schema, so columns added later (like user_id)
    never reach the old table. We add them here. The column is added as
    NULLABLE on purpose — SQLite cannot add a NOT NULL column to a table that
    already has rows; old rows simply won't match any user_id filter.
    """
    existing = [row[1] for row in conn.execute(f'PRAGMA table_info({table})')]
    if column not in existing:
        conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {col_def}')
        logger.info('Migration: added missing column %s.%s', table, column)


def init_db() -> None:
    db_dir = os.path.dirname(DB_FILE)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    with _connect() as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA busy_timeout=10000')
        c = conn.cursor()

        # 1) Create tables (safe on a fresh DB). Indexes are created separately
        #    in step 3, AFTER the migration, so they never run against an old
        #    table that is still missing a column.
        c.executescript('''
            CREATE TABLE IF NOT EXISTS budget (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                category    TEXT    NOT NULL,
                amount      REAL    NOT NULL,
                date        TEXT    NOT NULL,
                description TEXT,
                user_id     TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                quadrant    TEXT    NOT NULL,
                description TEXT    NOT NULL,
                created_at  TEXT    NOT NULL,
                completed   INTEGER NOT NULL DEFAULT 0,
                user_id     TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS contexts (
                user_id      TEXT PRIMARY KEY,
                context_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS budget_limits (
                category TEXT PRIMARY KEY,
                amount   REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS processed_messages (
                message_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            );
        ''')

        # 2) Migrate OLD databases on the Railway volume.
        #    THIS is what fixes "no such column: user_id": the tasks (and maybe
        #    budget) table was created by an earlier version without user_id,
        #    and CREATE TABLE IF NOT EXISTS left it untouched.
        _ensure_column(conn, 'budget', 'user_id', 'TEXT')
        _ensure_column(conn, 'tasks',  'user_id', 'TEXT')

        # 3) Indexes — only now that user_id is guaranteed to exist.
        c.executescript('''
            CREATE INDEX IF NOT EXISTS idx_budget_user_date
                ON budget (user_id, date);
            CREATE INDEX IF NOT EXISTS idx_tasks_user_completed
                ON tasks (user_id, completed);
        ''')

        # 4) Seed default limits only on first run.
        c.execute('SELECT COUNT(*) FROM budget_limits')
        if c.fetchone()[0] == 0:
            c.executemany(
                'INSERT INTO budget_limits (category, amount) VALUES (?, ?)',
                list(DEFAULT_BUDGET_LIMITS.items())
            )
        conn.commit()
    logger.info('Database initialised at %s', DB_FILE)


def _cleanup_processed_messages() -> None:
    """Drop dedup rows older than PROCESSED_TTL_DAYS (keeps the table small)."""
    cutoff = (now_local() - timedelta(days=PROCESSED_TTL_DAYS)).isoformat()
    try:
        with _connect() as conn:
            conn.execute('DELETE FROM processed_messages WHERE created_at < ?', (cutoff,))
            conn.commit()
    except Exception:
        logger.exception('processed_messages cleanup failed')


def mark_message_seen(message_id: str) -> bool:
    """Record a WhatsApp message id. Returns True if it is new (first time seen).

    WhatsApp delivers webhooks at-least-once, so the same message can arrive
    several times. We persist ids to avoid double-processing (e.g. logging the
    same expense twice).
    """
    if not message_id:
        return True
    try:
        with _connect() as conn:
            cur = conn.execute(
                'INSERT OR IGNORE INTO processed_messages (message_id, created_at) VALUES (?, ?)',
                (message_id, now_local().isoformat())
            )
            conn.commit()
        # Opportunistic, cheap, ~2% of the time — no extra scheduler needed.
        if random.random() < 0.02:
            _cleanup_processed_messages()
        return cur.rowcount > 0
    except Exception:
        logger.exception('Dedup check failed for %s', message_id)
        return True  # fail open: better a rare duplicate than a dropped message


# ── Context helpers ──────────────────────────

def get_user_context(user_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            'SELECT context_json FROM contexts WHERE user_id = ?', (user_id,)
        ).fetchone()
    return json.loads(row[0]) if row else None


def set_user_context(user_id: str, context: dict) -> None:
    with _connect() as conn:
        conn.execute(
            'INSERT OR REPLACE INTO contexts (user_id, context_json) VALUES (?, ?)',
            (user_id, json.dumps(context))
        )
        conn.commit()


def delete_user_context(user_id: str) -> None:
    with _connect() as conn:
        conn.execute('DELETE FROM contexts WHERE user_id = ?', (user_id,))
        conn.commit()


# ── Budget helpers ───────────────────────────

def get_budget_limit(category: str) -> float:
    with _connect() as conn:
        row = conn.execute(
            'SELECT amount FROM budget_limits WHERE category = ?', (category,)
        ).fetchone()
    return row[0] if row else 0.0


def set_budget_limit(category: str, amount: float) -> None:
    with _connect() as conn:
        conn.execute(
            'INSERT OR REPLACE INTO budget_limits (category, amount) VALUES (?, ?)',
            (category, amount)
        )
        conn.commit()


def get_all_budget_limits() -> dict:
    with _connect() as conn:
        rows = conn.execute('SELECT category, amount FROM budget_limits').fetchall()
    return {cat: amt for cat, amt in rows}


def add_expense(category: str, amount: float, date: str,
                description: str, user_id: str) -> None:
    # Store only the date portion (YYYY-MM-DD) so strftime queries work reliably
    date_only = date[:10]
    with _connect() as conn:
        conn.execute(
            'INSERT INTO budget (category, amount, date, description, user_id) VALUES (?, ?, ?, ?, ?)',
            (category, amount, date_only, description, user_id)
        )
        conn.commit()


def get_category_total_spent(category: str, user_id: str) -> float:
    current_month = now_local().strftime('%Y-%m')
    with _connect() as conn:
        row = conn.execute(
            """SELECT SUM(ABS(amount))
               FROM budget
               WHERE category = ?
                 AND amount < 0
                 AND user_id = ?
                 AND strftime('%Y-%m', date) = ?""",
            (category, user_id, current_month)
        ).fetchone()
    return row[0] if row[0] else 0.0


def get_all_budget_summary(user_id: str) -> list[tuple]:
    current_month = now_local().strftime('%Y-%m')
    with _connect() as conn:
        rows = conn.execute(
            """SELECT category, SUM(amount)
               FROM budget
               WHERE strftime('%Y-%m', date) = ?
                 AND user_id = ?
               GROUP BY category""",
            (current_month, user_id)
        ).fetchall()
    return rows


# ── Task helpers ─────────────────────────────

def add_task(quadrant: str, description: str, user_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            'INSERT INTO tasks (quadrant, description, created_at, completed, user_id) VALUES (?, ?, ?, 0, ?)',
            (quadrant, description, now_local().isoformat(), user_id)
        )
        conn.commit()


def get_active_tasks(user_id: str) -> list[tuple]:
    with _connect() as conn:
        rows = conn.execute(
            'SELECT id, quadrant, description FROM tasks WHERE completed = 0 AND user_id = ? ORDER BY id ASC',
            (user_id,)
        ).fetchall()
    return rows


def mark_task_completed(task_id: int, user_id: str) -> bool:
    with _connect() as conn:
        cursor = conn.execute(
            'UPDATE tasks SET completed = 1 WHERE id = ? AND user_id = ? AND completed = 0',
            (task_id, user_id)
        )
        conn.commit()
    return cursor.rowcount > 0


def delete_task_by_id(task_id: int, user_id: str) -> bool:
    with _connect() as conn:
        cursor = conn.execute(
            'DELETE FROM tasks WHERE id = ? AND user_id = ?',
            (task_id, user_id)
        )
        conn.commit()
    return cursor.rowcount > 0


def get_tasks_completion_stats(user_id: str) -> tuple[int, int]:
    """Completion stats for the CURRENT month only, so the ratio stays meaningful."""
    current_month = now_local().strftime('%Y-%m')
    with _connect() as conn:
        row = conn.execute(
            """SELECT SUM(completed), COUNT(*)
               FROM tasks
               WHERE user_id = ?
                 AND strftime('%Y-%m', created_at) = ?""",
            (user_id, current_month)
        ).fetchone()
    completed = row[0] if row[0] else 0
    total     = row[1] if row[1] else 0
    return completed, total


def find_tasks_by_text(query: str, user_id: str) -> list[tuple]:
    """Fuzzy-ish match: return active tasks whose description contains `query`
    (case-insensitive, whitespace-normalised). Used by complete/delete tools."""
    q = re.sub(r'\s+', ' ', (query or '').strip()).lower()
    active = get_active_tasks(user_id)
    if not q:
        return active
    matches = [(tid, quad, desc) for tid, quad, desc in active
               if q in re.sub(r'\s+', ' ', desc.strip()).lower()]
    return matches


# ─────────────────────────────────────────────
# Google Calendar
# ─────────────────────────────────────────────

def get_calendar_service():
    if not GOOGLE_CREDENTIALS:
        return None
    try:
        creds_dict  = json.loads(GOOGLE_CREDENTIALS)
        credentials = service_account.Credentials.from_service_account_info(
            creds_dict, scopes=['https://www.googleapis.com/auth/calendar']
        )
        # cache_discovery=False avoids a noisy warning + file cache on
        # read-only / serverless filesystems.
        return build('calendar', 'v3', credentials=credentials, cache_discovery=False)
    except Exception:
        logger.exception('Failed to build calendar service')
        return None


def _parse_event_datetime(start_time_iso: str) -> datetime | None:
    """Parse the AI-supplied start time and make it timezone-aware (Israel).

    The model returns naive ISO strings like '2026-06-01T09:00:00'. We attach
    LOCAL_TZ so the event lands at the intended wall-clock time regardless of
    the (UTC) server. If the model ever returns an offset, we respect it.
    """
    try:
        dt = datetime.fromisoformat(start_time_iso)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None and LOCAL_TZ is not None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt


def process_calendar_ai(title: str, start_time_iso: str, location: str | None) -> str:
    service = get_calendar_service()
    if not service:
        return 'שגיאת התחברות ליומן גוגל (בדוק Credentials).'

    start_time = _parse_event_datetime(start_time_iso)
    if start_time is None:
        return f'תאריך לא תקין: {start_time_iso}'

    # Guard against the model scheduling something that already passed (e.g. it
    # parsed "ב-8" as 08:00 when it's already 10:00). Warn, don't silently bury it.
    past_note = ''
    if start_time < now_local() - timedelta(minutes=1):
        past_note = '\n⚠️ שים לב: הזמן שביקשת כבר עבר — קבעתי בכל זאת. לשינוי כתוב לי תאריך חדש.'

    end_time = start_time + timedelta(hours=1)
    event: dict = {
        'summary': title,
        'start': {'dateTime': start_time.isoformat(), 'timeZone': TIMEZONE_NAME},
        'end':   {'dateTime': end_time.isoformat(),   'timeZone': TIMEZONE_NAME},
    }
    if location:
        event['location'] = location
    try:
        created = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        link    = created.get('htmlLink', 'לא זמין')
        weekday = HEB_WEEKDAYS[start_time.weekday()]
        return (
            f'האירוע נוצר בהצלחה! 📅\n'
            f'כותרת: {title}\n'
            f'יום {weekday}, {start_time.strftime("%d/%m/%Y בשעה %H:%M")}'
            + (f'\nמיקום: {location}' if location else '') +
            f'\nקישור: {link}'
            f'{past_note}'
        )
    except Exception:
        logger.exception('Calendar insert failed')
        return 'שגיאה ביצירת אירוע. ודא ששיתפת את היומן עם חשבון השירות.'


# ═════════════════════════════════════════════
# AI — Gemini Function / Tool Calling  (Doc §3)
# ═════════════════════════════════════════════
# Instead of asking the model for free-form JSON and parsing it (brittle), we
# declare strict, typed tools. The model returns one or more `function_call`
# parts with validated args, which we dispatch in `execute_tool`. This nearly
# eliminates "the bot didn't understand" failures and supports several actions
# in a single message (parallel function calling).

def _make_function_declaration(name: str, description: str, schema: dict):
    """Build a FunctionDeclaration compatibly across google-genai versions.

    Newer SDKs use `parameters_json_schema=`; older ones use `parameters=`.
    """
    try:
        return types.FunctionDeclaration(
            name=name, description=description, parameters_json_schema=schema
        )
    except TypeError:
        return types.FunctionDeclaration(
            name=name, description=description, parameters=schema
        )


def _build_tools() -> list:
    cats = ', '.join(VALID_CATEGORIES)
    quads = ', '.join(VALID_QUADRANTS)

    declarations = [
        _make_function_declaration(
            'add_expense',
            'רישום הוצאה או הכנסה כספית. השתמש בזה כשהמשתמש מדווח שקנה משהו, '
            'שילם, הוציא או הרוויח כסף. עבור הכנסה (משכורת, החזר, קיבל כסף) '
            'בחר את הקטגוריה "הכנסה".',
            {
                'type': 'object',
                'properties': {
                    'amount': {
                        'type': 'number',
                        'description': 'הסכום בשקלים, תמיד מספר חיובי.',
                    },
                    'category': {
                        'type': 'string',
                        'enum': VALID_CATEGORIES,
                        'description': f'הקטגוריה. אחת מתוך: {cats}.',
                    },
                    'description': {
                        'type': 'string',
                        'description': 'תיאור קצר של ההוצאה/ההכנסה (למשל "המבורגר", "משכורת").',
                    },
                },
                'required': ['amount', 'category'],
            },
        ),
        _make_function_declaration(
            'add_task',
            'הוספת משימה לרשימת המטלות לפי מטריצת אייזנהאואר. השתמש בזה כשהמשתמש '
            'מבקש להוסיף משימה, לזכור לעשות משהו, או לשים תזכורת למטלה.',
            {
                'type': 'object',
                'properties': {
                    'quadrant': {
                        'type': 'string',
                        'enum': VALID_QUADRANTS,
                        'description': (
                            f'רמת הדחיפות/חשיבות. אחת מתוך: {quads}. '
                            'אם המשתמש לא ציין במפורש, הסק לפי ההקשר '
                            '(ברירת מחדל סבירה: "חשוב לא דחוף").'
                        ),
                    },
                    'description': {
                        'type': 'string',
                        'description': 'תיאור המשימה.',
                    },
                },
                'required': ['quadrant', 'description'],
            },
        ),
        _make_function_declaration(
            'create_calendar_event',
            'יצירת אירוע ביומן גוגל. השתמש בזה כשהמשתמש מבקש לקבוע פגישה, '
            'תור, אירוע או תזכורת עם זמן מסוים.',
            {
                'type': 'object',
                'properties': {
                    'title': {
                        'type': 'string',
                        'description': 'כותרת האירוע (למשל "פגישה עם דני").',
                    },
                    'start_time': {
                        'type': 'string',
                        'description': (
                            'זמן ההתחלה בפורמט ISO 8601 ללא אזור זמן, '
                            'למשל "2026-06-01T09:00:00". חשב תאריכים יחסיים '
                            '("מחר", "יום שלישי", "עוד שעה") לפי הזמן הנוכחי שניתן לך.'
                        ),
                    },
                    'location': {
                        'type': 'string',
                        'description': 'מיקום האירוע, אם צוין. אחרת השמט.',
                    },
                },
                'required': ['title', 'start_time'],
            },
        ),
        _make_function_declaration(
            'complete_task',
            'סימון משימה קיימת כהושלמה. השתמש בזה כשהמשתמש אומר שסיים, ביצע '
            'או השלים מטלה כלשהי.',
            {
                'type': 'object',
                'properties': {
                    'task_query': {
                        'type': 'string',
                        'description': (
                            'תיאור או מילות מפתח של המשימה שהושלמה (למשל "חלב", '
                            '"להתקשר לרופא"). אם המשתמש לא פירט איזו, השאר ריק.'
                        ),
                    },
                },
                'required': [],
            },
        ),
        _make_function_declaration(
            'delete_task',
            'מחיקת משימה מהרשימה (לא סימון כהושלמה, אלא הסרה מוחלטת). השתמש בזה '
            'כשהמשתמש מבקש למחוק, להסיר או לבטל משימה.',
            {
                'type': 'object',
                'properties': {
                    'task_query': {
                        'type': 'string',
                        'description': 'תיאור או מילות מפתח של המשימה למחיקה.',
                    },
                },
                'required': [],
            },
        ),
        _make_function_declaration(
            'set_budget_limit',
            'הגדרה או עדכון של תקרת תקציב חודשית לקטגוריה.',
            {
                'type': 'object',
                'properties': {
                    'category': {
                        'type': 'string',
                        'enum': [c for c in VALID_CATEGORIES if c != 'הכנסה'],
                        'description': f'הקטגוריה. אחת מתוך: {cats} (לא כולל הכנסה).',
                    },
                    'amount': {
                        'type': 'number',
                        'description': 'תקרת התקציב החדשה בשקלים.',
                    },
                },
                'required': ['category', 'amount'],
            },
        ),
        _make_function_declaration(
            'get_budget_status',
            'הצגת דוח כספי מפורט לחודש הנוכחי (הכנסות, הוצאות, יתרה לכל קטגוריה). '
            'השתמש כשהמשתמש שואל על מצב כספי, מאזן, תקציב או כמה הוציא.',
            {'type': 'object', 'properties': {}, 'required': []},
        ),
        _make_function_declaration(
            'get_tasks_status',
            'הצגת רשימת כל המשימות הפתוחות. השתמש כשהמשתמש שואל מה יש לו לעשות, '
            'מבקש את רשימת המשימות או את הסטטוס שלהן.',
            {'type': 'object', 'properties': {}, 'required': []},
        ),
        _make_function_declaration(
            'show_help',
            'הצגת תפריט העזרה והפקודות הזמינות. השתמש כשהמשתמש שואל מה אתה יכול '
            'לעשות, מבקש עזרה או תפריט.',
            {'type': 'object', 'properties': {}, 'required': []},
        ),
    ]
    return [types.Tool(function_declarations=declarations)]


# Build once at import — declarations are static.
_TOOLS = None
def _get_tools():
    global _TOOLS
    if _TOOLS is None:
        _TOOLS = _build_tools()
    return _TOOLS


def _system_instruction() -> str:
    now = now_local()
    weekday = HEB_WEEKDAYS[now.weekday()]
    return (
        'אתה "סחבק", עוזר אישי חכם וידידותי בוואטסאפ שעוזר בניהול יומן, משימות '
        'ותקציב. אתה מדבר עברית, בקצרה ובחום.\n\n'
        f'הזמן הנוכחי: {now.strftime("%Y-%m-%d %H:%M")} (יום {weekday}).\n'
        'השתמש בזמן הזה כדי לחשב תאריכים יחסיים כמו "מחר", "מחרתיים", '
        '"יום ראשון הבא" או "עוד שעתיים".\n\n'
        'כשהמשתמש מבקש פעולה (הוצאה, משימה, אירוע, שאילתה) — קרא לכלי המתאים. '
        'מותר וכדאי לקרוא לכמה כלים בהודעה אחת אם המשתמש ביקש כמה דברים '
        '(למשל גם לקבוע פגישה וגם להוסיף משימה).\n'
        'אם המשתמש רק משוחח, שואל שאלה כללית או אומר שלום — ענה בטקסט קצר '
        'וחביב בלי לקרוא לכלי.'
    )


def get_ai_tool_calls(text: str) -> tuple[list[tuple[str, dict]], str]:
    """Send a free-form Hebrew message to Gemini.

    Returns (tool_calls, reply_text):
      • tool_calls — list of (function_name, args_dict). Empty for plain chat.
      • reply_text — the model's text answer when no tool was called.
    Raises on a final, non-recoverable API failure (caller shows a friendly msg).
    """
    client = get_genai_client()
    if not client:
        return [], 'מפתח Gemini חסר. הגדר GEMINI_API_KEY ב-Railway.'

    def _call():
        return client.models.generate_content(
            model=GEMINI_MODEL,
            contents=text,
            config=types.GenerateContentConfig(
                system_instruction=_system_instruction(),
                tools=_get_tools(),
                temperature=0.0,  # deterministic routing
            ),
        )

    response = call_with_retry(_call, what='Gemini (router)')

    # Preferred path: SDK exposes a convenient .function_calls property.
    calls: list[tuple[str, dict]] = []
    fcs = getattr(response, 'function_calls', None)
    if fcs:
        for fc in fcs:
            calls.append((fc.name, dict(fc.args or {})))
        return calls, ''

    # Fallback: walk the parts manually (older SDKs / edge cases).
    try:
        parts = response.candidates[0].content.parts or []
    except (AttributeError, IndexError, TypeError):
        parts = []
    text_chunks: list[str] = []
    for part in parts:
        fc = getattr(part, 'function_call', None)
        if fc and getattr(fc, 'name', None):
            calls.append((fc.name, dict(fc.args or {})))
        elif getattr(part, 'text', None):
            text_chunks.append(part.text)

    if calls:
        return calls, ''
    return [], ' '.join(c.strip() for c in text_chunks).strip()


# ── Tool dispatch ────────────────────────────

def _tool_add_expense(args: dict, user_id: str) -> str:
    try:
        amt = abs(float(args.get('amount', 0)))
    except (TypeError, ValueError):
        return 'לא הצלחתי להבין את הסכום. נסה שוב, למשל "הוצאתי 50 שקל על מזון".'
    if amt <= 0:
        return 'הסכום חייב להיות גדול מאפס. נסה שוב 🙂'
    cat  = (args.get('category') or '').strip()
    desc = (args.get('description') or '').strip() or cat
    if cat not in BUDGET_CATEGORIES_HE:
        return f'קטגוריה לא מוכרת: "{cat}".\nקטגוריות: {", ".join(VALID_CATEGORIES)}'

    signed_amt = amt if cat == 'הכנסה' else -amt
    add_expense(cat, signed_amt, now_local().isoformat(), desc, user_id)

    alert = ''
    if cat != 'הכנסה':
        limit = get_budget_limit(cat)
        if limit > 0:
            spent = get_category_total_spent(cat, user_id)
            rem   = limit - spent
            if rem < 0:
                alert = f'\n⚠️ חרגת ב-{abs(rem):,.0f} ש"ח מהתקציב של {cat}!'
            else:
                alert = f'\nנותרו {rem:,.0f} ש"ח בקטגוריה החודש'
    label = 'הכנסה' if cat == 'הכנסה' else 'הוצאה'
    return f'נרשם! {BUDGET_CATEGORIES_HE.get(cat, "💵")}\n*{cat}* ({label}): {amt:,.0f} ש"ח{alert}'


def _tool_add_task(args: dict, user_id: str) -> str:
    quad = (args.get('quadrant') or '').strip()
    desc = (args.get('description') or '').strip()
    if quad not in TASK_QUADRANTS_EMOJI:
        quad = 'חשוב לא דחוף'  # safe default rather than failing
    if not desc:
        return 'מה המשימה שתרצה להוסיף?'
    add_task(quad, desc, user_id)
    return f'משימה נוספה! ✅\n{TASK_QUADRANTS_EMOJI[quad]} *{quad}*\n{desc}'


def _tool_create_event(args: dict, user_id: str) -> str:
    title          = (args.get('title') or 'אירוע').strip()
    start_time_iso = args.get('start_time')
    if not start_time_iso:
        return 'חסר תאריך ושעה לאירוע. מתי לקבוע אותו?'
    location = (args.get('location') or '').strip() or None
    return process_calendar_ai(title, start_time_iso, location)


def _tool_complete_task(args: dict, user_id: str) -> str:
    query   = (args.get('task_query') or '').strip()
    matches = find_tasks_by_text(query, user_id)

    if not get_active_tasks(user_id):
        return 'אין משימות פתוחות לסיים! 🎉'

    # Exactly one match → complete it immediately.
    if query and len(matches) == 1:
        tid, _quad, desc = matches[0]
        if mark_task_completed(tid, user_id):
            preview = desc[:50] + ('…' if len(desc) > 50 else '')
            return f'מעולה! 🎉 סימנתי כהושלם:\n[{tid}] {preview}'
        return 'לא הצלחתי לסמן את המשימה. נסה "סטטוס משימות".'

    # Zero or many matches → ask which one, via the existing context flow.
    candidates = matches if matches else get_active_tasks(user_id)
    set_user_context(user_id, {'type': 'complete_task'})
    msg = '*איזו משימה סיימת? (שלח את המספר)*\n\n'
    for tid, quad, desc in candidates:
        preview = desc[:40] + ('…' if len(desc) > 40 else '')
        msg += f'{tid}. {TASK_QUADRANTS_EMOJI.get(quad, "📌")} {preview}\n'
    msg += '\n(או "ביטול")'
    return msg


def _tool_delete_task(args: dict, user_id: str) -> str:
    query   = (args.get('task_query') or '').strip()
    matches = find_tasks_by_text(query, user_id)

    if not get_active_tasks(user_id):
        return 'אין משימות פתוחות למחוק.'

    if query and len(matches) == 1:
        tid, _quad, desc = matches[0]
        if delete_task_by_id(tid, user_id):
            preview = desc[:50] + ('…' if len(desc) > 50 else '')
            return f'🗑️ נמחקה המשימה:\n[{tid}] {preview}'
        return 'לא הצלחתי למחוק את המשימה. נסה "סטטוס משימות".'

    candidates = matches if matches else get_active_tasks(user_id)
    set_user_context(user_id, {'type': 'delete_task'})
    msg = '*איזו משימה למחוק? (שלח את המספר)*\n\n'
    for tid, quad, desc in candidates:
        preview = desc[:40] + ('…' if len(desc) > 40 else '')
        msg += f'{tid}. {TASK_QUADRANTS_EMOJI.get(quad, "📌")} {preview}\n'
    msg += '\n(או "ביטול")'
    return msg


def _tool_set_limit(args: dict, user_id: str) -> str:
    cat = (args.get('category') or '').strip()
    try:
        amt = float(args.get('amount', 0))
    except (TypeError, ValueError):
        return 'הסכום לתקציב לא תקין.'
    if cat not in BUDGET_CATEGORIES_HE or cat == 'הכנסה':
        return f'לא ניתן להגדיר תקציב לקטגוריה "{cat}".'
    if amt <= 0:
        return 'תקרת התקציב חייבת להיות גדולה מאפס.'
    set_budget_limit(cat, amt)
    return f'✅ תקרת התקציב לקטגוריית *{cat}* עודכנה ל-{amt:,.0f} ש"ח.'


def execute_tool(name: str, args: dict, user_id: str) -> str:
    """Dispatch a single validated tool call to its handler."""
    handlers = {
        'add_expense':            _tool_add_expense,
        'add_task':               _tool_add_task,
        'create_calendar_event':  _tool_create_event,
        'complete_task':          _tool_complete_task,
        'delete_task':            _tool_delete_task,
        'set_budget_limit':       _tool_set_limit,
        'get_budget_status':      lambda a, u: get_detailed_budget(u),
        'get_tasks_status':       lambda a, u: get_task_status(u),
        'show_help':              lambda a, u: get_help_menu(),
    }
    handler = handlers.get(name)
    if not handler:
        logger.warning('Unknown tool requested: %s', name)
        return 'לא הצלחתי לבצע את הפעולה. נסה לנסח אחרת או כתוב "תפריט".'
    try:
        return handler(args, user_id)
    except Exception:
        logger.exception('Tool "%s" failed (args=%s)', name, args)
        return 'אופס, משהו השתבש בביצוע הפעולה. נסה שוב 🙏'


# ── Multimodal helpers (image / audio / document) ──

def describe_image_with_ai(image_data: bytes | None, mime_type: str, caption: str) -> str:
    client = get_genai_client()
    if not client or not image_data:
        return 'שלח הודעת טקסט כדי שאוכל לעזור לך 😊'
    contents = [
        'תאר את התמונה הזו בעברית בקצרה. אם יש בה טקסט, ציין אותו. היה ממוקד ומועיל.',
        types.Part.from_bytes(data=image_data, mime_type=mime_type or 'image/jpeg'),
    ]
    if caption:
        contents.append(f'הערת המשתמש: {caption}')
    try:
        response = call_with_retry(
            lambda: client.models.generate_content(model=GEMINI_MODEL, contents=contents),
            what='Gemini (image)',
        )
        out = (response.text or '').strip()
        return f'📷 *תיאור התמונה:*\n{out}' if out else 'לא הצלחתי לנתח את התמונה.'
    except Exception:
        logger.exception('Image description failed')
        return 'לא הצלחתי לעבד את התמונה. שלח הודעת טקסט ואעזור לך 😊'


def transcribe_audio_with_ai(audio_data: bytes | None, mime_type: str) -> str | None:
    client = get_genai_client()
    if not client or not audio_data:
        return None
    try:
        response = call_with_retry(
            lambda: client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[
                    'תמלל את ההקלטה הבאה לעברית. החזר אך ורק את הטקסט המתומלל, ללא הסברים.',
                    types.Part.from_bytes(data=audio_data, mime_type=mime_type or 'audio/ogg'),
                ],
            ),
            what='Gemini (audio)',
        )
        out = (response.text or '').strip()
        return out or None
    except Exception:
        logger.exception('Audio transcription failed')
        return None


def summarize_document_with_ai(doc_data: bytes | None, mime_type: str, filename: str) -> str:
    client = get_genai_client()
    name = filename or 'מסמך'
    if not client or not doc_data:
        return (f'📄 קיבלתי מסמך: *{name}*\nכרגע אני לא יכול לקרוא אותו. '
                'העתק את הטקסט ושלח אותו ישירות ואשמח לעזור.')
    can_read = mime_type.startswith('application/pdf') or mime_type.startswith('text/')
    if not can_read:
        return (f'📄 קיבלתי מסמך: *{name}* ({mime_type or "סוג לא ידוע"}).\n'
                'אני יכול לקרוא כרגע רק PDF או קובצי טקסט. '
                'העתק את הטקסט ושלח אותו ישירות ואשמח לעזור.')
    try:
        response = call_with_retry(
            lambda: client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[
                    'סכם בקצרה בעברית את תוכן המסמך הבא, בנקודות עיקריות וברורות.',
                    types.Part.from_bytes(data=doc_data, mime_type=mime_type),
                ],
            ),
            what='Gemini (document)',
        )
        out = (response.text or '').strip()
        return f'📄 *סיכום — {name}:*\n{out}' if out else f'לא הצלחתי לסכם את {name}.'
    except Exception:
        logger.exception('Document summary failed')
        return f'לא הצלחתי לקרוא את {name}. נסה לשלוח את הטקסט ישירות.'


# ─────────────────────────────────────────────
# WhatsApp API
# ─────────────────────────────────────────────

def _split_for_whatsapp(message: str, limit: int = WHATSAPP_TEXT_LIMIT) -> list[str]:
    """Split a long reply into <=limit chunks, preferring line boundaries so we
    never cut a word/emoji mid-way (which WhatsApp's hard 4096 cut would do)."""
    if len(message) <= limit:
        return [message]
    chunks: list[str] = []
    current = ''
    for line in message.split('\n'):
        # A single line longer than the limit must be hard-split.
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) + 1 > limit:
            if current:
                chunks.append(current)
            current = line
        else:
            current = f'{current}\n{line}' if current else line
    if current:
        chunks.append(current)
    return chunks


def _post_whatsapp(payload: dict) -> bool:
    url     = f'https://graph.facebook.com/{WHATSAPP_API_VERSION}/{PHONE_NUMBER_ID}/messages'
    headers = {
        'Authorization': f'Bearer {WHATSAPP_TOKEN}',
        'Content-Type':  'application/json',
    }

    def _do():
        resp = http_requests.post(url, headers=headers, json=payload, timeout=15)
        # Retry on Meta's transient/5xx; surface the body for everything else.
        if resp.status_code in RETRYABLE_STATUS:
            raise RuntimeError(f'WhatsApp transient {resp.status_code}: {resp.text[:300]}')
        if resp.status_code >= 400:
            # Log Meta's actual error body — this is what you need to debug
            # "the bot isn't replying" (expired token, number not allow-listed,
            # 24-hour window closed, etc.). Not retryable.
            logger.error('WhatsApp send failed (%s): %s', resp.status_code, resp.text[:500])
            return False
        return True

    try:
        return call_with_retry(_do, what='WhatsApp send', max_attempts=3)
    except Exception:
        logger.exception('Failed to send WhatsApp message')
        return False


def send_whatsapp_message(to: str, message: str) -> bool:
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        logger.error('WhatsApp credentials not configured (WHATSAPP_TOKEN / PHONE_NUMBER_ID)')
        return False
    ok = True
    for chunk in _split_for_whatsapp(message):
        payload = {
            'messaging_product': 'whatsapp',
            'to':                to,
            'type':              'text',
            'text':              {'body': chunk},
        }
        ok = _post_whatsapp(payload) and ok
    return ok


def download_whatsapp_media(media_id: str) -> tuple[bytes | None, str]:
    """Download media bytes from WhatsApp. Returns (data, mime_type)."""
    if not WHATSAPP_TOKEN:
        return None, ''
    headers = {'Authorization': f'Bearer {WHATSAPP_TOKEN}'}
    try:
        # Step 1: get media URL (retry transient failures)
        meta = call_with_retry(
            lambda: http_requests.get(
                f'https://graph.facebook.com/{WHATSAPP_API_VERSION}/{media_id}',
                headers=headers, timeout=15
            ),
            what='WhatsApp media meta', max_attempts=3,
        )
        meta.raise_for_status()
        meta_json = meta.json()
        media_url  = meta_json.get('url', '')
        mime_type  = meta_json.get('mime_type', 'application/octet-stream')
        if not media_url:
            return None, mime_type
        # Step 2: download actual bytes (the CDN URL also needs the auth header)
        media_resp = call_with_retry(
            lambda: http_requests.get(media_url, headers=headers, timeout=30),
            what='WhatsApp media download', max_attempts=3,
        )
        media_resp.raise_for_status()
        return media_resp.content, mime_type
    except Exception:
        logger.exception('Failed to download media %s', media_id)
        return None, ''


# ─────────────────────────────────────────────
# Security
# ─────────────────────────────────────────────

def verify_meta_signature(raw_body: bytes, signature_header: str | None) -> bool:
    if not APP_SECRET or not signature_header:
        return False
    mac      = hmac.new(APP_SECRET.encode('utf-8'), raw_body, hashlib.sha256)
    expected = 'sha256=' + mac.hexdigest()
    return hmac.compare_digest(expected, signature_header)


# ─────────────────────────────────────────────
# Message Processing
# ─────────────────────────────────────────────

def process_message(text: str, user_id: str) -> str:
    text = text.strip()

    # ── Multi-step context flow ──────────────
    context = get_user_context(user_id)
    if context:
        if text in ('ביטול', 'בטל'):
            delete_user_context(user_id)
            return 'הפעולה בוטלה ✅'

        ctype = context.get('type')
        if ctype in ('complete_task', 'delete_task'):
            match = re.search(r'(\d+)', text)
            if not match:
                action = 'שסיימת' if ctype == 'complete_task' else 'למחיקה'
                return f'שלח רק את המספר של המשימה {action} (או "ביטול").'
            task_id = int(match.group(1))
            if ctype == 'complete_task':
                ok = mark_task_completed(task_id, user_id)
                done_msg = f'מעולה! 🎉 משימה {task_id} סומנה כהושלמה.'
            else:
                ok = delete_task_by_id(task_id, user_id)
                done_msg = f'🗑️ משימה {task_id} נמחקה.'
            if ok:
                delete_user_context(user_id)
                return done_msg
            return 'לא מצאתי משימה עם המספר הזה. נסה שוב (או "ביטול").'

    if text in ('ביטול', 'בטל'):
        return 'אין פעולה פתוחה לביטול.'

    # ── A couple of instant, deterministic shortcuts (no AI latency) ──
    if text in ('תפריט', 'עזרה', 'help', 'menu'):
        return get_help_menu()

    # ── AI routing via Function Calling ──────
    try:
        tool_calls, reply_text = get_ai_tool_calls(text)
    except Exception:
        # All retries exhausted — transient AI outage. Be honest, stay calm.
        logger.exception('AI router permanently failed for user %s', user_id)
        return 'יש כרגע עומס זמני בשרתי ה-AI 🛠️ נסה שוב עוד רגע קצר.'

    if tool_calls:
        # Execute every requested action (supports several in one message) and
        # combine the confirmations into a single reply.
        results = [execute_tool(name, args, user_id) for name, args in tool_calls]
        return '\n\n'.join(r for r in results if r)

    if reply_text:
        return reply_text

    # Neither a tool nor text — extremely rare. Nudge gently.
    return 'לא הבנתי בדיוק 🤔 נסה לנסח אחרת, או כתוב "תפריט" לרשימת היכולות.'


def process_media_message(message: dict, user_id: str) -> str:
    """Handle non-text message types (image, audio, document)."""
    msg_type  = message.get('type', '')
    media_obj = message.get(msg_type, {}) or {}
    media_id  = media_obj.get('id', '')
    caption   = media_obj.get('caption', '') or ''

    media_data, mime_type = (None, '')
    if media_id:
        media_data, mime_type = download_whatsapp_media(media_id)

    # Guard against oversized media (Gemini inline-request limit)
    if media_data and len(media_data) > MAX_MEDIA_BYTES:
        return 'הקובץ גדול מדי לעיבוד (מעל ~18MB). שלח גרסה קטנה יותר או את הטקסט ישירות.'

    if msg_type == 'image':
        return describe_image_with_ai(media_data, mime_type, caption)

    if msg_type == 'audio':
        transcript = transcribe_audio_with_ai(media_data, mime_type)
        if not transcript:
            return ('🎤 קיבלתי הקלטה אך לא הצלחתי לתמלל אותה.\n'
                    'נסה שוב, או שלח את ההודעה כטקסט.')
        # Route the transcription through the normal text pipeline so a voice
        # note can create events, tasks and expenses exactly like typed text.
        result = process_message(transcript, user_id)
        return f'🎤 _שמעתי:_ "{transcript}"\n\n{result}'

    if msg_type == 'document':
        filename = media_obj.get('filename', '') or caption
        return summarize_document_with_ai(media_data, mime_type, filename)

    return 'סוג הודעה זה אינו נתמך עדיין. שלח טקסט, תמונה, הקלטה או קובץ.'


# ─────────────────────────────────────────────
# Response Builders
# ─────────────────────────────────────────────

def get_task_status(user_id: str) -> str:
    completed, total = get_tasks_completion_stats(user_id)
    active_tasks     = get_active_tasks(user_id)
    if not active_tasks:
        return f'אין משימות פתוחות 🎉\n{completed}/{total} משימות הושלמו החודש.'
    grouped: dict[str, list] = {q: [] for q in TASK_QUADRANTS_EMOJI}
    for tid, quad, desc in active_tasks:
        grouped.setdefault(quad, []).append((tid, desc))
    status = '*משימות פתוחות*\n\n'
    for quad, emoji in TASK_QUADRANTS_EMOJI.items():
        if grouped.get(quad):
            status += f'{emoji} *{quad}*\n'
            for tid, desc in grouped[quad]:
                preview = desc[:50] + ('…' if len(desc) > 50 else '')
                status += f'  [{tid}] {preview}\n'
            status += '\n'
    status += f'סה"כ: {len(active_tasks)} פתוחות | {completed}/{total} הושלמו החודש\n'
    status += '(לסיום: "סיימתי [שם המשימה]" · למחיקה: "מחק [שם המשימה]")'
    return status


def get_detailed_budget(user_id: str) -> str:
    summary = get_all_budget_summary(user_id)
    if not summary:
        return 'אין רשומות לחודש הנוכחי.'
    month   = now_local().strftime('%m/%Y')
    report  = f'*סטטוס כלכלי — {month}*\n\n'
    total_inc, total_exp = 0.0, 0.0

    for cat, total in summary:
        emoji = BUDGET_CATEGORIES_HE.get(cat, '💵')
        if cat == 'הכנסה':
            total_inc += total
            report += f'{emoji} *{cat}*: +{total:,.0f} ש"ח\n\n'
        else:
            spent = abs(total)
            total_exp += spent
            limit = get_budget_limit(cat)
            if limit > 0:
                perc   = min((spent / limit) * 100, 100)
                filled = min(int(perc / 10), 10)
                bar    = '█' * filled + '░' * (10 - filled)
                over   = ' ⚠️' if spent > limit else ''
                report += f'{emoji} *{cat}*: {spent:,.0f}/{limit:,.0f} ש"ח{over}\n{bar} {int(perc)}%\n\n'
            else:
                report += f'{emoji} *{cat}*: {spent:,.0f} ש"ח\n\n'

    bal = total_inc - total_exp
    report += '━━━━━━━━━━━━━━━\n'
    report += f'💰 הכנסות: {total_inc:,.0f} ש"ח\n'
    report += f'💸 הוצאות: {total_exp:,.0f} ש"ח\n'
    report += '━━━━━━━━━━━━━━━\n'
    if bal >= 0:
        report += f'✅ *מאזן חיובי*: +{bal:,.0f} ש"ח'
    else:
        report += f'⚠️ *גרעון*: {abs(bal):,.0f} ש"ח'
    return report


def get_welcome_message() -> str:
    return (
        'אהלן! אני *סחבק* — העוזר האישי שלך 🤖\n\n'
        'פשוט כתוב לי בחופשי:\n\n'
        '📅 *יומן:* "תקבע לי פגישה עם דני מחר ב-8"\n'
        '✅ *משימות:* "שים לי משימה דחופה לקנות חלב"\n'
        '💵 *תקציב:* "אכלתי המבורגר ב-70 שקל"\n\n'
        'אפשר גם לשלוח לי *הקלטה קולית*, *תמונה* או *קובץ PDF* 🎤📷📄\n\n'
        'לדוחות ועזרה שלח *"תפריט"*'
    )


def get_help_menu() -> str:
    return (
        '*תפריט עזרה — סחבק* 🤖\n\n'
        'דבר איתי חופשי, אני מבין שפה טבעית:\n'
        '• "קבע פגישה עם רופא השיניים ביום ראשון ב-10"\n'
        '• "תוסיף משימה חשובה להכין מצגת"\n'
        '• "שילמתי 250 על דלק"\n'
        '• "סיימתי את המשימה של החלב"\n\n'
        '*אפשר גם לבקש כמה דברים בהודעה אחת!*\n\n'
        '*פקודות מהירות:*\n'
        '• "סטטוס משימות" / "מה יש לי לעשות"\n'
        '• "סטטוס כלכלי" / "מאזן"\n'
        '• "הגדר תקציב מזון 3000"\n'
        '• "מחק משימה ..." · "ביטול"\n\n'
        '*קטגוריות תקציב:*\n'
        + '  '.join(f'{e} {c}' for c, e in BUDGET_CATEGORIES_HE.items()) +
        '\n\nאפשר גם הקלטה קולית 🎤, תמונה 📷 או PDF 📄\n'
        'אני כאן לעשות לך סדר! 💪'
    )


# ─────────────────────────────────────────────
# Webhook Routes
# ─────────────────────────────────────────────

def _handle_message_safely(message: dict, from_number: str) -> None:
    """Runs on the thread pool: build the reply and send it."""
    try:
        msg_type = message.get('type', '')
        if msg_type == 'text':
            text     = message.get('text', {}).get('body', '').strip()
            response = process_message(text, from_number) if text else get_welcome_message()
        elif msg_type in MEDIA_TYPES:
            response = process_media_message(message, from_number)
        else:
            response = 'סוג הודעה זה אינו נתמך עדיין. שלח טקסט, תמונה, הקלטה או קובץ.'
        send_whatsapp_message(from_number, response)
    except Exception:
        logger.exception('Failed to handle message from %s', from_number)
        try:
            send_whatsapp_message(from_number, 'אופס, משהו השתבש אצלי. נסה שוב עוד רגע 🙏')
        except Exception:
            logger.exception('Also failed to send error reply to %s', from_number)


@app.route('/webhook', methods=['GET'])
def verify_webhook():
    mode      = request.args.get('hub.mode')
    token     = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')
    if mode == 'subscribe' and token == VERIFY_TOKEN and challenge:
        logger.info('Webhook verified successfully')
        return challenge, 200
    logger.warning('Webhook verification failed (bad token or mode)')
    return 'Forbidden', 403


@app.route('/webhook', methods=['POST'])
def webhook():
    raw_body  = request.get_data()
    signature = request.headers.get('X-Hub-Signature-256')

    if APP_SECRET and not verify_meta_signature(raw_body, signature):
        logger.warning('Invalid webhook signature — request rejected')
        return jsonify({'error': 'invalid signature'}), 403

    data = request.get_json(silent=True)
    if not data:
        return jsonify({'status': 'ignored'}), 200

    try:
        changes  = data.get('entry', [{}])[0].get('changes', [{}])[0]
        value    = changes.get('value', {})
        messages = value.get('messages')
        if not messages:
            # Delivery/read receipts ("statuses") and other events land here
            return jsonify({'status': 'ignored'}), 200

        message     = messages[0]
        from_number = message.get('from', '')
        message_id  = message.get('id', '')

        if not from_number:
            return jsonify({'status': 'ignored'}), 200

        # Deduplicate — WhatsApp may deliver the same message more than once.
        if not mark_message_seen(message_id):
            logger.info('Duplicate message %s ignored', message_id)
            return jsonify({'status': 'duplicate'}), 200

        # Process on a bounded background pool so we ACK Meta within
        # milliseconds. This prevents webhook timeouts (and the automatic
        # re-delivery that follows) when Gemini / Calendar take a few seconds.
        _executor.submit(_handle_message_safely, message, from_number)

        return jsonify({'status': 'ok'}), 200

    except (IndexError, KeyError) as exc:
        logger.warning('Malformed webhook payload: %s', exc)
        return jsonify({'status': 'ignored'}), 200
    except Exception:
        logger.exception('Unhandled webhook error')
        # Still ACK with 200 so Meta does not retry in a loop.
        return jsonify({'status': 'error'}), 200


@app.route('/', methods=['GET'])
def index():
    return jsonify({'service': 'sahbak', 'status': 'running'}), 200


@app.route('/health', methods=['GET'])
def health():
    """Simple health-check endpoint for Railway / uptime monitors."""
    return jsonify({
        'status':    'ok',
        'timestamp': now_local().isoformat(),
        'model':     GEMINI_MODEL,
        'gemini':    bool(GEMINI_API_KEY),
        'whatsapp':  bool(WHATSAPP_TOKEN and PHONE_NUMBER_ID),
        'calendar':  bool(GOOGLE_CREDENTIALS),
    }), 200


# ─────────────────────────────────────────────
# Startup
# ─────────────────────────────────────────────

def _log_startup_config() -> None:
    logger.info('סחבק starting — model=%s, whatsapp_api=%s, workers=%d',
                GEMINI_MODEL, WHATSAPP_API_VERSION, MAX_WORKERS)
    missing = [name for name, val in {
        'WHATSAPP_TOKEN':  WHATSAPP_TOKEN,
        'PHONE_NUMBER_ID': PHONE_NUMBER_ID,
        'GEMINI_API_KEY':  GEMINI_API_KEY,
    }.items() if not val]
    if missing:
        logger.warning('Missing env vars (related features will be disabled): %s', ', '.join(missing))
    if not APP_SECRET:
        logger.warning('APP_SECRET not set — webhook signature verification is OFF')

# ═══════════════════════════════════════════════════════════════════════
# ██  Dashboard REST API  ██
# הוסף את הבלוק הזה ב-main.py, ממש לפני השורה:  if __name__ == '__main__':
# ═══════════════════════════════════════════════════════════════════════
#
# אבטחה: כל בקשה מה-Dashboard חייבת לשלוח Header:
#   X-Dashboard-Key: <הערך שהגדרת ב-Railway כ-DASHBOARD_API_KEY>
#
# ═══════════════════════════════════════════════════════════════════════

DASHBOARD_API_KEY = os.getenv('DASHBOARD_API_KEY', '')

def _require_dashboard_key():
    """מחזיר None אם המפתח תקין, response שגיאה אחרת."""
    if not DASHBOARD_API_K שEY:
        return jsonify({'error': 'DASHBOARD_API_KEY not configured on server'}), 503
    key = request.headers.get('X-Dashboard-Key', '')
    if not hmac.compare_digest(key, DASHBOARD_API_KEY):
        return jsonify({'error': 'unauthorized'}), 401
    return None


def _get_user_id():
    """user_id מה-query string או מה-JSON body."""
    return (request.args.get('user_id') or
            (request.get_json(silent=True) or {}).get('user_id', ''))


# ─── GET /api/dashboard?user_id=<PHONE> ─────────────────────────────────────
@app.route('/api/dashboard', methods=['GET'])
def api_dashboard():
    """מחזיר את כל הנתונים לדשבורד: תקציב, משימות, תנועות."""
    err = _require_dashboard_key()
    if err:
        return err
    user_id = _get_user_id()
    if not user_id:
        return jsonify({'error': 'user_id required'}), 400

    # תקציב חודש נוכחי
    budget_rows = get_all_budget_summary(user_id)
    limits      = get_all_budget_limits()
    budget = []
    for cat, total in budget_rows:
        budget.append({
            'category': cat,
            'emoji':    BUDGET_CATEGORIES_HE.get(cat, '💵'),
            'total':    round(total, 2),
            'limit':    limits.get(cat, 0),
        })

    # משימות פתוחות
    active = get_active_tasks(user_id)
    tasks  = [{'id': t[0], 'quadrant': t[1], 'description': t[2]} for t in active]

    # סטטיסטיקת השלמה (חודש נוכחי)
    completed, total_tasks = get_tasks_completion_stats(user_id)

    # תנועות אחרונות — 50 האחרונות בחודש הנוכחי
    current_month = now_local().strftime('%Y-%m')
    with _connect() as conn:
        rows = conn.execute(
            """SELECT id, category, amount, date, description
               FROM budget
               WHERE user_id = ? AND strftime('%Y-%m', date) = ?
               ORDER BY id DESC LIMIT 50""",
            (user_id, current_month)
        ).fetchall()
    transactions = [
        {'id': r[0], 'category': r[1], 'amount': r[2],
         'date': r[3], 'description': r[4]}
        for r in rows
    ]

    return jsonify({
        'user_id':         user_id,
        'month':           current_month,
        'budget_summary':  budget,
        'budget_limits':   limits,
        'tasks':           tasks,
        'tasks_completed': completed,
        'tasks_total':     total_tasks,
        'transactions':    transactions,
        'timestamp':       now_local().isoformat(),
    }), 200


# ─── POST /api/expense ───────────────────────────────────────────────────────
@app.route('/api/expense', methods=['POST'])
def api_add_expense():
    """הוסף הוצאה/הכנסה מהדשבורד — מסונכרן מיידית עם הבוט."""
    err = _require_dashboard_key()
    if err:
        return err
    data    = request.get_json(silent=True) or {}
    user_id = data.get('user_id', '')
    if not user_id:
        return jsonify({'error': 'user_id required'}), 400

    try:
        amount = float(data.get('amount', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'invalid amount'}), 400

    category    = (data.get('category') or '').strip()
    description = (data.get('description') or category).strip()

    if category not in BUDGET_CATEGORIES_HE:
        return jsonify({'error': f'invalid category: {category}',
                        'valid': VALID_CATEGORIES}), 400
    if amount <= 0:
        return jsonify({'error': 'amount must be positive'}), 400

    signed = amount if category == 'הכנסה' else -amount
    add_expense(category, signed, now_local().isoformat(), description, user_id)
    return jsonify({'status': 'ok', 'category': category, 'amount': signed}), 201


# ─── DELETE /api/expense/<id>?user_id=<PHONE> ───────────────────────────────
@app.route('/api/expense/<int:expense_id>', methods=['DELETE'])
def api_delete_expense(expense_id):
    """מחיקת תנועה לפי ID."""
    err = _require_dashboard_key()
    if err:
        return err
    user_id = _get_user_id()
    if not user_id:
        return jsonify({'error': 'user_id required'}), 400
    with _connect() as conn:
        cur = conn.execute(
            'DELETE FROM budget WHERE id = ? AND user_id = ?',
            (expense_id, user_id)
        )
        conn.commit()
    if cur.rowcount == 0:
        return jsonify({'error': 'expense not found'}), 404
    return jsonify({'status': 'ok', 'expense_id': expense_id}), 200


# ─── POST /api/tasks ─────────────────────────────────────────────────────────
@app.route('/api/tasks', methods=['POST'])
def api_add_task():
    """הוסף משימה מהדשבורד — מסונכרן מיידית עם הבוט."""
    err = _require_dashboard_key()
    if err:
        return err
    data    = request.get_json(silent=True) or {}
    user_id = data.get('user_id', '')
    quad    = (data.get('quadrant') or 'חשוב לא דחוף').strip()
    desc    = (data.get('description') or '').strip()
    if not user_id:
        return jsonify({'error': 'user_id required'}), 400
    if not desc:
        return jsonify({'error': 'description required'}), 400
    if quad not in TASK_QUADRANTS_EMOJI:
        quad = 'חשוב לא דחוף'
    add_task(quad, desc, user_id)
    return jsonify({'status': 'ok', 'quadrant': quad, 'description': desc}), 201


# ─── PATCH /api/tasks/<id>/complete?user_id=<PHONE> ─────────────────────────
@app.route('/api/tasks/<int:task_id>/complete', methods=['PATCH'])
def api_complete_task(task_id):
    """סמן משימה כהושלמה מהדשבורד."""
    err = _require_dashboard_key()
    if err:
        return err
    user_id = _get_user_id()
    if not user_id:
        return jsonify({'error': 'user_id required'}), 400
    ok = mark_task_completed(task_id, user_id)
    if not ok:
        return jsonify({'error': 'task not found or already completed'}), 404
    return jsonify({'status': 'ok', 'task_id': task_id}), 200


# ─── DELETE /api/tasks/<id>?user_id=<PHONE> ─────────────────────────────────
@app.route('/api/tasks/<int:task_id>', methods=['DELETE'])
def api_delete_task_route(task_id):
    """מחק משימה מהדשבורד."""
    err = _require_dashboard_key()
    if err:
        return err
    user_id = _get_user_id()
    if not user_id:
        return jsonify({'error': 'user_id required'}), 400
    ok = delete_task_by_id(task_id, user_id)
    if not ok:
        return jsonify({'error': 'task not found'}), 404
    return jsonify({'status': 'ok', 'task_id': task_id}), 200


# ─── PUT /api/budget-limits ──────────────────────────────────────────────────
@app.route('/api/budget-limits', methods=['PUT'])
def api_set_budget_limits():
    """עדכן מגבלות תקציב מהדשבורד. Body: { "מזון": 3000, "רכב": 2000, ... }"""
    err = _require_dashboard_key()
    if err:
        return err
    data    = request.get_json(silent=True) or {}
    updated = []
    for cat, amt in data.items():
        if cat in BUDGET_CATEGORIES_HE and cat != 'הכנסה':
            try:
                set_budget_limit(cat, float(amt))
                updated.append(cat)
            except (TypeError, ValueError):
                pass
    return jsonify({'status': 'ok', 'updated': updated}), 200

# ═══════════════════════════════════════════════════════════════════════
# סוף בלוק ה-Dashboard API  — המשך if __name__ == '__main__': אחרי זה
# ═══════════════════════════════════════════════════════════════════════

# Run at import time so it also executes under gunicorn (production).
init_db()
_log_startup_config()


# ─────────────────────────────────────────────
# Entry Point (local development only)
# ─────────────────────────────────────────────
if __name__ == '__main__':
    debug = os.getenv('FLASK_DEBUG', 'false').lower() in ('true', '1')
    port  = int(os.getenv('PORT', '5000'))
    app.run(host='0.0.0.0', port=port, debug=debug)

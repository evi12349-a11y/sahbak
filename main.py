"""
סחבק — WhatsApp Personal Assistant Bot
Production-grade Flask server for Railway deployment.
"""

import os
import json
import re
import hmac
import hashlib
import sqlite3
import logging
import base64
from datetime import datetime, timedelta

import requests as http_requests
from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
import google.generativeai as genai

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

VERIFY_TOKEN     = os.getenv('VERIFY_TOKEN', 'sahbak-verify-2026')
WHATSAPP_TOKEN   = os.getenv('WHATSAPP_TOKEN')
PHONE_NUMBER_ID  = os.getenv('PHONE_NUMBER_ID')
GOOGLE_CREDENTIALS = os.getenv('GOOGLE_CREDENTIALS')
CALENDAR_ID      = os.getenv('CALENDAR_ID', 'primary')
APP_SECRET       = os.getenv('APP_SECRET')
GEMINI_API_KEY   = os.getenv('GEMINI_API_KEY')

DB_FILE = '/app/data/sahbak.db'

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────
BUDGET_CATEGORIES_HE = {
    'דיור': '🏠', 'רכב': '🚗', 'נופש': '✈️',
    'מזון': '🍔', 'בריאות': '💊', 'חינוך': '📚',
    'בילויים': '🎉', 'קניות': '🛒', 'הכנסה': '💰',
}

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

SUPPORTED_IMAGE_TYPES  = {'image'}
SUPPORTED_AUDIO_TYPES  = {'audio'}
SUPPORTED_DOC_TYPES    = {'document'}
MEDIA_TYPES            = SUPPORTED_IMAGE_TYPES | SUPPORTED_AUDIO_TYPES | SUPPORTED_DOC_TYPES

# ─────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────
def init_db() -> None:
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
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
        ''')
        # Seed default limits only on first run
        c.execute('SELECT COUNT(*) FROM budget_limits')
        if c.fetchone()[0] == 0:
            c.executemany(
                'INSERT INTO budget_limits (category, amount) VALUES (?, ?)',
                DEFAULT_BUDGET_LIMITS.items()
            )
        conn.commit()
    logger.info('Database initialised at %s', DB_FILE)


init_db()


# ── Context helpers ──────────────────────────

def get_user_context(user_id: str) -> dict | None:
    with sqlite3.connect(DB_FILE) as conn:
        row = conn.execute(
            'SELECT context_json FROM contexts WHERE user_id = ?', (user_id,)
        ).fetchone()
    return json.loads(row[0]) if row else None


def set_user_context(user_id: str, context: dict) -> None:
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            'INSERT OR REPLACE INTO contexts (user_id, context_json) VALUES (?, ?)',
            (user_id, json.dumps(context))
        )
        conn.commit()


def delete_user_context(user_id: str) -> None:
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('DELETE FROM contexts WHERE user_id = ?', (user_id,))
        conn.commit()


# ── Budget helpers ───────────────────────────

def get_budget_limit(category: str) -> float:
    with sqlite3.connect(DB_FILE) as conn:
        row = conn.execute(
            'SELECT amount FROM budget_limits WHERE category = ?', (category,)
        ).fetchone()
    return row[0] if row else 0.0


def set_budget_limit(category: str, amount: float) -> None:
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            'INSERT OR REPLACE INTO budget_limits (category, amount) VALUES (?, ?)',
            (category, amount)
        )
        conn.commit()


def add_expense(category: str, amount: float, date: str,
                description: str, user_id: str) -> None:
    # Store only the date portion (YYYY-MM-DD) so strftime queries work reliably
    date_only = date[:10]
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            'INSERT INTO budget (category, amount, date, description, user_id) VALUES (?, ?, ?, ?, ?)',
            (category, amount, date_only, description, user_id)
        )
        conn.commit()


def get_category_total_spent(category: str, user_id: str) -> float:
    current_month = datetime.now().strftime('%Y-%m')
    with sqlite3.connect(DB_FILE) as conn:
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
    current_month = datetime.now().strftime('%Y-%m')
    with sqlite3.connect(DB_FILE) as conn:
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
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            'INSERT INTO tasks (quadrant, description, created_at, completed, user_id) VALUES (?, ?, ?, 0, ?)',
            (quadrant, description, datetime.now().isoformat(), user_id)
        )
        conn.commit()


def get_active_tasks(user_id: str) -> list[tuple]:
    with sqlite3.connect(DB_FILE) as conn:
        rows = conn.execute(
            'SELECT id, quadrant, description FROM tasks WHERE completed = 0 AND user_id = ? ORDER BY id ASC',
            (user_id,)
        ).fetchall()
    return rows


def mark_task_completed(task_id: int, user_id: str) -> bool:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.execute(
            'UPDATE tasks SET completed = 1 WHERE id = ? AND user_id = ? AND completed = 0',
            (task_id, user_id)
        )
        conn.commit()
    return cursor.rowcount > 0


def get_tasks_completion_stats(user_id: str) -> tuple[int, int]:
    with sqlite3.connect(DB_FILE) as conn:
        row = conn.execute(
            'SELECT SUM(completed), COUNT(*) FROM tasks WHERE user_id = ?',
            (user_id,)
        ).fetchone()
    completed = row[0] if row[0] else 0
    total     = row[1] if row[1] else 0
    return completed, total


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
        return build('calendar', 'v3', credentials=credentials)
    except Exception:
        logger.exception('Failed to build calendar service')
        return None


def process_calendar_ai(title: str, start_time_iso: str, location: str | None) -> str:
    service = get_calendar_service()
    if not service:
        return 'שגיאת התחברות ליומן גוגל (בדוק Credentials).'
    try:
        start_time = datetime.fromisoformat(start_time_iso)
    except ValueError:
        return f'תאריך לא תקין: {start_time_iso}'
    end_time = start_time + timedelta(hours=1)
    event: dict = {
        'summary': title,
        'start': {'dateTime': start_time.isoformat(), 'timeZone': 'Asia/Jerusalem'},
        'end':   {'dateTime': end_time.isoformat(),   'timeZone': 'Asia/Jerusalem'},
    }
    if location:
        event['location'] = location
    try:
        created = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        link    = created.get('htmlLink', 'לא זמין')
        return (
            f'האירוע נוצר בהצלחה! 📅\n'
            f'כותרת: {title}\n'
            f'זמן: {start_time.strftime("%d/%m/%Y %H:%M")}\n'
            f'קישור: {link}'
        )
    except Exception:
        logger.exception('Calendar insert failed')
        return 'שגיאה ביצירת אירוע. ודא ששיתפת את היומן עם חשבון השירות.'


# ─────────────────────────────────────────────
# AI — Gemini
# ─────────────────────────────────────────────

_GEMINI_MODEL = 'gemini-2.0-flash'

def analyze_with_ai(text: str) -> dict:
    """Parse a free-form Hebrew message into a structured action dict."""
    if not GEMINI_API_KEY:
        return {'action': 'unknown', 'reply': 'מפתח Gemini חסר. הגדר GEMINI_API_KEY ב-Railway.'}
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    prompt = f"""
אתה עוזר אישי חכם בוואטסאפ שנקרא "סחבק". תפקידך לנתח משפטים חופשיים של משתמש ולהמיר אותם לפעולות במערכת.
תאריך ושעה נוכחיים: {now_str}

נתח את המשפט הבא: "{text}"

החזר אך ורק אובייקט JSON טהור ללא טקסט נוסף, ללא בקשת json וללא ```.

התבניות האפשריות:

1. {{"action": "expense", "amount": 100, "category": "מזון", "description": "תיאור"}}
   קטגוריות חוקיות בלבד: דיור, רכב, נופש, מזון, בריאות, חינוך, בילויים, קניות, הכנסה

2. {{"action": "task", "quadrant": "חשוב דחוף", "description": "מה לעשות"}}
   ערכי quadrant חוקיים בלבד: חשוב דחוף, חשוב לא דחוף, דחוף לא חשוב, לא דחוף לא חשוב

3. {{"action": "calendar", "title": "נושא", "start_time": "2026-06-01T09:00:00", "location": null}}

4. {{"action": "unknown", "reply": "תשובה קצרה וידידותית בעברית"}}
"""
    try:
        model    = genai.GenerativeModel(_GEMINI_MODEL)
        response = model.generate_content(prompt)
        raw      = response.text.strip()
        # Strip any accidental markdown fences
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw).strip()
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning('AI returned non-JSON: %s', raw[:200])
        return {'action': 'unknown', 'reply': 'סליחה, לא הצלחתי להבין. נסח אחרת?'}
    except Exception:
        logger.exception('Gemini API error')
        return {'action': 'unknown', 'reply': 'שגיאה זמנית בשרתי AI. נסה שוב עוד רגע.'}


def describe_media_with_ai(media_type: str, mime_type: str, media_data: bytes | None,
                            caption: str) -> str:
    """Use Gemini to describe/transcribe non-text media content."""
    if not GEMINI_API_KEY:
        return 'שלח הודעת טקסט כדי שאוכל לעזור לך 😊'

    try:
        model = genai.GenerativeModel(_GEMINI_MODEL)

        if media_type == 'image' and media_data:
            image_part = {'mime_type': mime_type, 'data': base64.b64encode(media_data).decode()}
            prompt_parts = [
                image_part,
                'תאר את התמונה הזו בעברית בקצרה. אם יש בה טקסט, ציין אותו. היה ממוקד ומועיל.'
            ]
            if caption:
                prompt_parts.append(f'הערת המשתמש: {caption}')
            response = model.generate_content(prompt_parts)
            return f'📷 *תיאור התמונה:*\n{response.text.strip()}'

        if media_type == 'audio':
            return (
                '🎤 קיבלתי הודעה קולית, אך עדיין לא מסוגל לתמלל שמע.\n'
                'שלח את ההודעה כטקסט ואשמח לעזור!'
            )

        if media_type == 'document':
            doc_name = caption or 'מסמך'
            return (
                f'📄 קיבלתי מסמך: *{doc_name}*\n'
                'כרגע אני לא יכול לקרוא קבצים מצורפים.\n'
                'אם תרצה עזרה עם תוכנו — העתק את הטקסט ושלח אותו ישירות.'
            )

    except Exception:
        logger.exception('Media description failed')

    return 'לא הצלחתי לעבד את הקובץ. שלח הודעת טקסט ואעזור לך 😊'


# ─────────────────────────────────────────────
# WhatsApp API
# ─────────────────────────────────────────────

def send_whatsapp_message(to: str, message: str) -> bool:
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        logger.error('WhatsApp credentials not configured')
        return False
    url     = f'https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages'
    headers = {
        'Authorization': f'Bearer {WHATSAPP_TOKEN}',
        'Content-Type':  'application/json',
    }
    payload = {
        'messaging_product': 'whatsapp',
        'to':                to,
        'type':              'text',
        'text':              {'body': message},
    }
    try:
        resp = http_requests.post(url, headers=headers, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception:
        logger.exception('Failed to send WhatsApp message to %s', to)
        return False


def download_whatsapp_media(media_id: str) -> tuple[bytes | None, str]:
    """Download media bytes from WhatsApp. Returns (data, mime_type)."""
    if not WHATSAPP_TOKEN:
        return None, ''
    headers = {'Authorization': f'Bearer {WHATSAPP_TOKEN}'}
    try:
        # Step 1: get media URL
        meta = http_requests.get(
            f'https://graph.facebook.com/v19.0/{media_id}',
            headers=headers, timeout=10
        )
        meta.raise_for_status()
        meta_json = meta.json()
        media_url  = meta_json.get('url', '')
        mime_type  = meta_json.get('mime_type', 'application/octet-stream')
        if not media_url:
            return None, mime_type
        # Step 2: download actual bytes
        media_resp = http_requests.get(media_url, headers=headers, timeout=30)
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

        if context['type'] == 'complete_task':
            match = re.search(r'(\d+)', text)
            if not match:
                return 'שלח רק את המספר של המשימה שסיימת (או "ביטול").'
            task_id = int(match.group(1))
            if mark_task_completed(task_id, user_id):
                delete_user_context(user_id)
                return f'מעולה! 🎉 משימה {task_id} סומנה כהושלמה.'
            return 'לא מצאתי משימה פתוחה עם המספר הזה. נסה שוב (או "ביטול").'

    if text in ('ביטול', 'בטל'):
        return 'אין פעולה פתוחה לביטול.'

    # ── Quick commands ───────────────────────

    # Set budget limit: "הגדר תקציב מזון 3000"
    limit_match = re.search(r'הגדר\s*תקציב\s*([א-ת\s]+?)\s+(\d+)', text)
    if limit_match:
        cat = limit_match.group(1).strip()
        amt = int(limit_match.group(2))
        if cat in BUDGET_CATEGORIES_HE:
            set_budget_limit(cat, amt)
            return f'✅ תקרת התקציב לקטגוריית *{cat}* עודכנה ל-{amt:,} ש"ח.'
        return f'לא מצאתי קטגוריה בשם "{cat}".\nקטגוריות: {", ".join(BUDGET_CATEGORIES_HE)}'

    # Complete task flow
    if re.search(r'(סיימתי|בוצע|הושלם)\s*(משימה)?', text):
        active_tasks = get_active_tasks(user_id)
        if not active_tasks:
            return 'אין משימות פתוחות לסיים! 🎉'
        msg = '*איזו משימה סיימת? (שלח מספר)*\n\n'
        for task_id, quad, desc in active_tasks:
            preview = desc[:40] + ('…' if len(desc) > 40 else '')
            msg += f'{task_id}. {TASK_QUADRANTS_EMOJI.get(quad, "📌")} {preview}\n'
        set_user_context(user_id, {'type': 'complete_task'})
        return msg

    if any(kw in text for kw in ('סטטוס משימות', 'רשימת משימות')):
        return get_task_status(user_id)
    if any(kw in text for kw in ('סטטוס כלכלי', 'מאזן', 'תקציב')):
        return get_detailed_budget(user_id)
    if any(kw in text for kw in ('עזרה', 'תפריט')):
        return get_help_menu()

    # ── AI routing ───────────────────────────
    ai_result = analyze_with_ai(text)

    action = ai_result.get('action')

    if action == 'expense':
        amt  = float(ai_result.get('amount', 0))
        cat  = ai_result.get('category', '')
        desc = ai_result.get('description') or text
        if cat not in BUDGET_CATEGORIES_HE:
            return f'קטגוריה לא מוכרת: "{cat}".\nקטגוריות: {", ".join(BUDGET_CATEGORIES_HE)}'
        signed_amt = amt if cat == 'הכנסה' else -amt
        add_expense(cat, signed_amt, datetime.now().isoformat(), desc, user_id)
        alert = ''
        if cat != 'הכנסה':
            limit = get_budget_limit(cat)
            if limit > 0:
                spent = get_category_total_spent(cat, user_id)
                rem   = limit - spent
                alert = f'\n⚠️ חרגת ב-{abs(rem):,.0f} ש"ח!' if rem < 0 else f'\nנותרו {rem:,.0f} ש"ח החודש'
        return f'נרשם! {BUDGET_CATEGORIES_HE.get(cat, "💵")}\n*{cat}*: {amt:,.0f} ש"ח{alert}'

    if action == 'task':
        quad = ai_result.get('quadrant', '')
        desc = ai_result.get('description') or text
        if quad not in TASK_QUADRANTS_EMOJI:
            return f'סוג משימה לא חוקי: "{quad}".'
        add_task(quad, desc, user_id)
        return f'משימה נוספה! ✅\n{TASK_QUADRANTS_EMOJI[quad]} *{quad}*\n{desc}'

    if action == 'calendar':
        title          = ai_result.get('title') or 'אירוע'
        start_time_iso = ai_result.get('start_time')
        if not start_time_iso:
            return 'חסר תאריך ושעה לאירוע.'
        return process_calendar_ai(title, start_time_iso, ai_result.get('location'))

    if action == 'unknown':
        return ai_result.get('reply') or 'לא הבנתי. כתוב "תפריט" לרשימת הפקודות.'

    # Fallback — should never reach here if AI behaves
    logger.warning('Unexpected AI action "%s" for user %s', action, user_id)
    return 'לא הצלחתי לעבד את הבקשה. נסה לנסח אחרת, או כתוב "תפריט".'


def process_media_message(message: dict, user_id: str) -> str:
    """Handle non-text message types (image, audio, document)."""
    msg_type = message.get('type', '')
    media_obj = message.get(msg_type, {})
    media_id  = media_obj.get('id', '')
    caption   = media_obj.get('caption', '') or ''

    media_data, mime_type = None, ''
    if msg_type == 'image' and media_id:
        media_data, mime_type = download_whatsapp_media(media_id)

    return describe_media_with_ai(msg_type, mime_type, media_data, caption)


# ─────────────────────────────────────────────
# Response Builders
# ─────────────────────────────────────────────

def get_task_status(user_id: str) -> str:
    completed, total = get_tasks_completion_stats(user_id)
    active_tasks     = get_active_tasks(user_id)
    if not active_tasks:
        return f'אין משימות פתוחות 🎉\n{completed}/{total} משימות הושלמו.'
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
    status += f'סה"כ: {len(active_tasks)} פתוחות | {completed}/{total} הושלמו\n'
    status += '(לסיום: שלח "סיימתי משימה")'
    return status


def get_detailed_budget(user_id: str) -> str:
    summary = get_all_budget_summary(user_id)
    if not summary:
        return 'אין רשומות לחודש הנוכחי.'
    month   = datetime.now().strftime('%m/%Y')
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
        'לדוחות ועזרה שלח *"תפריט"*'
    )


def get_help_menu() -> str:
    return (
        '*תפריט עזרה — סחבק* 🤖\n\n'
        '*פקודות מהירות:*\n'
        '• "סטטוס משימות"\n'
        '• "סיימתי משימה"\n'
        '• "סטטוס כלכלי"\n'
        '• "הגדר תקציב [קטגוריה] [סכום]"\n\n'
        '*קטגוריות תקציב:*\n'
        + '  '.join(f'{e} {c}' for c, e in BUDGET_CATEGORIES_HE.items()) +
        '\n\nאני כאן לעשות לך סדר! 💪'
    )


# ─────────────────────────────────────────────
# Webhook Routes
# ─────────────────────────────────────────────

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
        changes = data.get('entry', [{}])[0].get('changes', [{}])[0]
        value   = changes.get('value', {})
        if 'messages' not in value:
            return jsonify({'status': 'ignored'}), 200

        message     = value['messages'][0]
        from_number = message.get('from', '')
        msg_type    = message.get('type', '')

        if not from_number:
            return jsonify({'status': 'ignored'}), 200

        if msg_type == 'text':
            text     = message.get('text', {}).get('body', '').strip()
            response = process_message(text, from_number) if text else get_welcome_message()
        elif msg_type in MEDIA_TYPES:
            response = process_media_message(message, from_number)
        else:
            response = 'סוג הודעה זה אינו נתמך עדיין. שלח טקסט, תמונה, או קובץ.'

        send_whatsapp_message(from_number, response)
        return jsonify({'status': 'ok'}), 200

    except (IndexError, KeyError) as exc:
        logger.warning('Malformed webhook payload: %s', exc)
        return jsonify({'status': 'ignored'}), 200
    except Exception:
        logger.exception('Unhandled webhook error')
        return jsonify({'status': 'error'}), 500


@app.route('/health', methods=['GET'])
def health():
    """Simple health-check endpoint for Railway / uptime monitors."""
    return jsonify({
        'status':    'ok',
        'timestamp': datetime.now().isoformat(),
        'gemini':    bool(GEMINI_API_KEY),
        'whatsapp':  bool(WHATSAPP_TOKEN and PHONE_NUMBER_ID),
        'calendar':  bool(GOOGLE_CREDENTIALS),
    }), 200


# ─────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────
if __name__ == '__main__':
    debug = os.getenv('FLASK_DEBUG', 'false').lower() in ('true', '1')
    port  = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=debug)

from flask import Flask, request, jsonify
import os
import json
import re
import requests as http_requests
from datetime import datetime, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build
import hmac
import hashlib
import sqlite3

app = Flask(__name__)

# Environment variables
VERIFY_TOKEN = os.getenv('VERIFY_TOKEN', 'sahbak-verify-2026')
WHATSAPP_TOKEN = os.getenv('WHATSAPP_TOKEN')
PHONE_NUMBER_ID = os.getenv('PHONE_NUMBER_ID')
GOOGLE_CREDENTIALS = os.getenv('GOOGLE_CREDENTIALS')
CALENDAR_ID = os.getenv('CALENDAR_ID', 'primary')
APP_SECRET = os.getenv('APP_SECRET')

# DB File
DB_FILE = '/app/data/sahbak.db'

# Categories & Task Mapping
BUDGET_CATEGORIES = {
    'דיור': '🏠',
    'רכב': '🚗',
    'נופש': '✈️',
    'מזון': '🍔',
    'בריאות': '💊',
    'חינוך': '📚',
    'בילויים': '🎉',
    'קניות': '🛒',
    'הכנסה': '💰'
}

DEFAULT_BUDGET_LIMITS = {
    'דיור': 5000,
    'רכב': 2000,
    'נופש': 1500,
    'מזון': 3000,
    'בריאות': 1000,
    'חינוך': 1500,
    'בילויים': 800,
    'קניות': 1000
}

TASK_COLOR_MAP = {
    'אדום': 'חשוב דחוף',
    'צהוב': 'חשוב לא דחוף',
    'כתום': 'דחוף לא חשוב',
    'ירוק': 'לא דחוף לא חשוב'
}

TASK_QUADRANTS_EMOJI = {
    'חשוב דחוף': '🔴',
    'חשוב לא דחוף': '🟡',
    'דחוף לא חשוב': '🟠',
    'לא דחוף לא חשוב': '🟢'
}


def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS budget (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            amount REAL,
            date TEXT,
            description TEXT,
            user_id TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quadrant TEXT,
            description TEXT,
            created_at TEXT,
            completed INTEGER DEFAULT 0
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS contexts (
            user_id TEXT PRIMARY KEY,
            context_json TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS budget_limits (
            category TEXT PRIMARY KEY,
            amount REAL
        )
    ''')
    
    # Initialize default limits if table is empty
    cursor.execute('SELECT COUNT(*) FROM budget_limits')
    if cursor.fetchone()[0] == 0:
        for cat, limit in DEFAULT_BUDGET_LIMITS.items():
            cursor.execute('INSERT INTO budget_limits (category, amount) VALUES (?, ?)', (cat, limit))
            
    conn.commit()
    conn.close()


init_db()


# --- Database Helpers ---
def get_user_context(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT context_json FROM contexts WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return json.loads(row[0])
    return None


def set_user_context(user_id, context):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO contexts (user_id, context_json)
        VALUES (?, ?)
    ''', (user_id, json.dumps(context)))
    conn.commit()
    conn.close()


def delete_user_context(user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM contexts WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()


def get_budget_limit(category):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT amount FROM budget_limits WHERE category = ?', (category,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0


def set_budget_limit(category, amount):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO budget_limits (category, amount)
        VALUES (?, ?)
    ''', (category, amount))
    conn.commit()
    conn.close()


def add_expense(category, amount, date, description, user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO budget (category, amount, date, description, user_id)
        VALUES (?, ?, ?, ?, ?)
    ''', (category, amount, date, description, user_id))
    conn.commit()
    conn.close()


def get_category_total_spent(category):
    current_month = datetime.now().strftime('%Y-%m')
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT SUM(ABS(amount)) FROM budget 
        WHERE category = ? AND amount < 0 AND strftime('%Y-%m', date) = ?
    ''', (category, current_month))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row[0] else 0


def get_all_budget_summary():
    current_month = datetime.now().strftime('%Y-%m')
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT category, SUM(amount) FROM budget 
        WHERE strftime('%Y-%m', date) = ?
        GROUP BY category
    ''', (current_month,))
    rows = cursor.fetchall()
    conn.close()
    return rows


def add_task(quadrant, description):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO tasks (quadrant, description, created_at, completed)
        VALUES (?, ?, ?, 0)
    ''', (quadrant, description, datetime.now().isoformat()))
    conn.commit()
    conn.close()


def get_active_tasks():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT id, quadrant, description FROM tasks WHERE completed = 0 ORDER BY id ASC')
    rows = cursor.fetchall()
    conn.close()
    return rows


def mark_task_completed(task_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('UPDATE tasks SET completed = 1 WHERE id = ?', (task_id,))
    changes = cursor.rowcount
    conn.commit()
    conn.close()
    return changes > 0


def get_tasks_completion_stats():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT SUM(completed), COUNT(*) FROM tasks')
    row = cursor.fetchone()
    conn.close()
    completed = row[0] if row[0] else 0
    total = row[1] if row[1] else 0
    return completed, total


# --- Logic Helpers ---
def verify_meta_signature(raw_body, signature_header):
    if not APP_SECRET or not signature_header:
        return False
    expected = 'sha256=' + hmac.new(
        APP_SECRET.encode('utf-8'),
        raw_body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def parse_hebrew_datetime(text):
    now = datetime.now()
    day_names = {'ראשון': 6, 'שני': 0, 'שלישי': 1, 'רביעי': 2, 'חמישי': 3, 'שישי': 4, 'שבת': 5}
    
    date_match = re.search(r'(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?', text)
    time_match = re.search(r'(?:בשעה|שעה)?\s*(\d{1,2}):(\d{2})', text)
    hour_only_match = re.search(r'(?:בשעה|שעה)\s*(\d{1,2})\b', text)

    target = now
    is_weekday_parsed = False

    if date_match:
        day = int(date_match.group(1))
        month = int(date_match.group(2))
        year_raw = date_match.group(3)
        year = int(year_raw) if year_raw else now.year
        if year < 100: year += 2000
        target = target.replace(year=year, month=month, day=day)
    else:
        for day_name, weekday in day_names.items():
            if f'ביום {day_name}' in text or f'{day_name}' in text:
                days_ahead = weekday - now.weekday()
                if days_ahead < 0: days_ahead += 7
                target = now + timedelta(days=days_ahead)
                is_weekday_parsed = True
                break

    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))
    elif hour_only_match:
        hour = int(hour_only_match.group(1))
        minute = 0
    else:
        return None, 'לא זיהיתי שעה. כתוב למשל: "ביום ראשון בשעה 09:15"'

    try:
        target = target.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except ValueError:
        return None, 'תאריך או שעה לא תקינים.'

    if is_weekday_parsed and target < now and target.date() == now.date():
        target += timedelta(days=7)

    if target < now:
        return None, 'התאריך/שעה כבר עברו. כתוב תאריך עתידי.'

    return target, None


def extract_event_fields(text):
    location = None
    location_match = re.search(r'ב(?:מיקום|מקום)\s+(.+?)(?:\s+ביום|\s+\d{1,2}[./]\d{1,2}|\s+בשעה|$)', text)
    if location_match:
        location = location_match.group(1).strip()

    clean = text
    clean = re.sub(r'(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?', '', clean)
    clean = re.sub(r'(?:בשעה|שעה)?\s*\d{1,2}:\d{2}', '', clean)
    clean = re.sub(r'(?:בשעה|שעה)\s*\d{1,2}\b', '', clean)
    clean = re.sub(r'ביום\s+(ראשון|שני|שלישי|רביעי|חמישי|שישי|שבת)', '', clean)
    clean = re.sub(r'\b(ליומן|יומן|פגישה|אירוע|תור|תוסיף|הוסף|קבע|תקבע|לקבוע|תזמן|הכנס|לי)\b', '', clean)
    clean = re.sub(r'ב(?:מיקום|מקום)\s+.+$', '', clean)
    clean = re.sub(r'\s+', ' ', clean).strip()

    title = clean if clean else 'אירוע מסהבאק'
    return title, location


def get_calendar_service():
    if not GOOGLE_CREDENTIALS: return None
    try:
        creds_dict = json.loads(GOOGLE_CREDENTIALS)
        credentials = service_account.Credentials.from_service_account_info(
            creds_dict, scopes=['https://www.googleapis.com/auth/calendar']
        )
        return build('calendar', 'v3', credentials=credentials)
    except Exception as e:
        print(f'Calendar service error: {e}')
        return None


# --- WhatsApp Connectors ---
def send_whatsapp_message(to, message):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        return {'ok': False, 'error': 'WHATSAPP_NOT_CONFIGURED'}

    url = f'https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages'
    headers = {'Authorization': f'Bearer {WHATSAPP_TOKEN}', 'Content-Type': 'application/json'}
    data = {'messaging_product': 'whatsapp', 'to': to, 'type': 'text', 'text': {'body': message}}

    try:
        resp = http_requests.post(url, headers=headers, json=data, timeout=10)
        resp.raise_for_status()
        return {'ok': True, 'response': resp.json()}
    except Exception as e:
        print(f'WhatsApp send error: {e}')
        return {'ok': False, 'error': str(e)}


# --- Flask Routes ---
@app.route('/webhook', methods=['GET'])
def verify_webhook():
    if request.args.get('hub.mode') == 'subscribe' and request.args.get('hub.verify_token') == VERIFY_TOKEN:
        return request.args.get('hub.challenge'), 200
    return 'Forbidden', 403


@app.route('/webhook', methods=['POST'])
def webhook():
    raw_body = request.get_data()
    signature = request.headers.get('X-Hub-Signature-256')

    if APP_SECRET and not verify_meta_signature(raw_body, signature):
        return jsonify({'error': 'invalid signature'}), 403

    data = request.get_json(silent=True)
    if not data: return jsonify({'status': 'ignored', 'reason': 'empty json'}), 200

    try:
        entry = data.get('entry', [])[0]
        changes = entry.get('changes', [])[0]
        value = changes.get('value', {})

        if 'messages' not in value:
            return jsonify({'status': 'ignored', 'reason': 'no messages'}), 200

        message = value['messages'][0]
        from_number = message.get('from')

        if message.get('type') == 'interactive':
            text = message.get('interactive', {}).get('button_reply', {}).get('title', '')
        elif message.get('type') == 'text':
            text = message.get('text', {}).get('body', '')
        else:
            send_whatsapp_message(from_number, 'כרגע אני תומך רק בהודעות טקסט.')
            return jsonify({'status': 'ignored'}), 200

        response = process_message(text, from_number)
        send_whatsapp_message(from_number, response)
        return jsonify({'status': 'ok'}), 200

    except Exception as e:
        print(f'Error processing webhook: {e}')
        return jsonify({'status': 'error', 'error': str(e)}), 500


# --- Core Logic ---
def process_message(text, user_id):
    text = text.strip()

    # 1. State Machine (Conversational Context)
    context = get_user_context(user_id)
    if context:
        if text in ['ביטול', 'בטל']:
            delete_user_context(user_id)
            return 'הפעולה בוטלה. אפשר להתחיל מחדש 🙂'
        return handle_context(text, user_id)

    # 2. Cancel Global
    if text in ['ביטול', 'בטל']:
        return 'אין פעולה פתוחה לביטול.'

    # 3. Budget Limits Settings
    limit_match = re.search(r'הגדר\s*תקציב\s*([א-ת]+)\s*(\d+)', text)
    if limit_match:
        category = limit_match.group(1)
        amount = int(limit_match.group(2))
        if category in BUDGET_CATEGORIES:
            set_budget_limit(category, amount)
            return f'✅ תקרת התקציב לקטגוריית {category} עודכנה ל-{amount} ש"ח.'
        return f'❌ לא מצאתי קטגוריה בשם "{category}". הקטגוריות הן: {", ".join(BUDGET_CATEGORIES.keys())}'

    # 4. Task Marking as Done
    if re.search(r'(סיימתי|בוצע|הושלם)\s*(משימה)?', text):
        active_tasks = get_active_tasks()
        if not active_tasks:
            return '📝 אין משימות פתוחות לסיים!'
        
        msg = '*איזו משימה סיימת? (שלח את המספר)*\n\n'
        for task_id, quad, desc in active_tasks:
            emoji = TASK_QUADRANTS_EMOJI.get(quad, '📌')
            msg += f'{task_id}. {emoji} {desc[:40]}...\n'
            
        set_user_context(user_id, {'type': 'complete_task'})
        return msg

    # 5. Add Task (Wizard Step 1)
    if re.search(r'\b(משימה|תוסיף משימה|תזכיר לי)\b', text):
        set_user_context(user_id, {'type': 'add_task_color'})
        return (
            '📝 בוא נוסיף משימה. איזה **צבע** היא?\n\n'
            '🔴 אדום - חשוב ודחוף\n'
            '🟡 צהוב - חשוב, לא דחוף\n'
            '🟠 כתום - דחוף, לא חשוב\n'
            '🟢 ירוק - לא דחוף, לא חשוב\n\n'
            '(שלח לי את הצבע, למשל: "אדום")'
        )

    # 6. Calendar
    if any(word in text for word in ['יומן', 'פגישה', 'אירוע', 'תור', 'קבע', 'תזמן', 'לקבוע']):
        return process_calendar(text)

    # 7. Budget Entry
    amount_match = re.search(r'(\d+)', text)
    if amount_match:
        return handle_amount_entry(text, user_id)

    # 8. Reports
    if 'סטטוס משימות' in text or 'רשימת משימות' in text:
        return get_task_status()
    if 'סטטוס כלכלי' in text or 'מאזן' in text or 'תקציב' in text:
        return get_detailed_budget()
    if 'עזרה' in text or 'תפריט' in text:
        return get_help_menu()

    return get_welcome_message()


def handle_context(text, user_id):
    context = get_user_context(user_id)

    # Flow: Add Budget Category
    if context['type'] == 'budget':
        category = None
        for cat in BUDGET_CATEGORIES.keys():
            if cat in text:
                category = cat
                break
        if not category:
            return '❌ לא זיהיתי קטגוריה. שלח שם תקין (למשל: מזון, דיור) או "ביטול".'
            
        result = finalize_expense(context['amount'], category, context['description'], user_id)
        delete_user_context(user_id)
        return result

    # Flow: Add Task - Step 1 (Color Selection)
    if context['type'] == 'add_task_color':
        selected_quadrant = None
        for color_name, quad_name in TASK_COLOR_MAP.items():
            if color_name in text:
                selected_quadrant = quad_name
                break
                
        if not selected_quadrant:
            return '❌ לא זיהיתי את הצבע. אנא בחר: אדום, צהוב, כתום, או ירוק (או "ביטול").'
            
        set_user_context(user_id, {
            'type': 'add_task_desc',
            'quadrant': selected_quadrant
        })
        emoji = TASK_QUADRANTS_EMOJI[selected_quadrant]
        return f'מצוין ({emoji} {selected_quadrant}).\nמה המשימה בפועל?'

    # Flow: Add Task - Step 2 (Description)
    if context['type'] == 'add_task_desc':
        quadrant = context['quadrant']
        add_task(quadrant, text)
        delete_user_context(user_id)
        emoji = TASK_QUADRANTS_EMOJI[quadrant]
        return f'✅ המשימה נשמרה בהצלחה!\n{emoji} {quadrant}\n{text}'

    # Flow: Complete Task
    if context['type'] == 'complete_task':
        match = re.search(r'(\d+)', text)
        if not match:
            return '❌ אנא שלח רק את **המספר** של המשימה שסיימת (או "ביטול").'
            
        task_id = int(match.group(1))
        success = mark_task_completed(task_id)
        if success:
            delete_user_context(user_id)
            return f'🎉 מעולה! משימה מספר {task_id} סומנה כהושלמה.'
        else:
            return f'❌ לא מצאתי משימה פתוחה עם המספר {task_id}. נסה שוב.'

    delete_user_context(user_id)
    return 'משהו השתבש עם הפעולה. בוטל.'


def handle_amount_entry(text, user_id):
    amount_match = re.search(r'(\d+)', text)
    amount = int(amount_match.group(1))

    category = None
    for cat in BUDGET_CATEGORIES.keys():
        if cat in text:
            category = cat
            break

    if category:
        return finalize_expense(amount, category, text, user_id)

    set_user_context(user_id, {
        'type': 'budget',
        'amount': amount,
        'description': text
    })

    category_list = '\n'.join([f'{emoji} {cat}' for cat, emoji in BUDGET_CATEGORIES.items()])
    return f'רשמתי {amount} ש"ח\n\n📂 לאיזו קטגוריה לשייך?\n\n{category_list}\n\n(שלח את שם הקטגוריה)'


def finalize_expense(amount, category, description, user_id):
    is_income = category == 'הכנסה'
    add_expense(category, amount if is_income else -amount, datetime.now().isoformat(), description, user_id)
    emoji = BUDGET_CATEGORIES.get(category, '💵')

    if not is_income:
        total_spent = get_category_total_spent(category)
        limit = get_budget_limit(category)
        
        if limit > 0:
            remaining = limit - total_spent
            if remaining < 0:
                warning = f'\n\n⚠️ חרגת מהתקציב החודשי ב-{abs(remaining)} ש"ח!'
            elif remaining < limit * 0.2:
                warning = f'\n\n⚠️ נותרו רק {remaining} ש"ח בקטגוריה זו לחודש הנוכחי'
            else:
                warning = f'\nנותרו {remaining} ש"ח (החודש)'
        else:
            warning = ''
    else:
        warning = ''

    return f'✅ נרשם!\n{emoji} {category}: {amount} ש"ח{warning}'


def process_calendar(text):
    service = get_calendar_service()
    if not service:
        return '❌ Google Calendar לא מחובר או חסר תוקף הרשאות.'

    start_time, error = parse_hebrew_datetime(text)
    if error:
        return f'❌ {error}\nלדוגמה: "פגישת צוות ביום ראשון בשעה 09:15"'

    title, location = extract_event_fields(text)
    end_time = start_time + timedelta(hours=1)

    event = {
        'summary': title,
        'start': {'dateTime': start_time.isoformat(), 'timeZone': 'Asia/Jerusalem'},
        'end': {'dateTime': end_time.isoformat(), 'timeZone': 'Asia/Jerusalem'},
    }
    if location: event['location'] = location

    try:
        created = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        link = created.get("htmlLink", "לא זמין")
        return (f'📅 האירוע נוצר בהצלחה!\nכותרת: {title}\n'
                f'זמן: {start_time.strftime("%d/%m/%Y %H:%M")}\nקישור: {link}')
    except Exception as e:
        print(f'Calendar error: {e}')
        return f'❌ שגיאה ביצירת אירוע. בדוק שהיומן המוגדר פתוח לעריכה מול ה-Service Account.'


def get_task_status():
    completed, total = get_tasks_completion_stats()
    active_tasks = get_active_tasks()
    
    if not active_tasks:
        return f'📝 אין משימות פתוחות ברשימה.\n✅ {completed}/{total} משימות הושלמו היסטורית.'

    status = '*📋 סטטוס משימות פתוחות*\n\n'
    
    # Group by quadrant
    grouped = {q: [] for q in TASK_QUADRANTS_EMOJI.keys()}
    for task_id, quad, desc in active_tasks:
        grouped[quad].append((task_id, desc))

    for quadrant, emoji in TASK_QUADRANTS_EMOJI.items():
        tasks_in_quad = grouped[quadrant]
        if tasks_in_quad:
            status += f'{emoji} *{quadrant}* ({len(tasks_in_quad)})\n'
            for task_id, desc in tasks_in_quad:
                status += f'  • [משימה {task_id}]: {desc[:50]}\n'
            status += '\n'

    status += f'\n(לסיום משימה כתוב "סיימתי משימה")'
    return status


def get_detailed_budget():
    summary = get_all_budget_summary()
    if not summary:
        return '💰 אין רשומות תקציב לחודש הנוכחי.'

    current_month_str = datetime.now().strftime("%m/%Y")
    report = f'*💰 סטטוס כלכלי (חודש {current_month_str})*\n\n'

    total_income = 0
    total_expenses = 0

    for category, cat_total in summary:
        emoji = BUDGET_CATEGORIES.get(category, '💵')

        if category == 'הכנסה':
            total_income += cat_total
            report += f'{emoji} *{category}*: +{cat_total} ש"ח\n'
        else:
            total_expenses += abs(cat_total)
            limit = get_budget_limit(category)
            
            if limit > 0:
                percentage = min((abs(cat_total) / limit) * 100, 100)
                filled = min(int(percentage / 10), 10)
                bar = '█' * filled + '░' * (10 - filled)
                report += f'{emoji} *{category}*: {abs(cat_total)}/{int(limit)} ש"ח\n{bar} {int(percentage)}%\n\n'
            else:
                report += f'{emoji} *{category}*: {abs(cat_total)} ש"ח\n\n'

    balance = total_income - total_expenses
    report += '━━━━━━━━━━━━━━━\n'
    report += f'💵 הכנסות: {total_income} ש"ח\n'
    report += f'💸 הוצאות: {total_expenses} ש"ח\n'
    report += '━━━━━━━━━━━━━━━\n'

    if balance >= 0:
        report += f'✅ *מאזן חודשי*: +{balance} ש"ח'
    else:
        report += f'⚠️ *גרעון חודשי*: {balance} ש"ח'

    return report


def get_welcome_message():
    return (
        '👋 שלום! אני *סהבאק* - העוזר האישי שלך\n\n'
        '📅 *יומן*: "תור לרופא ביום ראשון בשעה 09:15"\n'
        '💰 *הוצאות*: "59 שקל ארוחה"\n'
        '✅ *משימות*: "תוסיף משימה" או "סיימתי משימה"\n'
        '📊 *דוחות*: "סטטוס כלכלי" או "סטטוס משימות"\n\n'
        'מה תרצה לעשות?'
    )

def get_help_menu():
    return (
        '🤖 *תפריט עזרה - סהבאק*\n\n'
        '*משימות* ✅\n'
        '• "משימה" / "תוסיף משימה"\n'
        '• "סיימתי משימה" (כדי למחוק משימה מהרשימה)\n'
        '• "סטטוס משימות"\n\n'
        '*תקציב (מתאפס אוטומטית בכל חודש)* 💰\n'
        '• "50 שקל דיור"\n'
        '• "הגדר תקציב דיור 6000" (לעדכון התקרה)\n'
        '• "סטטוס כלכלי"\n\n'
        '*יומן* 📅\n'
        '• "תור לרופא ביום שלישי בשעה 14:00"\n\n'
        '*ביטול פעולה באמצע*\n'
        '• תמיד אפשר לכתוב "ביטול"'
    )


if __name__ == '__main__':
    flask_debug = os.getenv('FLASK_DEBUG', 'False').lower() in ['true', '1']
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=flask_debug)

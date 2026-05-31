from flask import Flask, request, jsonify
import os
import json
import re
import requests as http_requests
from datetime import datetime, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build
import google.generativeai as genai
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
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# DB File (Volume in Railway)
DB_FILE = '/app/data/sahbak.db'

# Configure Gemini AI
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

BUDGET_CATEGORIES = {'דיור': '🏠', 'רכב': '🚗', 'נופש': '✈️', 'מזון': '🍔', 'בריאות': '💊', 'חינוך': '📚', 'בילויים': '🎉', 'קניות': '🛒', 'הכנסה': '💰'}
DEFAULT_BUDGET_LIMITS = {'דיור': 5000, 'רכב': 2000, 'נופש': 1500, 'מזון': 3000, 'בריאות': 1000, 'חינוך': 1500, 'בילויים': 800, 'קניות': 1000}
TASK_QUADRANTS_EMOJI = {'חשוב דחוף': '🔴', 'חשוב לא דחוף': '🟡', 'דחוף לא חשוב': '🟠', 'לא דחוף לא חשוב': '🟢'}

def init_db():
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS budget (id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT, amount REAL, date TEXT, description TEXT, user_id TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, quadrant TEXT, description TEXT, created_at TEXT, completed INTEGER DEFAULT 0)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS contexts (user_id TEXT PRIMARY KEY, context_json TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS budget_limits (category TEXT PRIMARY KEY, amount REAL)''')
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
    return json.loads(row[0]) if row else None

def set_user_context(user_id, context):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO contexts (user_id, context_json) VALUES (?, ?)', (user_id, json.dumps(context)))
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
    cursor.execute('INSERT OR REPLACE INTO budget_limits (category, amount) VALUES (?, ?)', (category, amount))
    conn.commit()
    conn.close()

def add_expense(category, amount, date, description, user_id):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO budget (category, amount, date, description, user_id) VALUES (?, ?, ?, ?, ?)', (category, amount, date, description, user_id))
    conn.commit()
    conn.close()

def get_category_total_spent(category):
    current_month = datetime.now().strftime('%Y-%m')
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(ABS(amount)) FROM budget WHERE category = ? AND amount < 0 AND strftime('%Y-%m', date) = ?", (category, current_month))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row[0] else 0

def get_all_budget_summary():
    current_month = datetime.now().strftime('%Y-%m')
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT category, SUM(amount) FROM budget WHERE strftime('%Y-%m', date) = ? GROUP BY category", (current_month,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def add_task(quadrant, description):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO tasks (quadrant, description, created_at, completed) VALUES (?, ?, ?, 0)', (quadrant, description, datetime.now().isoformat()))
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

# --- Google Calendar ---
def get_calendar_service():
    if not GOOGLE_CREDENTIALS: return None
    try:
        creds_dict = json.loads(GOOGLE_CREDENTIALS)
        credentials = service_account.Credentials.from_service_account_info(creds_dict, scopes=['https://www.googleapis.com/auth/calendar'])
        return build('calendar', 'v3', credentials=credentials)
    except Exception as e:
        print(f'Calendar service error: {e}')
        return None

def process_calendar_ai(title, start_time_iso, location):
    service = get_calendar_service()
    if not service: return '❌ שגיאת התחברות ליומן גוגל (בדוק Credentials).'
    
    start_time = datetime.fromisoformat(start_time_iso)
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
        return f'📅 האירוע נוצר בהצלחה ביומן!\nכותרת: {title}\nזמן: {start_time.strftime("%d/%m/%Y %H:%M")}\nקישור: {link}'
    except Exception as e:
        print(f'Calendar error: {e}')
        return f'❌ שגיאה ביצירת אירוע. ודא ששיתפת את היומן עם הבוט בהגדרות גוגל.'

# --- AI Logic Engine ---
def analyze_with_ai(text):
    if not GEMINI_API_KEY:
        return {"action": "unknown", "reply": "⚠️ מפתח Gemini חסר. אנא הגדר GEMINI_API_KEY ב-Railway."}
        
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    prompt = f"""
    אתה עוזר אישי חכם בוואטסאפ שנקרא "סחבק". תפקידך לנתח משפטים חופשיים של משתמש ולהמיר אותם לפעולות במערכת.
    תאריך ושעה נוכחיים: {now_str} (השתמש בזה כדי לחשב זמנים מופשטים כמו "מחר", "בעוד יומיים", או "ביום שלישי").
    
    נתח את המשפט הבא בדיוק רב: "{text}"
    
    החזר *אך ורק* אובייקט JSON טהור (ללא טקסט מקדים וללא תגיות מיוחדות של קוד). ה-JSON חייב להתאים לאחת מ-4 התבניות הבאות:
    
    1. הוצאה או הכנסה כספית:
    {{"action": "expense", "amount": 100, "category": "מזון", "description": "תיאור קצר של מה שנקנה"}}
    * קטגוריות מותרות בלבד (מצא את המתאימה ביותר): דיור, רכב, נופש, מזון, בריאות, חינוך, בילויים, קניות, הכנסה.
    
    2. הוספת משימה חדשה:
    {{"action": "task", "quadrant": "חשוב דחוף", "description": "מה צריך לעשות"}}
    * הערך quadrant חייב להיות אחד מאלה בלבד: חשוב דחוף, חשוב לא דחוף, דחוף לא חשוב, לא דחוף לא חשוב. בחר לפי ההקשר.
    
    3. קביעת פגישה או אירוע ביומן:
    {{"action": "calendar", "title": "נושא הפגישה", "start_time": "2026-06-01T08:00:00", "location": "מיקום אם צוין אחרת null"}}
    * חובה לחשב תאריך ושעה מדויקים ולהחזיר בפורמט ISO 8601. אם המשתמש לא ציין שעה מפורשת, תקבע את הפגישה ל-09:00 בבוקר.
    
    4. לא מובן / חסר מידע מהותי / שיחת חולין (למשל סתם "היי"):
    {{"action": "unknown", "reply": "תשובה ידידותית וקצרה בעברית שאומרת שאתה סחבק ואיך אפשר לעזור"}}
    """
    try:
        model = genai.GenerativeModel('gemini-1.5-flash-latest')
        response = model.generate_content(prompt)
        res_text = response.text.strip()
        
        # טריק קוד כדי לנקות שאריות Markdown מבלי לשבור את התצוגה כאן
        triple_tick = "`" * 3
        if res_text.startswith(triple_tick + "json"):
            res_text = res_text[7:-3].strip()
        elif res_text.startswith(triple_tick):
            res_text = res_text[3:-3].strip()
        if res_text.endswith(triple_tick):
            res_text = res_text[:-3].strip()
            
        try:
            return json.loads(res_text)
        except json.JSONDecodeError:
            print(f"Failed to parse AI response as JSON: {res_text}")
            return {"action": "unknown", "reply": "סליחה, המוח שלי קצת התבלבל בפיענוח. אפשר לנסח את זה קצת אחרת? 😅"}
            
    except Exception as e:
        print(f"Gemini API Error: {e}")
        return {"action": "unknown", "reply": f"⚠️ יש כרגע עומס על שרתי הבינה המלאכותית: {str(e)[:50]}..."}

# --- WhatsApp Handlers ---
def send_whatsapp_message(to, message):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID: return {'ok': False}
    url = f'https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages'
    headers = {'Authorization': f'Bearer {WHATSAPP_TOKEN}', 'Content-Type': 'application/json'}
    data = {'messaging_product': 'whatsapp', 'to': to, 'type': 'text', 'text': {'body': message}}
    try:
        http_requests.post(url, headers=headers, json=data, timeout=10)
        return {'ok': True}
    except Exception as e:
        return {'ok': False}

def verify_meta_signature(raw_body, signature_header):
    if not APP_SECRET or not signature_header: return False
    expected = 'sha256=' + hmac.new(APP_SECRET.encode('utf-8'), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)

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
    if not data: return jsonify({'status': 'ignored'}), 200
    try:
        value = data.get('entry', [])[0].get('changes', [])[0].get('value', {})
        if 'messages' not in value: return jsonify({'status': 'ignored'}), 200
        message = value['messages'][0]
        from_number = message.get('from')
        text = message.get('text', {}).get('body', '') if message.get('type') == 'text' else ''
        if text:
            response = process_message(text, from_number)
            send_whatsapp_message(from_number, response)
        return jsonify({'status': 'ok'}), 200
    except Exception as e:
        print(f'Webhook Error: {e}')
        return jsonify({'status': 'error'}), 500

# --- Main Processor ---
def process_message(text, user_id):
    text = text.strip()

    # 1. State Machine Context (Wizard fallback)
    context = get_user_context(user_id)
    if context:
        if text in ['ביטול', 'בטל']:
            delete_user_context(user_id)
            return 'הפעולה בוטלה. אפשר להתחיל מחדש 🙂'
        if context['type'] == 'complete_task':
            match = re.search(r'(\d+)', text)
            if not match: return '❌ שלח רק את **המספר** של המשימה שסיימת (או "ביטול").'
            success = mark_task_completed(int(match.group(1)))
            if success:
                delete_user_context(user_id)
                return f'🎉 מעולה! משימה {match.group(1)} סומנה כהושלמה.'
            return '❌ לא מצאתי משימה פתוחה עם המספר הזה.'

    # 2. Hardcoded System Commands
    if text in ['ביטול', 'בטל']: return 'אין פעולה פתוחה לביטול.'
    
    limit_match = re.search(r'הגדר\s*תקציב\s*([א-ת]+)\s*(\d+)', text)
    if limit_match:
        cat, amt = limit_match.group(1), int(limit_match.group(2))
        if cat in BUDGET_CATEGORIES:
            set_budget_limit(cat, amt)
            return f'✅ תקרת התקציב לקטגוריית {cat} עודכנה ל-{amt} ש"ח.'
        return f'❌ לא מצאתי קטגוריה בשם "{cat}".'

    if re.search(r'(סיימתי|בוצע|הושלם)\s*(משימה)?', text):
        active_tasks = get_active_tasks()
        if not active_tasks: return '📝 אין משימות פתוחות לסיים!'
        msg = '*איזו משימה סיימת? (שלח את המספר)*\n\n'
        for task_id, quad, desc in active_tasks:
            msg += f'{task_id}. {TASK_QUADRANTS_EMOJI.get(quad, "📌")} {desc[:40]}...\n'
        set_user_context(user_id, {'type': 'complete_task'})
        return msg

    if 'סטטוס משימות' in text or 'רשימת משימות' in text: return get_task_status()
    if 'סטטוס כלכלי' in text or 'מאזן' in text or 'תקציב' in text: return get_detailed_budget()
    if 'עזרה' in text or 'תפריט' in text: return get_help_menu()

    # 3. AI Magic - The Brain!
    ai_result = analyze_with_ai(text)
    if not ai_result:
        return "❌ המוח של סחבק לא הצליח לנתח את ההודעה. נסה שוב."

    action = ai_result.get("action")
    
    if action == "expense":
        amt = ai_result.get("amount", 0)
        cat = ai_result.get("category", "")
        desc = ai_result.get("description", text)
        if cat not in BUDGET_CATEGORIES: return f"❌ קטגוריה לא חוקית ({cat})."
        add_expense(cat, -amt if cat != 'הכנסה' else amt, datetime.now().isoformat(), desc, user_id)
        limit_txt = ""
        if cat != 'הכנסה' and get_budget_limit(cat) > 0:
            rem = get_budget_limit(cat) - get_category_total_spent(cat)
            limit_txt = f'\n⚠️ חרגת ב-{abs(rem)}!' if rem < 0 else f'\nנותרו {rem} ש"ח (החודש)'
        return f'✅ נרשם!\n{BUDGET_CATEGORIES.get(cat, "💵")} {cat}: {amt} ש"ח{limit_txt}'
        
    elif action == "task":
        quad = ai_result.get("quadrant", "")
        desc = ai_result.get("description", text)
        if quad not in TASK_QUADRANTS_EMOJI: return f"❌ סוג משימה לא חוקי ({quad})."
        add_task(quad, desc)
        return f'✅ משימה נוספה\n{TASK_QUADRANTS_EMOJI[quad]} {quad}\n{desc}'
        
    elif action == "calendar":
        title = ai_result.get("title", "אירוע מסחבק")
        start_time_iso = ai_result.get("start_time")
        if not start_time_iso: return "❌ חסר תאריך ושעה."
        return process_calendar_ai(title, start_time_iso, ai_result.get("location"))
        
    elif action == "unknown":
        return ai_result.get("reply", "לא כל כך הבנתי. אפשר לנסח אחרת? 😅")
    else:
        return get_welcome_message()

def get_task_status():
    completed, total = get_tasks_completion_stats()
    active_tasks = get_active_tasks()
    if not active_tasks: return f'📝 אין משימות פתוחות.\n✅ {completed}/{total} משימות הושלמו.'
    status = '*📋 משימות פתוחות*\n\n'
    grouped = {q: [] for q in TASK_QUADRANTS_EMOJI.keys()}
    for tid, quad, desc in active_tasks: grouped[quad].append((tid, desc))
    for quad, emoji in TASK_QUADRANTS_EMOJI.items():
        if grouped[quad]:
            status += f'{emoji} *{quad}*\n'
            for tid, desc in grouped[quad]: status += f'  • [{tid}]: {desc[:50]}\n'
            status += '\n'
    return status + f'(לסיום: "סיימתי משימה")'

def get_detailed_budget():
    summary = get_all_budget_summary()
    if not summary: return '💰 אין רשומות לחודש הנוכחי.'
    report = f'*💰 סטטוס כלכלי (חודש {datetime.now().strftime("%m/%Y")})*\n\n'
    total_inc, total_exp = 0, 0
    for cat, total in summary:
        emoji = BUDGET_CATEGORIES.get(cat, '💵')
        if cat == 'הכנסה':
            total_inc += total
            report += f'{emoji} *{cat}*: +{total} ש"ח\n'
        else:
            total_exp += abs(total)
            limit = get_budget_limit(cat)
            if limit > 0:
                perc = min((abs(total) / limit) * 100, 100)
                bar = '█' * min(int(perc / 10), 10) + '░' * (10 - min(int(perc / 10), 10))
                report += f'{emoji} *{cat}*: {abs(total)}/{int(limit)}\n{bar} {int(perc)}%\n\n'
            else:
                report += f'{emoji} *{cat}*: {abs(total)} ש"ח\n\n'
    bal = total_inc - total_exp
    report += f'━━━━━━━━━━━━━━━\n💵 הכנסות: {total_inc}\n💸 הוצאות: {total_exp}\n━━━━━━━━━━━━━━━\n'
    report += f'✅ *מאזן*: +{bal}' if bal >= 0 else f'⚠️ *גרעון*: {bal}'
    return report

def get_welcome_message():
    return (
        '👋 אהלן! אני *סחבק* - העוזר האישי החכם שלך 🤖✨\n\n'
        'איתי אפשר פשוט לדבר חופשי, ממש כמו חבר! הנה כמה דברים שאני מבין:\n\n'
        '📅 *יומן:* "תקבע לי פגישה עם דני מחר ב-8 בבוקר"\n'
        '✅ *משימות:* "שים לי משימה דחופה לקנות חלב"\n'
        '💸 *תקציב:* "אכלתי עכשיו המבורגר ב-70 שקל"\n\n'
        '💡 *טיפ:* לעזרה או צפייה בדוחות (כמו מאזן כלכלי), פשוט שלח לי *"תפריט"*.\n\n'
        'אז... מה עושים היום? 🎯'
    )

def get_help_menu():
    return (
        '🤖 *תפריט עזרה - סחבק*\n'
        'אני מבין שפה חופשית! פשוט תכתוב לי מה שאתה רוצה לעשות.\n\n'
        '*פקודות מהירות שימושיות:* ⚡\n'
        '• 📋 *"סטטוס משימות"*\n'
        '• ✔️ *"סיימתי משימה"*\n'
        '• 💰 *"סטטוס כלכלי"*\n'
        '• ⚙️ *"הגדר תקציב [קטגוריה] [סכום]"*\n\n'
        'אני כאן כדי לעשות לך סדר! 😎'
    )

if __name__ == '__main__':
    flask_debug = os.getenv('FLASK_DEBUG', 'False').lower() in ['true', '1']
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=flask_debug)

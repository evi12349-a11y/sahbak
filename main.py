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

# משיכת משתני סביבה מהשרת (כולל מפתח ה-AI החדש)
VERIFY_TOKEN = os.getenv('VERIFY_TOKEN', 'sahbak-verify-2026')
WHATSAPP_TOKEN = os.getenv('WHATSAPP_TOKEN')
PHONE_NUMBER_ID = os.getenv('PHONE_NUMBER_ID')
GOOGLE_CREDENTIALS = os.getenv('GOOGLE_CREDENTIALS')
CALENDAR_ID = os.getenv('CALENDAR_ID', 'primary')
APP_SECRET = os.getenv('APP_SECRET')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# הגדרת נתיב מסד הנתונים הקבוע (Volume)
DB_FILE = '/app/data/sahbak.db'

# הפעלת מנוע הבינה המלאכותית
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# קטגוריות ומילונים
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
    if not service: return '❌ שגיאת התחברות ליומן גוגל. ודא שמשתני הסביבה מוגדרים נכון.'
    
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
        return f'❌ שגיאה ביצירת אירוע. ודא ששיתפת את היומן עם כתובת המייל של הבוט בהגדרות גוגל.'

def analyze_with_ai(text):
    if not GEMINI_API_KEY:
        return {"action": "unknown", "reply": "⚠️ מפתח ה-AI חסר. אנא הגדר GEMINI_API_KEY בשרת."}
        
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # זהו ה"פרומפט" שמוסתר למשתמש ומנחה את המודל איך להגיב
    prompt = f"""
    אתה עוזר אישי חכם בוואטסאפ שנקרא "סחבק". תפקידך לנתח משפטים חופשיים של משתמש ולהמיר אותם לפעולות במערכת.
    תאריך ושעה נוכחיים: {now_str} (השתמש בזה כדי לחשב זמנים מופשטים כמו "מחר", "בעוד יומיים", או "ביום שלישי").
    
    נתח את המשפט הבא בדיוק רב: "{text}"
    
    החזר *אך ורק* אובייקט JSON טהור (ללא טקסט מקדים וללא תגיות Markdown כמו ```json). ה-JSON חייב להתאים לאחת מ-4 התבניות הבאות:
    
    1. הוצאה או הכנסה כספית:
    {{"action": "expense", "amount": 100, "category": "מזון", "description": "תיאור קצר של מה שנקנה"}}
    * קטגוריות מותרות בלבד (מצא את המתאימה ביותר): דיור, רכב, נופש, מזון, בריאות, חינוך, בילויים, קניות, הכנסה.
    
    2. הוספת משימה חדשה:
    {{"action": "task", "quadrant": "חשוב דחוף", "description": "מה צריך לעשות"}}
    * הערך quadrant חייב להיות אחד מאלה בלבד: חשוב דחוף, חשוב לא דחוף, דחוף לא חשוב, לא דחוף לא חשוב. בחר לפי ההקשר אם המשתמש לא ציין במפורש.
    
    3. קביעת פגישה או אירוע ביומן:
    {{"action": "calendar", "title": "נושא הפגישה", "start_time": "2026-06-01T08:00:00", "location": "מיקום אם צוין אחרת null"}}
    * חובה לחשב תאריך ושעה מדויקים ולהחזיר בפורמט ISO 8601. אם המשתמש לא ציין שעה מפורשת, תקבע את הפגישה ל-09:00 בבוקר.
    
    4. לא מובן / חסר מידע מהותי / שיחת חולין (למשל סתם "היי"):
    {{"action": "unknown", "reply": "תשובה ידידותית וקצרה בעברית שאומרת שאתה סחבק ואיך אפשר לעזור"}}
    """
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt)
        res_text = response.text.strip()
        # מנקה שאריות ממרדקאון במידה והמודל בכל זאת החזיר
        if res_text.startswith("

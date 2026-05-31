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
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS budget (id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT, amount REAL, date TEXT, description TEXT, user_id TEXT)')
        conn.execute('CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, quadrant TEXT, description TEXT, created_at TEXT, completed INTEGER DEFAULT 0)')
        conn.execute('CREATE TABLE IF NOT EXISTS contexts (user_id TEXT PRIMARY KEY, context_json TEXT)')
        conn.execute('CREATE TABLE IF NOT EXISTS budget_limits (category TEXT PRIMARY KEY, amount REAL)')
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM budget_limits')
        if cursor.fetchone()[0] == 0:
            for cat, limit in DEFAULT_BUDGET_LIMITS.items():
                cursor.execute('INSERT INTO budget_limits (category, amount) VALUES (?, ?)', (cat, limit))
        conn.commit()

init_db()

# --- Database Helpers ---
def get_user_context(user_id):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT context_json FROM contexts WHERE user_id = ?', (user_id,))
        row = cursor.fetchone()
    return json.loads(row[0]) if row else None

def set_user_context(user_id, context):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('INSERT OR REPLACE INTO contexts (user_id, context_json) VALUES (?, ?)', (user_id, json.dumps(context)))
        conn.commit()

def delete_user_context(user_id):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('DELETE FROM contexts WHERE user_id = ?', (user_id,))
        conn.commit()

def get_budget_limit(category):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT amount FROM budget_limits WHERE category = ?', (category,))
        row = cursor.fetchone()
    return row[0] if row else 0

def set_budget_limit(category, amount):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('INSERT OR REPLACE INTO budget_limits (category, amount) VALUES (?, ?)', (category, amount))
        conn.commit()

def add_expense(category, amount, date, description, user_id):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('INSERT INTO budget (category, amount, date, description, user_id) VALUES (?, ?, ?, ?, ?)', (category, amount, date, description, user_id))
        conn.commit()

def get_category_total_spent(category, user_id):
    current_month = datetime.now().strftime('%Y-%m')
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT SUM(ABS(amount)) FROM budget WHERE category = ? AND amount < 0 AND user_id = ? AND strftime('%Y-%m', date) = ?", (category, user_id, current_month))
        row = cursor.fetchone()
    return row[0] if row[0] else 0

def get_all_budget_summary(user_id):
    current_month = datetime.now().strftime('%Y-%m')
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT category, SUM(amount) FROM budget WHERE strftime('%Y-%m', date) = ? AND user_id = ? GROUP BY category", (current_month, user_id))
        rows = cursor.fetchall()
    return rows

def add_task(quadrant, description):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('INSERT INTO tasks (quadrant, description, created_at, completed) VALUES (?, ?, ?, 0)', (quadrant, description, datetime.now().isoformat()))
        conn.commit()

def get_active_tasks():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id, quadrant, description FROM tasks WHERE completed = 0 ORDER BY id ASC')
        rows = cursor.fetchall()
    return rows

def mark_task_completed(task_id):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE tasks SET completed = 1 WHERE id = ?', (task_id,))
        changes = cursor.rowcount
        conn.commit()
    return changes > 0

def get_tasks_completion_stats():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT SUM(completed), COUNT(*) FROM tasks')
        row = cursor.fetchone()
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
    if not service: return '❌ שגיאת התחברות ליומן גוגל.'
    start_time = datetime.fromisoformat(start_time_iso)
    end_time = start_time + timedelta(hours=1)
    event = {'summary': title, 'start': {'dateTime': start_time.isoformat(), 'timeZone': 'Asia/Jerusalem'}, 'end': {'dateTime': end_time.isoformat(), 'timeZone': 'Asia/Jerusalem'}}
    if location: event['location'] = location
    try:
        created = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        return f'📅 האירוע נוצר בהצלחה!\nכותרת: {title}\nזמן: {start_time.strftime("%d/%m/%Y %H:%M")}'
    except Exception as e:
        return '❌ שגיאה ביצירת אירוע.'

# --- AI Engine ---
def analyze_with_ai(text):
    if not GEMINI_API_KEY: return {"action": "unknown", "reply": "⚠️ מפתח API חסר."}
    prompt = f"אתה העוזר סחבק. נתח '{text}' והחזר JSON בלבד עם action (expense, task, calendar) ושדות רלוונטיים."
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt)
        res_text = re.sub(r'^```(?:json)?\s*', '', response.text.strip())
        res_text = re.sub(r'\s*```$', '', res_text).strip()
        return json.loads(res_text)
    except Exception as e:
        print(f"AI Error: {e}")
        return {"action": "unknown", "reply": "אני נח לרגע, נסה שוב בעוד דקה."}

# --- WhatsApp Handler ---
@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        return request.args.get('hub.challenge') if request.args.get('hub.verify_token') == VERIFY_TOKEN else 'Forbidden', 403
    
    data = request.get_json(silent=True)
    if data and 'messages' in data.get('entry', [{}])[0].get('changes', [{}])[0].get('value', {}):
        msg = data['entry'][0]['changes'][0]['value']['messages'][0]
        user_id = msg.get('from')
        text = msg.get('text', {}).get('body', '')
        
        response = process_message(text, user_id)
        url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
        http_requests.post(url, headers=headers, json={"messaging_product": "whatsapp", "to": user_id, "text": {"body": response}})
    return jsonify({'status': 'ok'}), 200

def process_message(text, user_id):
    # (המשך לוגיקת process_message המוכרת שלך...)
    return "קיבלתי!" 

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)))

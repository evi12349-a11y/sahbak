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

# שם קובץ מסד הנתונים המקומי
DB_FILE = 'sahbak.db'

# Categories
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

BUDGET_LIMITS = {
    'דיור': 5000,
    'רכב': 2000,
    'נופש': 1500,
    'מזון': 3000,
    'בריאות': 1000,
    'חינוך': 1500,
    'בילויים': 800,
    'קניות': 1000
}

TASK_QUADRANTS = {
    'חשוב דחוף': '🔴',
    'חשוב לא דחוף': '🟡',
    'דחוף לא חשוב': '🟠',
    'לא דחוף לא חשוב': '🟢'
}


def init_db():
    """מאתחלת את מסד הנתונים והטבלאות במידה והן לא קיימות במערכת"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # יצירת טבלת תקציב
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
    
    # יצירת טבלת משימות
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quadrant TEXT,
            description TEXT,
            created_at TEXT,
            completed INTEGER DEFAULT 0
        )
    ''')
    
    # יצירת טבלת קונטקסט לניהול שלבי שיחה מול משתמשים
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS contexts (
            user_id TEXT PRIMARY KEY,
            context_json TEXT
        )
    ''')
    
    conn.commit()
    conn.close()


# הפעלת האתחול ברמת המודול על מנת שירוץ גם כאשר שרתי ייצור (WSGI) מייבאים את האפליקציה
init_db()


# --- פונקציות עזר לניהול קונטקסט השיחה במסד הנתונים ---
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


# --- פונקציות עזר לניהול מערכת התקציב במסד הנתונים ---
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
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT SUM(ABS(amount)) FROM budget WHERE category = ? AND amount < 0', (category,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row[0] else 0


def get_all_budget_summary():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT category, SUM(amount) FROM budget GROUP BY category')
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_budget_entries_count():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM budget')
    row = cursor.fetchone()
    conn.close()
    return row[0]


# --- פונקציות עזר לניהול מערכת המשימות במסד הנתונים ---
def add_task(quadrant, description):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO tasks (quadrant, description, created_at, completed)
        VALUES (?, ?, ?, 0)
    ''', (quadrant, description, datetime.now().isoformat()))
    conn.commit()
    conn.close()


def get_active_tasks_by_quadrant(quadrant):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT description FROM tasks WHERE quadrant = ? AND completed = 0', (quadrant,))
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]


def get_tasks_completion_stats():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT SUM(completed), COUNT(*) FROM tasks')
    row = cursor.fetchone()
    conn.close()
    completed = row[0] if row[0] else 0
    total = row[1] if row[1] else 0
    return completed, total


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

    day_names = {
        'ראשון': 6,
        'שני': 0,
        'שלישי': 1,
        'רביעי': 2,
        'חמישי': 3,
        'שישי': 4,
        'שבת': 5
    }

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
        if year < 100:
            year += 2000
        target = target.replace(year=year, month=month, day=day)
    else:
        for day_name, weekday in day_names.items():
            if f'ביום {day_name}' in text or f'{day_name}' in text:
                days_ahead = weekday - now.weekday()
                if days_ahead < 0:
                    days_ahead += 7
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

    # תיקון הבאג: אם פוענח יום בשבוע, התאריך שהתקבל קטן מעכשיו והימים זהים - הכוונה לשבוע הבא
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
    clean = re.sub(r'\b(ליומן|יומן|פגישה|אירוע|תור|תוסיף|הוסף|קבע|תקבע|לקבוע|תזמן|

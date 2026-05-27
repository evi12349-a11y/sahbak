from flask import Flask, request, jsonify
import os
import json
import re
import requests as http_requests
from datetime import datetime, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)

# Environment variables
VERIFY_TOKEN = os.getenv('VERIFY_TOKEN', 'sahbak-verify-2026')
WHATSAPP_TOKEN = os.getenv('WHATSAPP_TOKEN')
PHONE_NUMBER_ID = os.getenv('PHONE_NUMBER_ID')
GOOGLE_CREDENTIALS = os.getenv('GOOGLE_CREDENTIALS')
CALENDAR_ID = os.getenv('CALENDAR_ID', 'primary')

# In-memory storage
budget_data = {}
tasks_data = []

# Categories
BUDGET_CATEGORIES = ['דיור', 'רכב', 'נופש', 'מזון וצריכה', 'הכנסה', 'השקעה']
TASK_QUADRANTS = {
    'חשוב דחוף': 1,
    'חשוב לא דחוף': 2,
    'דחוף לא חשוב': 3,
    'לא דחוף לא חשוב': 4
}

def get_calendar_service():
    if not GOOGLE_CREDENTIALS:
        return None
    try:
        creds_dict = json.loads(GOOGLE_CREDENTIALS)
        credentials = service_account.Credentials.from_service_account_info(
            creds_dict,
            scopes=['https://www.googleapis.com/auth/calendar']
        )
        service = build('calendar', 'v3', credentials=credentials)
        return service
    except Exception as e:
        print(f'Calendar service error: {e}')
        return None

def send_whatsapp_message(to, message):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        print(f'WhatsApp not configured. Would send to {to}: {message}')
        return
    url = f'https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages'
    headers = {'Authorization': f'Bearer {WHATSAPP_TOKEN}', 'Content-Type': 'application/json'}
    data = {'messaging_product': 'whatsapp', 'to': to, 'type': 'text', 'text': {'body': message}}
    try:
        http_requests.post(url, headers=headers, json=data)
    except Exception as e:
        print(f'WhatsApp send error: {e}')

@app.route('/webhook', methods=['GET'])
def verify_webhook():
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')
    if mode == 'subscribe' and token == VERIFY_TOKEN:
        return challenge, 200
    return 'Forbidden', 403

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    if not data:
        return 'OK', 200
    try:
        entry = data['entry'][0]
        changes = entry['changes'][0]
        value = changes['value']
        if 'messages' in value:
            message = value['messages'][0]
            from_number = message['from']
            text = message.get('text', {}).get('body', '')
            response = process_message(text)
            send_whatsapp_message(from_number, response)
    except Exception as e:
        print(f'Error processing webhook: {e}')
    return 'OK', 200

def process_message(text):
    text = text.strip()
    if 'יומן' in text or 'פגישה' in text or 'אירוע' in text:
        return process_calendar(text)
    if 'הוצאה' in text or 'הכנסה' in text:
        return process_budget(text)
    if 'משימה' in text or any(q in text for q in TASK_QUADRANTS.keys()):
        return process_task(text)
    if 'סטטוס' in text or 'מצב' in text:
        return get_status()
    if 'מאזן' in text:
        return get_budget_status()
    return 'שלום! אני סהבאק. אפשר לשלוח:\n- יומן [נושא] [תאריך] [שעה]\n- הוצאה [סכום] [קטגוריה]\n- משימה [רבע] [תיאור]\n- סטטוס / מאזן'

def process_calendar(text):
    service = get_calendar_service()
    if not service:
        return 'שגיאה: Google Calendar לא מחובר'
    
    # Parse title
    title = text
    for word in ['יומן', 'פגישה', 'אירוע', 'תוסיף', 'הוסף']:
        title = title.replace(word, '').strip()
    
    # Parse date/time
    now = datetime.now()
    start_time = now + timedelta(hours=1)
    end_time = start_time + timedelta(hours=1)
    
    # Try to find time pattern (e.g. 14:00, 14)
    time_match = re.search(r'(\d{1,2}):(\d{2})', text)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))
        start_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        end_time = start_time + timedelta(hours=1)
    
    # Try to find date pattern (e.g. 28/05, 28.5)
    date_match = re.search(r'(\d{1,2})[./](\d{1,2})', text)
    if date_match:
        day = int(date_match.group(1))
        month = int(date_match.group(2))
        start_time = start_time.replace(day=day, month=month)
        end_time = start_time + timedelta(hours=1)
    
    event = {
        'summary': title if title else 'אירוע מסהבאק',
        'start': {'dateTime': start_time.isoformat(), 'timeZone': 'Asia/Jerusalem'},
        'end': {'dateTime': end_time.isoformat(), 'timeZone': 'Asia/Jerusalem'},
    }
    
    try:
        created = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        return f'אירוע נוצר ביומן!\n{event["summary"]}\n{start_time.strftime("%d/%m %H:%M")}'
    except Exception as e:
        print(f'Calendar insert error: {e}')
        return f'שגיאה ביצירת אירוע: {str(e)}'

def process_budget(text):
    amount_match = re.search(r'(\d+)', text)
    if not amount_match:
        return 'לא זיהיתי סכום. דוגמה: הוצאה 150 מזון וצריכה'
    amount = int(amount_match.group(1))
    category = None
    for cat in BUDGET_CATEGORIES:
        if cat in text:
            category = cat
            break
    if not category:
        return f'לא זיהיתי קטגוריה. הקטגוריות הן: {", ".join(BUDGET_CATEGORIES)}'
    is_income = 'הכנסה' in text
    if category not in budget_data:
        budget_data[category] = []
    budget_data[category].append({
        'amount': amount if is_income else -amount,
        'date': datetime.now().isoformat(),
        'description': text
    })
    return f'נרשם: {amount} ש"ח ב{category}'

def process_task(text):
    quadrant = None
    for q in TASK_QUADRANTS.keys():
        if q in text:
            quadrant = q
            break
    if not quadrant:
        return f'לא זיהיתי רבע. האפשרויות: {", ".join(TASK_QUADRANTS.keys())}'
    tasks_data.append({
        'quadrant': quadrant,
        'description': text,
        'created_at': datetime.now().isoformat()
    })
    return f'משימה נוספה לרבע "{quadrant}"'

def get_status():
    return f'סה"כ {len(tasks_data)} משימות ברשימה'

def get_budget_status():
    total = sum(sum(e['amount'] for e in entries) for entries in budget_data.values())
    return f'מאזן כולל: {total} ש"ח'

@app.route('/health', methods=['GET'])
def health():
    calendar_ok = get_calendar_service() is not None
    return jsonify({'status': 'healthy', 'service': 'sahbak', 'calendar': calendar_ok}), 200

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

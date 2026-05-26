from flask import Flask, request, jsonify
import os
import json
import re
from datetime import datetime

app = Flask(__name__)

# Environment variables
VERIFY_TOKEN = os.getenv('VERIFY_TOKEN', 'sahbak-verify-2026')
WHATSAPP_TOKEN = os.getenv('WHATSAPP_TOKEN')
PHONE_NUMBER_ID = os.getenv('PHONE_NUMBER_ID')

# In-memory storage (replace with database in production)
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
    
    # Extract message
    try:
        entry = data['entry'][0]
        changes = entry['changes'][0]
        value = changes['value']
        
        if 'messages' in value:
            message = value['messages'][0]
            from_number = message['from']
            text = message.get('text', {}).get('body', '')
            
            # Process message
            response = process_message(text)
            
            # Send reply (placeholder - requires WhatsApp API call)
            # send_whatsapp_message(from_number, response)
            
    except Exception as e:
        print(f'Error processing webhook: {e}')
    
    return 'OK', 200

def process_message(text):
    text = text.strip()
    
    # Check for calendar event
    if 'יומן' in text or 'פגישה' in text:
        return process_calendar(text)
    
    # Check for budget
    if 'הוצאה' in text or 'הכנסה' in text:
        return process_budget(text)
    
    # Check for task
    if 'משימה' in text or any(q in text for q in TASK_QUADRANTS.keys()):
        return process_task(text)
    
    # Status request
    if 'סטטוס' in text or 'מצב' in text:
        return get_status()
    
    if 'מאזן' in text:
        return get_budget_status()
    
    return 'לא הבנתי. אפשר לשלוח:\n- יומן [נושא] [תאריך] [שעה]\n- הוצאה [סכום] [קטגוריה]\n- משימה [רבע] [תיאור]\n- סטטוס / מאזן'

def process_calendar(text):
    # Placeholder for Google Calendar integration
    return f'התקבל אירוע ליומן: {text}\nיצירת אירוע ב-Google Calendar בקרוב...'

def process_budget(text):
    # Extract amount and category
    amount_match = re.search(r'(\d+)', text)
    if not amount_match:
        return 'לא זיהיתי סכום. דוגמה: הוצאה 150 מזון וצריכה'
    
    amount = int(amount_match.group(1))
    
    # Find category
    category = None
    for cat in BUDGET_CATEGORIES:
        if cat in text:
            category = cat
            break
    
    if not category:
        return f'לא זיהיתי קטגוריה. הקטגוריות הן: {", ".join(BUDGET_CATEGORIES)}'
    
    # Determine if income or expense
    is_income = 'הכנסה' in text
    
    # Save to budget data
    if category not in budget_data:
        budget_data[category] = []
    
    budget_data[category].append({
        'amount': amount if is_income else -amount,
        'date': datetime.now().isoformat(),
        'description': text
    })
    
    return f'נרשם: {amount} ש"ח ב{category}'

def process_task(text):
    # Find quadrant
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
    total_tasks = len(tasks_data)
    return f'סה"כ {total_tasks} משימות ברשימה'

def get_budget_status():
    total = 0
    for cat, entries in budget_data.items():
        cat_total = sum(e['amount'] for e in entries)
        total += cat_total
    
    return f'מאזן כולל: {total} ש"ח'

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'healthy', 'service': 'sahbak'}), 200

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

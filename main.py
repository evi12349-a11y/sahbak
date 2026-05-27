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
user_contexts = {}  # Track conversation state per user

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
    headers = {
        'Authorization': f'Bearer {WHATSAPP_TOKEN}',
        'Content-Type': 'application/json'
    }
    data = {
        'messaging_product': 'whatsapp',
        'to': to,
        'type': 'text',
        'text': {'body': message}
    }
    
    try:
        http_requests.post(url, headers=headers, json=data)
    except Exception as e:
        print(f'WhatsApp send error: {e}')

def send_interactive_buttons(to, body_text, buttons):
    """Send WhatsApp message with interactive buttons"""
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        return
    
    url = f'https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages'
    headers = {
        'Authorization': f'Bearer {WHATSAPP_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    # Format buttons (max 3)
    button_list = []
    for i, btn in enumerate(buttons[:3]):
        button_list.append({
            'type': 'reply',
            'reply': {
                'id': f'btn_{i}',
                'title': btn[:20]  # Max 20 chars
            }
        })
    
    data = {
        'messaging_product': 'whatsapp',
        'recipient_type': 'individual',
        'to': to,
        'type': 'interactive',
        'interactive': {
            'type': 'button',
            'body': {'text': body_text},
            'action': {'buttons': button_list}
        }
    }
    
    try:
        http_requests.post(url, headers=headers, json=data)
    except Exception as e:
        print(f'Interactive buttons error: {e}')
        # Fallback to regular message
        fallback = body_text + '\n\n' + '\n'.join([f'{i+1}. {b}' for i, b in enumerate(buttons)])
        send_whatsapp_message(to, fallback)

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
            
            # Handle button response
            if message['type'] == 'interactive':
                button_reply = message['interactive']['button_reply']
                text = button_reply['title']
            else:
                text = message.get('text', {}).get('body', '')
            
            response = process_message(text, from_number)
            
            if isinstance(response, dict) and 'buttons' in response:
                send_interactive_buttons(from_number, response['text'], response['buttons'])
            else:
                send_whatsapp_message(from_number, response)
    
    except Exception as e:
        print(f'Error processing webhook: {e}')
    
    return 'OK', 200

def process_message(text, user_id):
    text = text.strip()
    
    # Check if user is in middle of conversation
    if user_id in user_contexts:
        return handle_context(text, user_id)
    
    # Parse intent
    if any(word in text for word in ['יומן', 'פגישה', 'אירוע', 'תור', 'תוסיף', 'הוסף', 'קבע', 'תזמן', 'זמן', 'לפגוש', 'לקבוע', 'הכנס']):
        return process_calendar(text)
    
    # Check for amount (expense/income)
    amount_match = re.search(r'(\d+)', text)
    if amount_match and not any(word in text for word in ['משימה', 'רבע']):
        return handle_amount_entry(text, user_id)
    
    if any(word in text for word in ['משימה', 'חשוב', 'דחוף']):
        return handle_task_entry(text, user_id)
    
    if 'סטטוס משימות' in text or 'רשימת משימות' in text:
        return get_task_status()
    
    if 'סטטוס כלכלי' in text or 'מאזן' in text:
        return get_detailed_budget()
    
    if 'עזרה' in text or 'תפריט' in text:
        return get_help_menu()
    
    # Default welcome
    return get_welcome_message()

def get_welcome_message():
    return '''👋 שלום! אני *סהבאק* - העוזר האישי שלך\n\n📅 *יומן*: "תור לרופא ביום ראשון 31/5 בשעה 09:15"\n💰 *הוצאות*: "59 שקל ארוחה" (אשאל לאיזו קטגוריה)\n✅ *משימות*: "משימה חשובה ודחופה תרגיל בית 6"\n📊 *דוחות*: "סטטוס כלכלי" או "סטטוס משימות"\n\nמה תרצה לעשות?'''

def get_help_menu():
    menu = '''🤖 *תפריט עזרה - סהבאק*\n\n*ניהול יומן* 📅\n• תור לרופא ביום [תאריך] [שעה]\n• פגישה עם [שם] ב[מקום] [תאריך]\n\n*ניהול תקציב* 💰\n• [סכום] שקל [תיאור]\n• הכנסה [סכום]\n• סטטוס כלכלי\n\n*ניהול משימות* ✅\n• משימה חשובה ודחופה [תיאור]\n• משימה חשובה [תיאור]\n• סטטוס משימות\n\n*קטגוריות תקציב:*\n'''
    for cat, emoji in BUDGET_CATEGORIES.items():
        menu += f'{emoji} {cat}\n'
    return menu

def handle_amount_entry(text, user_id):
    """Handle expense/income - ask for category"""
    amount_match = re.search(r'(\d+)', text)
    amount = int(amount_match.group(1))
    
    # Check if category already mentioned
    category = None
    for cat in BUDGET_CATEGORIES.keys():
        if cat in text:
            category = cat
            break
    
    if category:
        return finalize_expense(amount, category, text, user_id)
    
    # Ask for category
    user_contexts[user_id] = {
        'type': 'budget',
        'amount': amount,
        'description': text
    }
    
    category_list = '\n'.join([f'{emoji} {cat}' for cat, emoji in BUDGET_CATEGORIES.items()])
    return f'רשמתי {amount} ש"ח\n\n📂 לאיזו קטגוריה?\n\n{category_list}\n\n(שלח את שם הקטגוריה)'

def handle_task_entry(text, user_id):
    """Handle task - ask for quadrant if not specified"""
    # Check if quadrant mentioned
    quadrant = None
    for q in TASK_QUADRANTS.keys():
        if all(word in text for word in q.split()):
            quadrant = q
            break
    
    if quadrant:
        return finalize_task(quadrant, text)
    
    # Ask for quadrant
    user_contexts[user_id] = {
        'type': 'task',
        'description': text
    }
    
    quadrant_list = '\n'.join([f'{emoji} {q}' for q, emoji in TASK_QUADRANTS.items()])
    return f'📝 באיזה רבע למשימה הזו?\n\n{quadrant_list}\n\n(שלח את הרבע הרצוי)'

def handle_context(text, user_id):
    """Handle follow-up message based on context"""
    context = user_contexts[user_id]
    
    if context['type'] == 'budget':
        # User replied with category
        category = None
        for cat in BUDGET_CATEGORIES.keys():
            if cat in text:
                category = cat
                break
        
        if not category:
            return 'לא זיהיתי קטגוריה. נסה שוב או שלח "ביטול"'
        
        result = finalize_expense(context['amount'], category, context['description'], user_id)
        del user_contexts[user_id]
        return result
    
    elif context['type'] == 'task':
        # User replied with quadrant
        quadrant = None
        for q in TASK_QUADRANTS.keys():
            if q in text or all(word in text for word in q.split()):
                quadrant = q
                break
        
        if not quadrant:
            return 'לא זיהיתי רבע. נסה שוב או שלח "ביטול"'
        
        result = finalize_task(quadrant, context['description'])
        del user_contexts[user_id]
        return result
    
    return 'משהו השתבש. נסה שוב!'

def finalize_expense(amount, category, description, user_id):
    """Save expense to budget"""
    is_income = 'הכנסה' in category
    
    if category not in budget_data:
        budget_data[category] = []
    
    budget_data[category].append({
        'amount': amount if is_income else -amount,
        'date': datetime.now().isoformat(),
        'description': description
    })
    
    emoji = BUDGET_CATEGORIES.get(category, '💵')
    
    # Check limit
    if not is_income and category in BUDGET_LIMITS:
        total_spent = sum(abs(e['amount']) for e in budget_data[category] if e['amount'] < 0)
        limit = BUDGET_LIMITS[category]
        remaining = limit - total_spent
        
        if remaining < 0:
            warning = f'\n\n⚠️ חרגת מהתקציב ב-{abs(remaining)} ש"ח!'
        elif remaining < limit * 0.2:
            warning = f'\n\n⚠️ נותרו רק {remaining} ש"ח בקטגוריה זו'
        else:
            warning = f'\nנותרו {remaining} ש"ח'
    else:
        warning = ''
    
    return f'✅ נרשם!\n{emoji} {category}: {amount} ש"ח{warning}'

def finalize_task(quadrant, description):
    """Save task"""
    tasks_data.append({
        'quadrant': quadrant,
        'description': description,
        'created_at': datetime.now().isoformat(),
        'completed': False
    })
    
    emoji = TASK_QUADRANTS[quadrant]
    return f'✅ משימה נוספה\n{emoji} {quadrant}\n{description}'

def process_calendar(text):
    service = get_calendar_service()
    if not service:
        return '❌ Google Calendar לא מחובר'
    
    # Parse title - remove command words
    title = text
    for word in ['ליומן', 'יומן', 'פגישה', 'אירוע', 'תור', 'תוסיף', 'הוסף',
             'קבע', 'תקבע', 'לקבוע', 'תזמן', 'הכנס', 'הקרוב', 'הבא', 'לי']:
        title = title.replace(word, '').strip()
    
    # Parse date/time
    now = datetime.now()
    start_time = now + timedelta(hours=1)
    end_time = start_time + timedelta(hours=1)
    
    # Day names
    day_names = {
        'ראשון': 0, 'שני': 1, 'שלישי': 2, 'רביעי': 3,
        'חמישי': 4, 'שישי': 5, 'שבת': 6
    }
    
    for day_name, weekday in day_names.items():
        if day_name in text:
            days_ahead = weekday - now.weekday()
        if days_ahead < 0:
            days_ahead += 7
            start_time = now + timedelta(days=days_ahead)
            break
    
    # Parse time (14:00 or 14)
    time_match = re.search(r'(\d{1,2}):(\d{2})', text)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))
    else:
    # נחפש במיוחד "בשעה 8" או "שעה 8" כדי לא לבלבל עם תאריך
        time_match = re.search(r'(?:בשעה|שעה)\s*(\d{1,2})', text)
        if time_match:
            hour = int(time_match.group(1))
            minute = 0
        else:
            hour, minute = start_time.hour, start_time.minute
    
    start_time = start_time.replace(hour=hour, minute=minute, second=0, microsecond=0)
    end_time = start_time + timedelta(hours=1)
    
    # Parse date (28/5 or 28.5)
    date_match = re.search(r'(\d{1,2})[./](\d{1,2})', text)
    if date_match:
        day = int(date_match.group(1))
        month = int(date_match.group(2))
        try:
            start_time = start_time.replace(day=day, month=month)
            end_time = start_time + timedelta(hours=1)
        except ValueError:
            pass
    
    # Extract location
    location = ''
    location_keywords = ['ב', 'בפסגת זאב', 'בירושלים']
    for keyword in location_keywords:
        if keyword in text:
            parts = text.split(keyword)
            if len(parts) > 1:
                location = parts[1].split()[0] if parts[1].split() else ''
                break
    
    event = {
        'summary': title if title else 'אירוע מסהבאק',
        'start': {'dateTime': start_time.isoformat(), 'timeZone': 'Asia/Jerusalem'},
        'end': {'dateTime': end_time.isoformat(), 'timeZone': 'Asia/Jerusalem'},
    }
    
    if location:
        event['location'] = location
    
    try:
        created = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        return f'📅 אירוע נוצר!\n\n{event["summary"]}\n📍 {location if location else "ללא מיקום"}\n🕐 {start_time.strftime("%A %d/%m/%Y בשעה %H:%M")}'
    except Exception as e:
        print(f'Calendar error: {e}')
        return f'❌ שגיאה ביצירת אירוע\n{str(e)}'

def get_task_status():
    if not tasks_data:
        return '📝 אין משימות ברשימה'
    
    status = '*📋 סטטוס משימות*\n\n'
    
    for quadrant, emoji in TASK_QUADRANTS.items():
        tasks_in_quad = [t for t in tasks_data if t['quadrant'] == quadrant and not t.get('completed')]
        if tasks_in_quad:
            status += f'{emoji} *{quadrant}* ({len(tasks_in_quad)})\n'
            for task in tasks_in_quad[:5]:  # Show max 5 per quadrant
                desc = task['description'][:50]
                status += f'  • {desc}\n'
            status += '\n'
    
    completed = len([t for t in tasks_data if t.get('completed')])
    total = len(tasks_data)
    status += f'\n✅ {completed}/{total} משימות הושלמו'
    
    return status

def get_detailed_budget():
    if not budget_data:
        return '💰 אין רשומות תקציב'
    
    report = '*💰 סטטוס כלכלי*\n\n'
    
    total_income = 0
    total_expenses = 0
    
    for category, entries in budget_data.items():
        cat_total = sum(e['amount'] for e in entries)
        emoji = BUDGET_CATEGORIES.get(category, '💵')
        
        if category == 'הכנסה':
            total_income += cat_total
            report += f'{emoji} *{category}*: +{cat_total} ש"ח\n'
        else:
            total_expenses += abs(cat_total)
            limit = BUDGET_LIMITS.get(category, 0)
            if limit:
                percentage = (abs(cat_total) / limit) * 100
                bar = '█' * int(percentage / 10) + '░' * (10 - int(percentage / 10))
                report += f'{emoji} *{category}*: {abs(cat_total)}/{limit} ש"ח\n{bar} {int(percentage)}%\n\n'
            else:
                report += f'{emoji} *{category}*: {abs(cat_total)} ש"ח\n\n'
    
    balance = total_income - total_expenses
    
    report += f'━━━━━━━━━━━━━━━\n'
    report += f'💵 הכנסות: {total_income} ש"ח\n'
    report += f'💸 הוצאות: {total_expenses} ש"ח\n'
    report += f'━━━━━━━━━━━━━━━\n'
    
    if balance >= 0:
        report += f'✅ *מאזן*: +{balance} ש"ח'
    else:
        report += f'⚠️ *גרעון*: {balance} ש"ח'
    
    return report

@app.route('/health', methods=['GET'])
def health():
    calendar_ok = get_calendar_service() is not None
    return jsonify({
        'status': 'healthy',
        'service': 'sahbak',
        'calendar': calendar_ok,
        'budget_entries': sum(len(v) for v in budget_data.values()),
        'tasks': len(tasks_data)
    }), 200

@app.route('/dashboard', methods=['GET'])
def dashboard():
    """Simple dashboard endpoint"""
    return jsonify({
        'budget': {
            category: {
                'total': sum(e['amount'] for e in entries),
                'count': len(entries),
                'limit': BUDGET_LIMITS.get(category, None)
            }
            for category, entries in budget_data.items()
        },
        'tasks': {
            quadrant: len([t for t in tasks_data if t['quadrant'] == quadrant])
            for quadrant in TASK_QUADRANTS.keys()
        },
        'total_balance': sum(sum(e['amount'] for e in entries) for entries in budget_data.values())
    }), 200

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

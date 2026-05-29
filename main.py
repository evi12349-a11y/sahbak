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

app = Flask(__name__)

# Environment variables
VERIFY_TOKEN = os.getenv('VERIFY_TOKEN', 'sahbak-verify-2026')
WHATSAPP_TOKEN = os.getenv('WHATSAPP_TOKEN')
PHONE_NUMBER_ID = os.getenv('PHONE_NUMBER_ID')
GOOGLE_CREDENTIALS = os.getenv('GOOGLE_CREDENTIALS')
CALENDAR_ID = os.getenv('CALENDAR_ID', 'primary')
APP_SECRET = os.getenv('APP_SECRET')

# In-memory storage (שים לב: נתונים אלו יתאפסו בכל הפעלה מחדש של השרת)
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
        return {'ok': False, 'error': 'WHATSAPP_NOT_CONFIGURED'}

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
        resp = http_requests.post(url, headers=headers, json=data, timeout=10)
        resp.raise_for_status()
        return {'ok': True, 'response': resp.json()}
    except http_requests.exceptions.HTTPError:
        try:
            err = resp.json()
        except Exception:
            err = resp.text
        print(f'WhatsApp HTTP error: {err}')
        return {'ok': False, 'error': err}
    except Exception as e:
        print(f'WhatsApp send error: {e}')
        return {'ok': False, 'error': str(e)}


def send_interactive_buttons(to, body_text, buttons):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        return {'ok': False, 'error': 'WHATSAPP_NOT_CONFIGURED'}

    url = f'https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages'
    headers = {
        'Authorization': f'Bearer {WHATSAPP_TOKEN}',
        'Content-Type': 'application/json'
    }

    button_list = []
    for i, btn in enumerate(buttons[:3]):
        button_list.append({
            'type': 'reply',
            'reply': {
                'id': f'btn_{i}_{btn[:10]}',
                'title': btn[:20]
            }
        })

    data = {
        'messaging_product': 'whatsapp',
        'recipient_type': 'individual',
        'to': to,
        'type': 'interactive',
        'interactive': {
            'type': 'button',
            'body': {'text': body_text[:1024]},
            'action': {'buttons': button_list}
        }
    }

    try:
        resp = http_requests.post(url, headers=headers, json=data, timeout=10)
        resp.raise_for_status()
        return {'ok': True, 'response': resp.json()}
    except Exception as e:
        print(f'Interactive buttons error: {e}')
        fallback = body_text + '\n\n' + '\n'.join([f'{i + 1}. {b}' for i, b in enumerate(buttons[:3])])
        return send_whatsapp_message(to, fallback)


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
    raw_body = request.get_data()
    signature = request.headers.get('X-Hub-Signature-256')

    if APP_SECRET and not verify_meta_signature(raw_body, signature):
        return jsonify({'error': 'invalid signature'}), 403

    data = request.get_json(silent=True)
    if not data:
        return jsonify({'status': 'ignored', 'reason': 'empty json'}), 200

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
            send_whatsapp_message(from_number, 'כרגע אני תומך רק בהודעות טקסט וכפתורים.')
            return jsonify({'status': 'ignored', 'reason': 'unsupported message type'}), 200

        response = process_message(text, from_number)

        if isinstance(response, dict) and 'buttons' in response:
            send_interactive_buttons(from_number, response['text'], response['buttons'])
        else:
            send_whatsapp_message(from_number, response)

        return jsonify({'status': 'ok'}), 200

    except Exception as e:
        print(f'Error processing webhook: {e}')
        return jsonify({'status': 'error', 'error': str(e)}), 500


def process_message(text, user_id):
    text = text.strip()

    # בדיקת מנגנון סימון משימה כבוצעה (חובה להציב בראש הפונקציה כדי למנוע סיווג שגוי)
    if any(word in text for word in ['ביצעתי', 'עשיתי', 'סיימתי', 'השלמתי']):
        return complete_task(text)

    if user_id in user_contexts:
        if text == 'ביטול':
            del user_contexts[user_id]
            return 'בוטל. אפשר להתחיל מחדש 🙂'
        return handle_context(text, user_id)

    if any(word in text for word in ['יומן', 'פגישה', 'אירוע', 'תור', 'תוסיף', 'הוסף', 'קבע', 'תקבע', 'לקבוע', 'תזמן', 'זמן', 'לפגוש', 'לקבוע', 'הכנס']):
        return process_calendar(text)

    amount_match = re.search(r'(\d+)', text)
    if amount_match and not any(word in text for word in ['משימה', 'רבע', 'חשוב', 'דחוף']):
        return handle_amount_entry(text, user_id)

    if any(word in text for word in ['משימה', 'חשוב', 'דחוף', 'חשובה', 'דחופה']):
        return handle_task_entry(text, user_id)

    if 'סטטוס משימות' in text or 'רשימת משימות' in text:
        return get_task_status()

    if 'סטטוס כלכלי' in text or 'מאזן' in text:
        return get_detailed_budget()

    if 'עזרה' in text or 'תפריט' in text:
        return get_help_menu()

    return get_welcome_message()


def get_welcome_message():
    return '''👋 שלום! אני *סהבאק* - העוזר האישי שלך\n\n📅 *יומן*: "תור לרופא ביום ראשון 31/5 בשעה 09:15"\n💰 *הוצאות*: "59 שקל ארוחה" (אשאל לאיזו קטגוריה)\n✅ *משימות*: "משימה חשובה ודחופה תרגיל בית 6"\n📊 *דוחות*: "סטטוס כלכלי" או "סטטוס משימות"\n\nמה תרצה לעשות?'''


def get_help_menu():
    menu = '''🤖 *תפריט עזרה - סהבאק*\n\n*ניהול יומן* 📅\n• תור לרופא ביום [תאריך] [שעה]\n• פגישה עם [שם] ב[מקום] [תאריך]\n\n*ניהול תקציב* 💰\n• [סכום] שקל [תיאור]\n• הכנסה [סכום]\n• סטטוס כלכלי\n\n*ניהול משימות* ✅\n• משימה חשובה ודחופה [תיאור]\n• משימה חשובה [תיאור]\n• סטטוס משימות\n\n*קטגוריות תקציב:*\n'''
    for cat, emoji in BUDGET_CATEGORIES.items():
        menu += f'{emoji} {cat}\n'
    return menu


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

    user_contexts[user_id] = {
        'type': 'budget',
        'amount': amount,
        'description': text
    }

    category_list = '\n'.join([f'{emoji} {cat}' for cat, emoji in BUDGET_CATEGORIES.items()])
    return f'רשמתי {amount} ש"ח\n\n📂 לאיזו קטגוריה?\n\n{category_list}\n\n(שלח את שם הקטגוריה)'


def handle_task_entry(text, user_id):
    # פתרון בעיית ההטיות בעברית: זיהוי גמיש של שורשי המילים (דחוף/דחופה, חשוב/חשובה)
    is_urgent = 'דחוף' in text or 'דחופה' in text
    is_important = 'חשוב' in text or 'חשובה' in text

    # בדיקת שלילה ("לא ד

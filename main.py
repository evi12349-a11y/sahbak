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


def process_tasks_status(user_id):
    user_tasks = [task for task in tasks_data if task.get('user_id') == user_id and not task.get('done')]
    if not user_tasks:
        return 'אין כרגע משימות.'

    sorted_tasks = sorted(user_tasks, key=lambda t: (task_priority_value(t), t.get('created_at', '')))
    lines = ['📋 משימות לפי עדיפות:']
    for i, task in enumerate(sorted_tasks, start=1):
        lines.append(f'{i}. [{task.get("priority", "רגיל")}] {task.get("text", "")}')
    return '\n'.join(lines)


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
    quadrant = None
    for q in TASK_QUADRANTS.keys():
        if all(word in text for word in q.split()):
            quadrant = q
            break

    if quadrant:
        return finalize_task(quadrant, text)

    user_contexts[user_id] = {
        'type': 'task',
        'description': text
    }

    quadrant_list = '\n'.join([f'{emoji} {q}' for q, emoji in TASK_QUADRANTS.items()])
    return f'📝 באיזה רבע למשימה הזו?\n\n{quadrant_list}\n\n(שלח את הרבע הרצוי)'


def handle_context(text, user_id):
    context = user_contexts[user_id]

    if context['type'] == 'budget':
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

    if context['type'] == 'task':
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

    del user_contexts[user_id]
    return 'משהו השתבש. נסה שוב!'


def finalize_expense(amount, category, description, user_id):
    is_income = category == 'הכנסה'

    if category not in budget_data:
        budget_data[category] = []

    budget_data[category].append({
        'amount': amount if is_income else -amount,
        'date': datetime.now().isoformat(),
        'description': description,
        'user_id': user_id
    })

    emoji = BUDGET_CATEGORIES.get(category, '💵')

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
        return (
            '❌ Google Calendar לא מחובר.\n'
            'בדוק שיש GOOGLE_CREDENTIALS, וששיתפת את היומן שלך עם כתובת המייל של ה-service account '
            'ונתת הרשאת Make changes to events.'
        )

    start_time, error = parse_hebrew_datetime(text)
    if error:
        return (
            f'❌ {error}\n'
            'שלח כך:\n'
            '• "פגישה עם דני ביום ראשון בשעה 09:15"\n'
            '• "תור לרופא 31/05 בשעה 14:00"\n'
            '• "פגישה צוות 31/05/2026 בשעה 16:30 במיקום עזריאלי תל אביב"'
        )

    title, location = extract_event_fields(text)
    end_time = start_time + timedelta(hours=1)

    event = {
        'summary': title,
        'start': {'dateTime': start_time.isoformat(), 'timeZone': 'Asia/Jerusalem'},
        'end': {'dateTime': end_time.isoformat(), 'timeZone': 'Asia/Jerusalem'},
    }

    if location:
        event['location'] = location

    try:
        created = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        return (
            f'📅 האירוע נוצר בהצלחה!\n'
            f'כותרת: {title}\n'
            f'מיקום: {location if location else "ללא מיקום"}\n'
            f'זמן: {start_time.strftime("%d/%m/%Y %H:%M")}\n'
            f'קישור: {created.get("htmlLink", "לא זמין")}'
        )
    except Exception as e:
        print(f'Calendar error: {e}')
        return (
            f'❌ לא הצלחתי ליצור אירוע ביומן.\n'
            f'סיבה: {str(e)}\n'
            'בדוק שה-CALENDAR_ID נכון, שה-service account שותף ליומן, '
            'וש-Calendar API מופעל בפרויקט Google Cloud.'
        )


def get_task_status():
    if not tasks_data:
        return '📝 אין משימות ברשימה'

    status = '*📋 סטטוס משימות*\n\n'

    for quadrant, emoji in TASK_QUADRANTS.items():
        tasks_in_quad = [t for t in tasks_data if t['quadrant'] == quadrant and not t.get('completed')]
        if tasks_in_quad:
            status += f'{emoji} *{quadrant}* ({len(tasks_in_quad)})\n'
            for task in tasks_in_quad[:5]:
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
                percentage = min((abs(cat_total) / limit) * 100, 100)
                filled = min(int(percentage / 10), 10)
                bar = '█' * filled + '░' * (10 - filled)
                report += f'{emoji} *{category}*: {abs(cat_total)}/{limit} ש"ח\n{bar} {int(percentage)}%\n\n'
            else:
                report += f'{emoji} *{category}*: {abs(cat_total)} ש"ח\n\n'

    balance = total_income - total_expenses

    report += '━━━━━━━━━━━━━━━\n'
    report += f'💵 הכנסות: {total_income} ש"ח\n'
    report += f'💸 הוצאות: {total_expenses} ש"ח\n'
    report += '━━━━━━━━━━━━━━━\n'

    if balance >= 0:
        report += f'✅ *מאזן*: +{balance} ש"ח'
    else:
        report += f'⚠️ *גרעון*: {balance} ש"ח'

    return report


@app.route('/health', methods=['GET'])
def health():
    calendar_ok = False
    calendar_error = None

    service = get_calendar_service()
    if service:
        try:
            service.calendars().get(calendarId=CALENDAR_ID).execute()
            calendar_ok = True
        except Exception as e:
            calendar_error = str(e)

    return jsonify({
        'status': 'healthy',
        'service': 'sahbak',
        'calendar': calendar_ok,
        'calendar_id': CALENDAR_ID,
        'calendar_error': calendar_error,
        'budget_entries': sum(len(v) for v in budget_data.values()),
        'tasks': len(tasks_data)
    }), 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=True)

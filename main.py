res_text = re.sub(r'\s*```$', '', res_text).strip()
            
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
        print(f"WhatsApp send error to {to}: {e}")
        return {'ok': False}

def verify_meta_signature(raw_body, signature_header):
    if not APP_SECRET or not signature_header: return False
    # שימוש ב- hmac.new מודרני ותקין
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
            # הוספת 3 נקודות (...) רק אם הטקסט ארוך מ-40 תווים
            short_desc = f"{desc[:40]}..." if len(desc) > 40 else desc
            msg += f'{task_id}. {TASK_QUADRANTS_EMOJI.get(quad, "📌")} {short_desc}\n'
        set_user_context(user_id, {'type': 'complete_task'})
        return msg

    if 'סטטוס משימות' in text or 'רשימת משימות' in text: return get_task_status()
    if 'סטטוס כלכלי' in text or 'מאזן' in text or 'תקציב' in text: return get_detailed_budget(user_id)
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
        
        # שמירת ההוצאה עם מזהה המשתמש הספציפי
        add_expense(cat, -amt if cat != 'הכנסה' else amt, datetime.now().isoformat(), desc, user_id)
        
        limit_txt = ""
        if cat != 'הכנסה' and get_budget_limit(cat) > 0:
            rem = get_budget_limit(cat) - get_category_total_spent(cat, user_id)
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
            for tid, desc in grouped[quad]: 
                short_desc = f"{desc[:50]}..." if len(desc) > 50 else desc
                status += f'  • [{tid}]: {short_desc}\n'
            status += '\n'
    return status + f'(לסיום: "סיימתי משימה")'

def get_detailed_budget(user_id):
    summary = get_all_budget_summary(user_id)
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

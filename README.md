# 🤖 סחבק — עוזר אישי ל-WhatsApp

בוט וואטסאפ אישי בעברית לניהול **יומן גוגל**, **משימות** (מטריצת אייזנהאואר) ו**תקציב חודשי** — מבוסס **Google Gemini** עם הבנת שפה טבעית.

תומך בטקסט, **הקלטות קוליות** 🎤, **תמונות** 📷 ו-**PDF** 📄.

---

## ✨ מה חדש בגרסה הזו (התיקונים)

הגרסה הזו פותרת את הבעיות שגרמו לבוט להיות איטי, לקרוס ו"לא להבין":

| # | מה תוקן | איך |
|---|---------|-----|
| 1 | **קריסות ועומס מ-Webhooks** | השרת מחזיר ל-WhatsApp `200 OK` תוך מילישניות ומעבד את התשובה ברקע על **thread pool** מוגבל. כך אין יותר timeouts ושליחות כפולות. |
| 2 | **"שגיאה זמנית בשרתי AI"** | כל קריאה ל-Gemini עטופה ב-**Exponential Backoff** עם jitter (1s → 2s → 4s). שגיאות זמניות (עומס / rate-limit / 5xx) נוסות שוב אוטומטית; שגיאות קבועות נכשלות מיד. |
| 3 | **הבוט "לא מבין"** | מעבר ל-**Function Calling** אמיתי — Gemini מחזיר פעולות מובְנות ומאומתות במקום טקסט חופשי לפענוח. כמעט אפס קריסות הבנה. תומך גם ב**כמה פעולות בהודעה אחת**. |
| 4 | **הודעות קוליות נופלות** | תמלול עובר דרך אותו צינור אסינכרוני + retry, ואז מנותב כאילו הוקלד (יוצר אירועים/משימות/הוצאות). |

### שיפורים נוספים שנכנסו
- 🗑️ **מחיקת משימות** ("מחק את המשימה של החלב"), בנוסף לסימון כהושלם.
- 🕐 **תיקון אזור זמן** ביצירת אירועים + התרעה אם הזמן שביקשת כבר עבר.
- ✂️ **פיצול הודעות ארוכות** למספר הודעות (במקום חיתוך באמצע מילה ב-4096 תווים).
- 🔁 **Retry גם על שליחת WhatsApp והורדת מדיה**, לא רק על ה-AI.
- 🧹 **ניקוי אוטומטי** של טבלת ה-dedup (לא גדלה לאינסוף).
- ✅ **ולידציות**: סכום הוצאה חיובי, קטגוריות חוקיות, ברירת מחדל חכמה לרביע משימה.
- ⚡ פקודות מיידיות ("תפריט") רצות בלי קריאת AI — חוסך זמן ועלות.

---

## 🚀 התקנה ב-Railway (צעד אחר צעד)

### 1. העלאת הקוד ל-GitHub
העלה את כל הקבצים (`app.py`, `requirements.txt`, `Procfile`, `runtime.txt`, `.gitignore`) ל-repository חדש ב-GitHub.

### 2. יצירת פרויקט ב-Railway
1. ב-[Railway](https://railway.app) → **New Project** → **Deploy from GitHub repo**.
2. בחר את ה-repo. Railway יזהה Python ויתקין הכל מ-`requirements.txt`.

### 3. הגדרת משתני סביבה (Variables)
ב-Railway, בלשונית **Variables**, הוסף:

| משתנה | חובה? | תיאור |
|-------|-------|-------|
| `WHATSAPP_TOKEN` | ✅ | טוקן ה-WhatsApp Cloud API (מ-Meta for Developers). |
| `PHONE_NUMBER_ID` | ✅ | ה-Phone Number ID שלך מ-Meta. |
| `GEMINI_API_KEY` | ✅ | מפתח Gemini מ-[Google AI Studio](https://aistudio.google.com/apikey). |
| `VERIFY_TOKEN` | ✅ | מחרוזת סוד שתמציא — תזין אותה גם ב-Meta כשמגדירים את ה-Webhook. |
| `APP_SECRET` | מומלץ | ה-App Secret מ-Meta. מפעיל אימות חתימה על כל webhook (אבטחה). |
| `GOOGLE_CREDENTIALS` | ליומן | תוכן ה-JSON המלא של חשבון שירות (Service Account) של Google Cloud. הדבק את כל ה-JSON כערך אחד. |
| `CALENDAR_ID` | ליומן | מזהה היומן (ברירת מחדל `primary`). אם משתמשים ב-Service Account, צור יומן ושתף אותו עם כתובת המייל של החשבון, ושים כאן את ה-ID שלו. |
| `TIMEZONE` | לא | ברירת מחדל `Asia/Jerusalem`. |
| `GEMINI_MODEL` | לא | ברירת מחדל `gemini-2.5-flash`. |
| `WHATSAPP_API_VERSION` | לא | ברירת מחדל `v21.0`. |
| `DB_PATH` | לא | נתיב למסד הנתונים. **לשמירת מידע בין פריסות — חבר Volume ב-Railway והצבע לכאן** (למשל `/app/data/sahbak.db`). |
| `MAX_WORKERS` | לא | מספר הודעות שמעובדות במקביל (ברירת מחדל `8`). |

> ⚠️ **שמירת נתונים:** SQLite נשמר על דיסק. בלי **Volume** ב-Railway, המידע (הוצאות/משימות) יימחק בכל פריסה מחדש. כדי לשמור: צור Volume, חבר אותו (למשל ל-`/app/data`), והגדר `DB_PATH=/app/data/sahbak.db`.

### 4. חיבור ה-Webhook ב-Meta
1. ב-Railway, העתק את כתובת ה-domain הציבורי של השירות (למשל `https://your-app.up.railway.app`).
2. ב-Meta for Developers → WhatsApp → Configuration → **Webhook**:
   - **Callback URL:** `https://your-app.up.railway.app/webhook`
   - **Verify Token:** הערך שהגדרת ב-`VERIFY_TOKEN`.
3. הירשם (Subscribe) לשדה **messages**.

### 5. בדיקה
- היכנס ל-`https://your-app.up.railway.app/health` — אמור להחזיר JSON עם `"status": "ok"` ולציין אילו שירותים מחוברים.
- שלח הודעה לבוט בוואטסאפ: `"היי"` → אמור לקבל הודעת ברוכים הבאים.

---

## 💬 דוגמאות שימוש

```
"תקבע פגישה עם רופא השיניים ביום ראשון ב-10 בבוקר"
"שים משימה חשובה ודחופה להגיש דוח עד מחר"
"שילמתי 250 שקל על דלק"
"קיבלתי משכורת 12000"
"סיימתי את המשימה של הדוח"
"מחק את המשימה של החלב"
"סטטוס כלכלי"
"מה יש לי לעשות?"
```

אפשר גם לשלב: `"תקבע לי אימון מחר ב-7 בערב וגם תוסיף משימה לקנות נעלי ספורט"` — שתי הפעולות יתבצעו יחד.

---

## 🛠️ הרצה מקומית (אופציונלי)

```bash
pip install -r requirements.txt

export WHATSAPP_TOKEN="..."
export PHONE_NUMBER_ID="..."
export GEMINI_API_KEY="..."
export VERIFY_TOKEN="my-secret"
# (שאר המשתנים לפי הצורך)

python app.py
```

לחשיפת השרת המקומי ל-Meta בזמן פיתוח אפשר להשתמש ב-[ngrok](https://ngrok.com).

---

## 🏗️ ארכיטקטורה בקצרה

```
WhatsApp  →  POST /webhook  →  ACK 200 מיד  ─┐
                                             │ (thread pool, ברקע)
                                             ▼
                          get_ai_tool_calls()  ── Gemini Function Calling (+retry)
                                             │
                          execute_tool()  ──→ יומן / משימות / תקציב (SQLite)
                                             │
                          send_whatsapp_message()  ── שליחת התשובה (+retry, פיצול)
```

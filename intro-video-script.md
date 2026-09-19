# סחבק: תסריט סרטון היכרות

## מפרט

- קהל: משתמשים חדשים וקיימים
- אורך: 75-90 שניות
- שפה: עברית
- קריינות: קול גברי חם, צעיר, כריזמטי ומשעשע בעדינות; אין לחקות אדם אמיתי
- סגנון: חברי, מקצועי, מהיר וברור
- פורמט מומלץ: 9:16 ל-WhatsApp ולרשתות

## חשוב: איך מפיקים את הסרטון ב-Google Flow

לא מבקשים מ-Flow ליצור סרטון אחד באורך 75-90 שניות. מפיקים 10-12 קליפים
קצרים, בוחרים את הטובים ביותר, ומרכיבים אותם לסרטון אחד. כך שומרים על שליטה,
מונעים קפיצות לוגיות ומתקנים קליפ בעייתי בלי להתחיל את הסרטון מחדש.

### כלל ברזל לעברית

לא מבקשים מ-Flow לכתוב טקסט עברי, מספרי טלפון, כפתורים או הודעות WhatsApp
בתוך הווידאו. מודלי וידאו עדיין טועים לעיתים קרובות בעברית. במקום זאת:

1. יוצרים ב-Flow את התנועה, הטלפון והרקע ללא טקסט קריא.
2. משתמשים בצילומי המסך האמיתיים של סחבק כחומר גלם, אם רוצים להציג טקסט.
3. מוסיפים את הכותרות, הכתוביות והמספר `+972 55-318-1335` בעריכה הסופית.

### הכנת חומרי הגלם

צור תיקייה בשם `sahbak-video-assets` והוסף אליה:

- `01-chat-calendar.png` — קביעת אירוע אמיתית.
- `02-free-windows.png` — חלונות פנויים אמיתיים.
- `03-week-plan.png` — הצעת תכנון עם אישור.
- `04-tasks.png` — סטטוס משימות.
- `05-budget.png` — מאזן או תקרות תקציב.
- `06-receipt.png` — קבלה, רק אם מותר לפרסם אותה.
- `07-payment.png` — Apple Pay או Android Pay, רק אם ההדגמה אמיתית.
- `logo-sahbak.png` — לוגו על רקע פשוט, אם יש.

טשטש לפני ההעלאה מספרים של משתמשים, Gmail, קישורי Calendar, שמות ונתונים
כספיים שאינם מיועדים לפרסום. צילומי מסך אמיתיים עדיפים על טקסט ש-Flow מנסה
להמציא.

## הוראות Google Flow צעד אחר צעד

### שלב 1: יצירת פרויקט

1. פתח את `https://flow.google.com/` במחשב.
2. בחר `New project`.
3. קרא לפרויקט `Sahbak intro video - Hebrew`.
4. הגדר יחס תמונה אנכי `9:16`.
5. בחר יצירת `Video` ולא תמונה.

### שלב 2: יצירת קליפ ראשון

1. התחל ללא טקסט עברי בתוך הסצנה.
2. בחר קליפ קצר, בערך 6-8 שניות, לפי האפשרויות שהחשבון מציג.
3. הוסף את צילום המסך המתאים כ-Ingredient או כ-Start Frame.
4. כתוב את הפרומפט של הקליפ מתוך הרשימה בהמשך.
5. צור 2-4 וריאציות.
6. בחר את הווריאציה שבה הטלפון יציב, הטקסט שבצילום לא נמרח, ואין אצבעות או
	 אובייקטים מיותרים שמסתירים את המוצר.

### שלב 3: שמירה על אחידות

- השתמש באותה תמונת טלפון, אותה תאורה ואותו צבע רקע לאורך הקליפים.
- אם Flow מציע `Ingredients`, הוסף את אותו טלפון ואת אותו צילום מסך לקליפים
	הרלוונטיים.
- אל תבקש להוסיף טקסט חדש למסך. הטקסט יתווסף בעריכה.
- אם יש מעבר בין שני צילומי מסך, השתמש ב-Start Frame וב-End Frame במקום לתאר
	את שני המסכים במילים.

### שלב 4: בניית הסרטון

1. פתח את כלי עריכת הסצנות של Flow.
2. סדר את הקליפים לפי מספריהם: 01 עד 12.
3. חתוך כל קליפ לרגעים החזקים בלבד.
4. שמור על מעברים פשוטים: cut, dissolve או zoom עדין.
5. הוסף את הקריינות, המוזיקה והכתוביות בעריכה הסופית.
6. ייצא גרסת בדיקה, צפה בה בטלפון, ורק אז ייצא את הגרסה הסופית.

## פרומפטים נפרדים ליצירת הקליפים

הדבק כל פרומפט בנפרד. אל תנסה להדביק את כולם יחד.

### קליפ 01 — פתיחה, 6 שניות

```text
Vertical 9:16 product intro for a Hebrew WhatsApp personal assistant called
Sahbak. Show a modern smartphone on a clean warm background, subtle motion,
friendly premium productivity product, teal and warm gold accents, soft studio
lighting, confident camera push-in, no readable text, no logos invented, no
extra apps, no distorted phone screen. Leave clean empty space on the upper
left for Hebrew titles added later in editing.
```

### קליפ 02 — פתיחת WhatsApp, 7 שניות

```text
Vertical 9:16 close-up of the same smartphone from the reference image. Show a
user opening a WhatsApp conversation with a personal assistant. Animate the
phone entering the conversation and a single clean message bubble appearing.
Use the uploaded screenshot as the screen reference. Preserve the screenshot
composition. Do not invent or redraw readable text. No extra fingers, no
unreadable UI, no fake app logos.
```

### קליפ 03 — קביעת אירוע, 7 שניות

```text
Vertical 9:16 product demonstration using the uploaded calendar chat
screenshot as the exact visual reference. Show a gentle zoom toward the event
confirmation and the calendar link. Keep the real screenshot unchanged and
legible. Do not generate Hebrew text. No new words, no changed dates, no
changed numbers. Professional, calm, trustworthy motion.
```

### קליפ 04 — חלונות פנויים, 8 שניות

```text
Vertical 9:16 product demonstration using the uploaded free-windows
WhatsApp screenshot. Slowly highlight the list of available time windows with
a clean animated focus ring and a subtle scroll. Preserve every Hebrew word,
date and time exactly from the screenshot. Do not generate replacement text.
No hallucinated calendar events. Keep the phone stable and the interface clear.
```

### קליפ 05 — תוכנית שבועית, 8 שניות

```text
Vertical 9:16 product demonstration using the uploaded weekly-plan screenshot.
Show the proposed tasks and approval controls. Add only a gentle camera move
and focus transitions. Preserve the real Hebrew interface exactly. Do not
redraw buttons or text. Do not change task order, dates, durations or numbers.
The mood is helpful and empowering, not robotic.
```

### קליפ 06 — אישור התוכנית, 6 שניות

```text
Vertical 9:16 close-up of the uploaded WhatsApp approval screenshot. Show a
subtle highlight moving from the full-plan approval option to the selective
task option, without changing any UI text. Use the screenshot as a locked
reference. No generated Hebrew, no invented buttons, no altered numbers.
```

### קליפ 07 — משימות, 7 שניות

```text
Vertical 9:16 clean productivity scene using the uploaded task-status
screenshot. Show the task list as the hero. Use a slow vertical camera move and
subtle emphasis on priority colors. Keep the original Hebrew text and task
labels unchanged. No new text, no fake tasks, no distorted checkboxes.
```

### קליפ 08 — תקציב, 7 שניות

```text
Vertical 9:16 product demonstration using the uploaded budget screenshot.
Show the budget summary and category progress with a calm zoom. Preserve all
real amounts and Hebrew labels exactly. Do not invent financial data. Keep the
visual language clean, trustworthy and easy to scan.
```

### קליפ 09 — קבלה או תמונה, 6 שניות

```text
Vertical 9:16 smartphone scene showing a user attaching a receipt image to a
WhatsApp assistant. Use the uploaded receipt screenshot only as a visual
reference. Show the assistant preparing a confirmation, not silently saving
money data. No readable generated text, no invented receipt details, no real
private information.
```

### קליפ 10 — קלט קולי, 6 שניות

```text
Vertical 9:16 smartphone scene showing a user recording a short voice message
to a personal assistant. Friendly natural hand movement, clean phone screen,
warm light, teal accents, no readable generated text, no visible private data.
The action should communicate speed and ease.
```

### קליפ 11 — חיבור היכולות, 7 שניות

```text
Vertical 9:16 polished product montage: the same smartphone transitions between
calendar planning, task list and budget summary using the uploaded screenshots
as references. Use three clean match cuts, one for each feature. Preserve the
real screenshots; do not invent Hebrew UI. Premium but friendly, restrained
motion, no excessive effects.
```

### קליפ 12 — סיום, 6 שניות

```text
Vertical 9:16 final product shot of the same smartphone on a clean teal and
warm gold background. Leave generous empty space for Hebrew end-card text added
later. Confident gentle camera pull-back, friendly premium productivity brand,
no generated text, no invented phone number, no watermark.
```

## קריינות וכתוביות

הקלט את הקריינות בנפרד, או צור אותה בכלי קול נפרד. אל תבקש מ-Flow לייצר
קריינות עברית יחד עם כל קליפ אם התוצאה אינה יציבה. השתמש בטקסט הקריינות
שמתחת לכל סצנה בקובץ הזה, וחבר אותו מעל הקליפים בעריכה.

הוסף את הכתוביות בעצמך בעריכה, עם גופן עברי קריא כמו Heebo או Rubik. השתמש
במשפט אחד קצר בכל פעם. אל תציג יותר משתי שורות. את מספר הבוט הוסף כטקסט
אמיתי בעריכה:

`+972 55-318-1335`

## רצף מומלץ ואורך סופי

בחר 10-12 קליפים, בערך 6-8 שניות כל אחד, והדק אותם לאורך כולל של 75-90
שניות. אם Flow מייצר רק 8 שניות, זה תקין: לא מאריכים קליפ חלש, אלא מחברים
כמה קליפים קצרים. רצף מומלץ:

`01 → 02 → 03 → 04 → 05 → 06 → 07 → 08 → 09 → 10 → 11 → 12`

## בדיקת איכות לפני פרסום

- האם כל הטקסט העברי הגיע מצילום מסך אמיתי או מהעריכה, ולא נוצר מחדש על ידי
	Flow?
- האם כל תאריך, סכום ומספר טלפון נכונים?
- האם הסרטון ארוך 75-90 שניות?
- האם כל קליפ מספר רעיון אחד?
- האם אפשר להבין את המוצר גם בלי קול?
- האם אין קישורים, מספרים או פרטי משתמש חשופים?
- האם הסרטון נראה טוב בטלפון ולא רק במסך המחשב?

## תסריט לפי סצנות

### 0:00-0:06 | פתיחה

**צילום:** מסך WhatsApp עם פתיחת צ'אט מול סחבק.

**קריינות:**
"תכירו את סחבק: העוזר האישי שמסדר לכם את היומן, המשימות והתקציב, ישירות ב-WhatsApp."

**טקסט על המסך:**
"סחבק. סדר אישי, בלי לפתוח עוד אפליקציה."

### 0:06-0:24 | יומן

**צילום:** הקלדת הודעה: "קבע לי פגישה עם דני מחר ב-10". לאחר מכן הודעה: "תמצא לי חלונות זמן לכל המשימות שלי".

**קריינות:**
"רוצים לקבוע פגישה? פשוט כותבים לסחבק. הוא מבין תאריך, שעה, מיקום ואפילו אירועים חוזרים. ואם השבוע עמוס, הוא קורא את היומן ומוצא חלונות פנויים במקום שתעשו את החישוב בעצמכם."

**טקסט על המסך:**
"קביעה מיידית לבקשה ברורה"
"תכנון חכם לפי היומן"

### 0:24-0:40 | תכנון משימות

**צילום:** הודעה: "תכנן לי את השבוע". הצגת תוכנית עם משימות, תאריכים ומשך. לחיצה על "אשר הכל" ואז דוגמה של "בחר משימות".

**קריינות:**
"אפשר גם לבקש תכנון של שבוע שלם. סחבק מדרג את המשימות לפי חשיבות ודחיפות, מתאים אותן לחלונות הפנויים, ומפצל משימות ארוכות כשצריך. אתם מאשרים את כל התוכנית, או בוחרים רק את המשימות הרלוונטיות."

**טקסט על המסך:**
"חשוב ודחוף קודם"
"אישור מלא או בחירה חלקית"

### 0:40-0:52 | משימות

**צילום:** הוספת משימה עם משך: "תוסיף משימה להכין מצגת, 90 דקות, בצבע כחול". לאחר מכן סטטוס משימות.

**קריינות:**
"לכל משימה אפשר להגדיר משך וצבע. ברירת המחדל היא שעה, אבל אתם בשליטה מלאה. אפשר להוסיף, להשלים, למחוק ולראות את כל המשימות במקום אחד."

**טקסט על המסך:**
"משך וצבע לפי בחירתכם"

### 0:52-1:06 | תקציב

**צילום:** הודעה: "שילמתי 70 שקל על המבורגר". לאחר מכן "מאזן" ותמונה של קבלה.

**קריינות:**
"גם התקציב נשאר פשוט: מדווחים על הוצאה או הכנסה, מקבלים סיכום חודשי, מגדירים תקרות, ואפילו שולחים תמונה של קבלה. סחבק מחכה לאישור שלכם לפני שהוא רושם מידע שחולץ מתמונה."

**טקסט על המסך:**
"הוצאות | הכנסות | דוחות | קבלות"

### 1:06-1:17 | קלט מכל מקום

**צילום:** אייקונים או חיתוכים קצרים של טקסט, הקלטה, תמונה, PDF והתראת Apple Pay/Android Pay.

**קריינות:**
"אפשר לדבר איתו בטקסט, בהקלטה, בתמונה או ב-PDF. עם חיבור מתאים, גם תשלומי Apple Pay ו-Android Pay יכולים להיכנס אוטומטית לתקציב."

### 1:17-1:28 | סיום

**צילום:** מסך WhatsApp עם הודעת תפריט.

**קריינות:**
"לא צריך לזכור פקודות. פשוט כותבים מה צריך, וסחבק עוזר להפוך את העומס לתוכנית שאפשר לבצע. מתחילים כאן: +972 55-318-1335."

**טקסט על המסך:**
"שלחו: תפריט"
"+972 55-318-1335"

## הודעות שכדאי להקליט בסרטון

- "קבע לי פגישה עם דני מחר ב-10"
- "תמצא לי חלונות זמן לכל המשימות שלי"
- "תכנן לי את השבוע"
- "תוסיף משימה להכין מצגת, 90 דקות, בצבע כחול"
- "שילמתי 70 שקל על המבורגר"
- "מאזן"
- "תפריט"

## הנחיות הפקה

- להציג מספרי בחירה זמניים בלבד, לא מזהי מסד נתונים.
- לטשטש מספרי טלפון, כתובות Gmail וקישורי Google Calendar שאינם מיועדים לפרסום.
- להציג אישור לפני יצירת תוכנית, כדי להמחיש שליטה ובטיחות.
- לא להציג התראות Apple Pay או Android Pay כאילו הן פעילות אצל כל משתמש; להציג אותן כיכולת שדורשת חיבור.
- להציג בסרטון את המספר הציבורי: +972 55-318-1335.

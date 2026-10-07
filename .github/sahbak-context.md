# Sahbak — current project context

**Purpose:** compact handoff for future sessions. Read this before re-investigating; refresh only time-sensitive status. Last checked: **2026-10-07 09:58 Israel time**.

## Goal and constraints
- Sahbak is a Hebrew personal assistant for a closed group of about 15 users, with a goal near 30. Features: expenses, tasks, Google Calendar, reminders.
- Owner is a student, has no registered business and wants to avoid bureaucracy or surprise costs; target budget is at most about **$5/month**.
- Owner wants to keep using the **official WhatsApp Business Platform** if this is genuinely allowed and can be made reliable. He explicitly does **not** want Telegram now.
- Do not suggest opening a registered business, adding a payment method, or moving platform as if it were a proven fix. Explain costs/obligations and get explicit approval before such decisions.
- Never use unofficial WhatsApp libraries, evade enforcement, or claim a policy workaround. Never handle credentials or submit legal/Meta forms on the owner's behalf.

## Current live status — verify again when acting
- `/health` checked **2026-10-07 04:39 Israel time**: app `2026-10-07-r39`, Railway `status=ok`, persistent DB, WhatsApp configured but `whatsapp_status=BANNED`, `whatsapp_live=false`.
- Meta Business Support page checked **2026-10-07 09:58 Israel time**: account disabled since **2026-10-06**, generic reason “Acceptable Use Policy” (Hebrew UI: “הפרת תנאי השימוש המקובל”). It says **“Review requested”**, review in progress, typically 24–48 hours.
- The page does not identify which submission the review status refers to or show a specific policy clause. A later appeal draft had been composed but not intentionally submitted; because the page now shows review requested, **do not submit another appeal until the owner verifies the current review/request status**.
- Do not assume that account reinstatement means the use case is approved permanently. Before restarting messages after reinstatement, establish policy fit with Meta and check current status.

## Root-cause assessment — fact vs hypothesis
- **Observed:** same business/WABA has been disabled and reinstated, then disabled again; the reason given is generic. Server and `/health` were up when Meta returned `BANNED`. Shabbat notifications were disabled before the latest recurrence. No evidence found of mass sends. This does not prove the exact trigger.
- **Leading policy-fit hypothesis (not confirmed by Meta):** WhatsApp's Business Solution Terms restrict AI providers/developers from using the Business Solution when AI is the primary rather than incidental/ancillary functionality. Sahbak uses Google's Gemini to understand user requests and is a personal assistant for invited friends, not customer support for a registered business. Removing free chat narrows scope, but does not remove AI as a core feature or make the product a conventional business-customer service. Meta alone must clarify whether this use case qualifies.
- Other possibilities (not established): portfolio/account risk, user feedback, data/privacy policy issue, or a different enforcement reason. Do not present these as confirmed causes.
- The public WhatsApp Business Messaging Policy also requires clear business identity/contact details, user opt-in and opt-out, accurate representation, and restricts sharing/requesting full payment-card numbers, financial account numbers, personal ID numbers, and certain health information. Do not claim these are the cause absent evidence.

## Meta / policy references
- Meta app ID: `3794466687361607`; business portfolio: `1334060028677417`; WABA: `3019343455075493`.
- Official enforcement guide: https://developers.facebook.com/documentation/business-messaging/whatsapp/policy-enforcement.md/
- Official `account_update` webhook reference: https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/account_update.md/
- WhatsApp Business Solution Terms: https://www.whatsapp.com/legal/business-solution-terms/
- WhatsApp Business Messaging Policy: https://www.whatsapp.com/legal/business-policy/
- Reliable reporting/context for AI-primary-functionality clause: https://techcrunch.com/2025/10/18/whatssapp-bans-general-purpose-ai-chatbots/
- Appeal formulation should be honest: explain actual product, disclose Gemini, ask for exact clause and eligibility. Do not assert compliance if unresolved. One review at a time.

## Code and operations
- Repo app: `main.py`; tests: `tests/test_regressions.py`.
- Run tests from `sahbak/`: `../.venv/bin/python -m unittest discover -s tests`.
- Railway deploys on push to `main`. For app changes, bump `BUILD_VERSION`, deploy and verify `/health`.
- `/health`: https://sahbak-production.up.railway.app/health
- Public pages: `/`, `/privacy`, `/terms`, `/data-deletion`, `/guide`.
- `SHABBAT_NOTIFICATIONS` defaults to false. Payment-triggered confirmations may still send following user-requested transactions.
- `.github/workflows/health-check.yml` checks every ~6 hours and emails on failure. Recent known sequence: run #7 passed Oct 6 09:20 Israel, #8 failed Oct 7 00:21 Israel, #9 failed after diagnostic release; check latest run rather than relying on this history.
- Version `r39` added logging for Meta `account_update` webhook events with WABA ID, event, violation type, and ban state/date, without logging message text or user phone numbers. **The webhook subscription still needs to be enabled in Meta App Dashboard** (Webhooks → WhatsApp Business Account → `account_update`). Logging will only help for future events and does not recover old events.

## Important correction from previous work
We previously overestimated the likelihood that removing free chat and disabling Shabbat messages would stop enforcement. Those were mitigations, not a verified root-cause fix. Public pages, business registration, payment method, or an appeal do not by themselves establish that this product is eligible for WhatsApp.

## Efficient start for a new conversation
1. Open the Sahbak workspace/repository.
2. Read this file and `.github/copilot-instructions.md` first.
3. Ask for or check only volatile state relevant to the task: `/health`, latest GitHub health-check run, and Meta's current review page.
4. Do not redo the broad policy search unless Meta supplies new evidence or the user asks. Prefer official Meta terms/docs; label reports and forum anecdotes as secondary.
5. Keep a short, timestamped “Current live status” update here when a material status changes; preserve uncertainty and never append the whole conversation.

**Suggested first prompt:**

> Continue work on Sahbak. First read `.github/copilot-instructions.md` and `.github/sahbak-context.md`. Refresh only the current `/health`, latest monitor run, and Meta review status. Do not resubmit an appeal or change platforms without asking. Keep facts separate from hypotheses and explain the next step simply in Hebrew.

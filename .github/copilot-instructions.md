# Sahbak – compact project guidance

Before investigating Sahbak or Meta status, read [`.github/sahbak-context.md`](sahbak-context.md). Treat its timestamped observations as the starting point; do not repeat broad research or reread large files unless the task requires it.

## Project
- Hebrew WhatsApp assistant: expenses, tasks, calendar, reminders; Flask app mainly in `main.py`.
- User's goal: keep Sahbak on official WhatsApp if feasible, with up to about $5/month. User does not want Telegram, unofficial WhatsApp libraries, or a new Meta business unless they explicitly change their mind.
- Never imply a policy workaround or promise reinstatement. Distinguish observed facts from hypotheses; ask Meta for the exact policy clause.
- User logs in and submits forms themselves. Never handle credentials or submit legal/business declarations for them.
- Reply in Hebrew simply. Always put a line break whenever switching between Hebrew and English.

## Commands and deployment
- Tests from `sahbak/`: `../.venv/bin/python -m unittest discover -s tests`.
- Push to `main` deploys to Railway. Bump `BUILD_VERSION` for application changes and verify `https://sahbak-production.up.railway.app/health`.
- Health fields: `whatsapp_live`, `whatsapp_status`, `whatsapp_error`. `BANNED` means Meta disabled the WhatsApp account, not that Railway is down.
- `.github/workflows/health-check.yml` checks about every six hours and emails on failure; use its run log for the reason.
- No secrets in instructions or context files.

## Token-efficient workflow
- Start from the compact context file; refresh only volatile facts (Meta review status, `/health`, latest workflow run) as needed.
- Search `main.py` with `rg`, then read narrow ranges. Avoid full-page dumps from Meta and full-file reads.
- For a new conversation, use the starter prompt at the end of `.github/sahbak-context.md`.

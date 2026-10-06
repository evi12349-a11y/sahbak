# Sahbak – project notes for AI assistants

## What it is
Hebrew WhatsApp personal assistant (expenses, tasks, calendar, reminders) for ~15–30 invited friends/family. One Flask file: `main.py`. No free chat: the `general` route returns a fixed refusal (Meta policy bans general-purpose AI bots).

## Run / test / deploy
- Tests (from `sahbak/`): `../.venv/bin/python -m unittest discover -s tests` (pytest is not installed).
- Deploy: push to `main` -> Railway auto-deploys in ~1-2 min. Bump `BUILD_VERSION` in `main.py` and poll `https://sahbak-production.up.railway.app/health` for `version`.
- `/health` fields: `whatsapp_live`, `whatsapp_status` (BANNED = Meta disabled the account), `whatsapp_error`.
- `.github/workflows/health-check.yml` runs every ~6h and emails on failure; the reason is only in the job log.
- Public pages (served from `site/`): `/`, `/privacy`, `/terms`, `/data-deletion`, plus `/guide`.

## Meta state (update when it changes)
- App `3794466687361607`, business `1334060028677417`, WABA `3019343455075493`, number +972 55-318-1335.
- Account disabled 30.9 -> appeal accepted 4.10 -> disabled again 5.10 (generic "Acceptable Use Policy", no specific reason).
- Second appeal submitted 6.10 (in review, ~24h). Max ~3 appeals. Do not submit duplicates.
- Business is unverified. Verification needs a registered business (user has none; opening an osek patur was discussed but NOT decided).
- Shabbat notifications are OFF by default (`SHABBAT_NOTIFICATIONS=false`); set env to `true` to restore.

## Rules
- Never type or store user credentials; the user logs in to Meta/Railway/Google themselves and clicks final "submit" buttons.
- User chose: no Telegram for now, no unofficial WhatsApp libraries, no new Meta business. Ask before changing this.
- Budget: at most ~$5/month. Do not add a payment method to Meta without asking.
- Explain simply in Hebrew; put a line break whenever switching between Hebrew and English.

## Saving tokens
- Read only line ranges of `main.py` (~5,300 lines); use grep first.
- Meta pages are huge; read specific elements, not full page text.
- Check this file instead of re-deriving the Meta state.

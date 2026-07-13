# Worklog (MANDATORY)

- After ANY completed work (code change, deploy, prod-DB intervention, decision), append an entry to `WORKLOG.md` — newest on top, format: date, who (Claude/Codex), what, state (local / DEPLOYED / prod-data). Read it at the start of a session to see what other agents did.

# Production

- Production runs ONLY on the Contabo VPS (169.58.8.1). Treat `/data/app.db` there as the sole source of truth for production data and audits.
- Fly.io (`zurys-shop`) was DESTROYED on 2026-07-13 (app + volume deleted). It no longer exists — never use `flyctl` for anything. Final DB snapshot saved locally at `C:\Users\Administrator\webos-backups\fly-app-db-final-2026-07-13.db`.
- Deploy: `python deploy.py --deploy` (tar over SSH → atomic release on Contabo → health check → Cloudflare purge). Only deploy when the user explicitly asks.

# Kick webhook (app/routers/kickhook.py)

- The webhook response code is a contract with Kick: 200 = processed (or duplicate), 503 = "retry this event". Return 503 ONLY when processing FAILED — the success path MUST return 200.
- Incident 2026-07-13: commit 8401852 changed the shared fall-through return to 503, so even successfully processed events told Kick to retry. Result: retry storm (~25 webhooks/min during live streams — every chat message!), load 2.5 on the single-worker SQLite app, slow site, and one lost gift event. Fixed same day by adding `return Response(status_code=200)` at the end of the success branch. Do not "simplify" that return away.
- Sustained 503s also risk Kick unsubscribing the event subscriptions entirely.

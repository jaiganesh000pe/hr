# WEBIFC AI Recruiter (Phase 1)
Single-user recruitment CRM. Two app files: `main.py` (FastAPI) and `index.html` (React/Tailwind via CDN).

## Deploy: GitHub → Railway
1. Push this repo to GitHub.
2. Railway → New Project → Deploy from GitHub repo.
3. Add a Volume mounted at `/data`.
4. Set variables: `APP_PASSWORD`, `SECRET_KEY`, optionally `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `GITHUB_TOKEN`, `DATABASE_URL`.
5. Generate a public domain and open `/health`.

## Phase 1 status
Working and tested: auth (cookie, lockout), /health, contractor and candidate CRUD with pagination, contractor pools, email/phone format validation (never "Verified" from format alone), JD analyzer (needs LLM key), dashboard, settings status.
## Not built yet
Connectors/discovery, resume parsing, matching, duplicates, pipeline Kanban, mail merge, exports, backup/restore, copilot, analytics, interviews, follow-ups.
## Known limitations
Lockout counter is in memory. No real connector has been tested yet. The UI uses in-browser Babel (fine for a single user, slower first load).

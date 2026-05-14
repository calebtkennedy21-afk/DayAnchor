# DayAnchor
Daily task app for personal and clinic responsibilities.

## Current State

The app now supports Postgres persistence using Railway-provided URL environment variables.
If no database URL is found, the app falls back to session memory.

Current capabilities include:

- Task capture with lane, priority, and due date.
- Optional scheduling with date, time, and estimated duration.
- Upcoming schedule panel for planned work blocks.
- Sidebar data controls for DB health and one-click sample task seeding.
- AI Planner panel to generate a prioritized daily plan from your current tasks.

## Run

```bash
streamlit run streamlit_app.py
```

## Notes

- Postgres URL env var priority: `DATABASE_URL`, then `DATABASE_PUBLIC_URL`.
- Connection behavior: the app tries both URLs and uses the first one that connects.
- No additional SSL env var is required; the app defaults to `sslmode=require` if omitted.
- Table creation is automatic on startup (`tasks`).
- Sidebar now shows detected DB variable names and health state for quick troubleshooting.
- AI is enabled by setting `OPENAI_API_KEY`.
- Optional model override: `OPENAI_MODEL` (default: `gpt-4o-mini`).

## Railway Troubleshooting

If the app shows `Detected DB vars: none`, the variables are not reaching the running web service container.

1. Open your **web app service** in Railway (not only the Postgres service).
2. In **Variables**, set one of:
	- `DATABASE_URL=${{Postgres.DATABASE_URL}}`
	- `DATABASE_PUBLIC_URL=${{Postgres.DATABASE_PUBLIC_URL}}`
3. Save and **redeploy** the web app service.
4. Confirm sidebar now shows detected vars and connected DB health.

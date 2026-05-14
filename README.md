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

## Run

```bash
streamlit run streamlit_app.py
```

## Notes

- Postgres URL env var priority: `DATABASE_URL`, then `DATABASE_PUBLIC_URL`.
- No additional SSL env var is required; the app defaults to `sslmode=require` if omitted.
- Table creation is automatic on startup (`tasks`).

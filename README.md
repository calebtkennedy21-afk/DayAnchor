e# DayAnchor
Daily task app for personal and clinic responsibilities.

## Current State

The app now supports Postgres persistence using Railway-provided URL environment variables.
If no database URL is found, the app falls back to session memory.

Current capabilities include:

- Multi-page navigation: Overview, Personal, Clinic, Schedule, AI, Analytics, Notifications, Daily Review, Settings.
- Task capture with lane, priority, and due date.
- Optional scheduling with date, time, and estimated duration.
- Upcoming schedule panel for planned work blocks.
- Sidebar data controls for DB health and one-click sample task seeding.
- AI Planner panel to generate a prioritized daily plan from your current tasks.
- One-click `Add Suggested Tasks` action to insert AI-generated tasks into your board.
- Sidebar view controls for search and filtering by category, priority, status, and schedule state.
- Expanded workflow statuses: Todo, In Progress, Blocked, Completed.
- Inline task editing and rescheduling directly from each task card.
- Recurring tasks (daily/weekly) with automatic next-instance creation when completed.
- Schedule Timeline panel with a configurable day window.
- AI `Auto-Schedule Tasks` flow with one-click apply for schedule updates.
- Notifications page with alert-focused task triage (overdue, blocked, unscheduled high priority, due tomorrow).
- Daily Review page with AI-generated end-of-day recap and tomorrow draft plan.
- Daily Review page now includes a generic Clinic Day Closeout Checklist with per-day completion tracking (independent from task status).
- Morning Ritual and Daily Review trend tracking now use Monday-Sunday week buckets (reset each Monday) with saved week-over-week and month-level productivity comparisons.
- MA Lead page now includes a Weekly Metrics Dashboard with saved week-by-week KPI snapshots and editable targets.
- MA Lead page now includes a 30-Day Rollout Checklist tracker with editable template, rollout start date, and per-day completion logs.
- MA Lead page now includes a full Biweekly Check-ins tab with due queue, check-in capture, follow-up action tracking, trend snapshots, and leadership summary export.
- Settings page with persistent defaults (category, priority, duration, schedule time, timeline window).

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

## Build Roadmap: Make DayAnchor Your Only Productivity App

This is the practical 6-step build order, mapped to this codebase with clear deliverables.

### Milestone 1: Universal Inbox + Smart Parse

Goal: capture any task in one input, from any page.

Implementation targets:
- `app_bootstrap.py`: extend the global quick command bar with a single natural-language input.
- `streamlit_app.py`: add parser helper for patterns like `tomorrow 9am high clinic call PT`.
- `data_access.py`: keep write path through existing `add_task` contract.

Acceptance criteria:
- Can type one-line input and auto-populate title/category/priority/due/schedule when present.
- Works from all major pages without navigation.
- Falls back safely if parser confidence is low.

### Milestone 2: Daily Command Center (Default Landing)

Goal: one screen answers "what matters now" for personal + clinic.

Implementation targets:
- `app_bootstrap.py`: set default page to a command-center-first view.
- `overview_core.py`: add ranked action list (`now`, `today`, `risk`).
- `page_sections.py`: render top-3 personal, top-3 clinic, conflicts, and follow-ups.

Acceptance criteria:
- Home screen loads with top priorities and risks in under 2 seconds on normal datasets.
- Shows schedule conflicts and overdue blockers prominently.

### Milestone 3: Focus Block Execution Mode

Goal: execution loop with minimal UI noise and clear end-of-block outcomes.

Implementation targets:
- `app_bootstrap.py`: add `Start Focus Block` entrypoint and active block banner.
- `streamlit_app.py`: session + DB persistence for block start/end and outcome (`done`, `partial`, `blocked`).
- `analytics` page section: include focus completion metrics.

Acceptance criteria:
- Start/stop a 25/50/90-minute block against any task.
- Ending a block updates task status/progress note in one click.

### Milestone 4: Clinic Workflow Automation

Goal: reduce manual follow-up creation after clinic/case events.

Implementation targets:
- `page_sections.py` surgical workflows: add automation rules UI.
- `streamlit_app.py`: rule engine for case status transitions (for example completed case -> protocol/doc/follow-up tasks).
- `data_access.py`: idempotent task generation guards (no duplicate automation tasks).

Acceptance criteria:
- Changing case status can auto-create configured follow-up tasks.
- Rules can be toggled on/off per automation type.

### Milestone 5: Weekly Review + Recommendations

Goal: close the loop with insights and next-week adjustments.

Implementation targets:
- `overview_core.py`: compute weekly completion, carry-over, blocked trend, lane balance.
- `page_sections.py`: weekly review panel with auto-generated recommendations.
- `streamlit_app.py`: persist weekly snapshots for trend history.

Acceptance criteria:
- Weekly review shows key metrics plus top 3 recommendations.
- Can mark recommendations as accepted and apply selected changes.

### Milestone 6: Mobile Quick Actions + Reminder Reliability

Goal: make capture and completion viable on-the-go.

Implementation targets:
- `app_bootstrap.py` + `page_renderers.py`: mobile-first quick action strip (capture, done, focus start, close day).
- `streamlit_app.py`: reminder escalation rules for critical clinic tasks.
- `README.md`: deployment guidance for home-screen bookmark/PWA-like behavior.

Acceptance criteria:
- Mobile viewport supports one-tap capture and one-tap complete.
- Critical reminders escalate visually by urgency and due window.

## Suggested Delivery Plan

- Week 1: Milestone 1 and Milestone 2
- Week 2: Milestone 3 and Milestone 4
- Week 3: Milestone 5 and Milestone 6

## Definition of Done (Global)

- `PYTHONPATH=. pytest -q` passes.
- No new diagnostics errors in touched files.
- Every milestone includes one short "how to use" note in this README.

e# DayAnchor
Daily task app for personal and clinic responsibilities.

## Current State

The app now supports Postgres persistence using Railway-provided URL environment variables.
If no database URL is found, the app falls back to session memory.

Current capabilities include:

- Multi-page navigation: Overview, Personal, Clinic, Schedule, AI, Analytics, Notifications, Daily Review, Settings.
- Task capture with lane, priority, and due date.
- Smart quick capture now supports natural-language commands (for example: `tomorrow 9am high clinic call PT`) with confidence-based parsing.
- Optional scheduling with date, time, and estimated duration.
- Optional quick-capture autopilot can auto-place unscheduled high-priority tasks into the next open planning slot.
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
- Notification task cards combine overlapping alert reasons so each task appears once while keeping its task actions.
- If tasks share an ID, Notifications reports the collision and withholds task actions until the IDs are resolved.
- Session task IDs use a high-water mark to prevent reuse during that session; resetting session state resets the counter. Database-read fallback alerts are read-only until database reads recover.
- Notifications page now includes Telegram alert controls with test send, manual alert check, and history reset.
- Daily Review page with AI-generated end-of-day recap and tomorrow draft plan.
- Daily Review page now includes a generic Clinic Day Closeout Checklist with per-day completion tracking (independent from task status).
- Morning Ritual and Daily Review trend tracking now use Monday-Sunday week buckets (reset each Monday) with saved week-over-week and month-level productivity comparisons.
- MA Lead page now includes a Weekly Metrics Dashboard with saved week-by-week KPI snapshots and editable targets.
- MA Lead page now includes a 30-Day Rollout Checklist tracker with editable template, rollout start date, and per-day completion logs.
- MA Lead page now includes a full Biweekly Check-ins tab with due queue, check-in capture, follow-up action tracking, trend snapshots, and leadership summary export.
- Settings page with persistent defaults (category, priority, duration, schedule time,\timeline window).
- Settings now includes a Feature Flags section so optional/experimental surfaces can be enabled or disabled without code changes.
- Settings now includes Quick Capture Automation controls (smart parse toggle, confidence threshold, and auto-schedule guardrails).
- Optional diagnostics panel is available via Feature Flags and shows render-time timings for key app sections.
- MA Lead advanced sections now load one selected advanced section at a time to reduce render overhead.

## Run

The application requires the owner-only Google OIDC configuration described below.
For an isolated development environment, set `ALERT_SCHEDULER_ENABLED=false` and
start the container with `sh ./start.sh`. The launcher creates a private temporary
Streamlit secrets file from environment variables and deletes it on exit. Running
`streamlit run streamlit_app.py` directly does not consume those OIDC environment
variables; it requires an equivalent protected Streamlit secrets file.
Existing Streamlit secrets sources remain available; the generated `[auth]`
section takes precedence over any existing `[auth]` section. The scheduler does
not receive the browser authentication variables.

## Owner authentication: staging setup

This branch is a foundation for staging, not authorization to deploy to production.
The app denies access when its auth configuration or owner's subject is missing.
The scheduler is independent of browser authentication and its existing default
is unchanged; explicitly disable it in staging.

1. Create a separate staging Railway project and database with synthetic data.
   Do not copy production database variables or external integration credentials.
   Set `ALERT_SCHEDULER_ENABLED=false`.
   Set `DAYANCHOR_DEPLOYMENT_ENV=staging` on every staging process. This mode is
   an operational setting, not proof that a database is safe.
   Configure the staging service start command as `sh ./start-staging.sh`. It
   runs `python -m staging_database` as a mandatory connection-free preflight
   before launching the existing application command. The standalone preflight
   may also be run on its own. Railway start-command configuration and live
   enforcement must be verified before staging deployment; the unchanged
   production launcher does not enforce this staging preflight.
   The only accepted explicit deployment modes are `production` and `staging`;
   an absent value preserves production behavior. Empty, padded, misspelled,
   or other explicit values are errors. An absent value cannot identify staging,
   so the staging-specific start command is essential.
   Set the app's `DATABASE_URL` to the new staging database's private connection
   string, and independently configure these expected target attributes:

   - `DAYANCHOR_STAGING_DB_HOST`
   - `DAYANCHOR_STAGING_DB_PORT`
   - `DAYANCHOR_STAGING_DB_NAME`
   - `DAYANCHOR_STAGING_DB_USER`

   Obtain the four non-password fields from the new staging PostgreSQL service's
   connection settings, verify them independently, and enter them as separate
   variables. Do not copy a production database URL, password, or target.
   The staging guard requires an explicit host, port, database, user, and
   password and `sslmode` in `DATABASE_URL`, and accepts PostgreSQL URIs or
   keyword DSNs. Approved TLS modes are `require`, `verify-ca`, and
   `verify-full`; prefer `verify-full` with a trusted CA and matching hostname.
   `require` encrypts but does not guarantee server identity. GSS encryption
   negotiation is disabled so that the specified TLS policy is used. Other
   TLS parameters and `PG*` overrides are rejected; libpq's default trust store
   must support the chosen verification mode. Actual Railway TLS and CA
   compatibility has not been verified and must be confirmed before deployment;
   do not silently downgrade TLS.
   It rejects missing or mismatched identities, multiple URL aliases, `PG*`
   environment variables, multi-host and indirect targets, and unsupported
   connection parameters before connecting. It does not fall back after failure.
   Leave `DATABASE_PUBLIC_URL`, `DATABASE_PRIVATE_URL`, `POSTGRES_URL`,
   `POSTGRESQL_URL`, and `PG*` variables unset on the staging app and scheduler.
   Do not set `OPENAI_API_KEY`, `NEWSAPI_KEY`, `TELEGRAM_BOT_TOKEN`, or
   `TELEGRAM_CHAT_ID`, and keep Telegram disabled in staging settings and
   `TELEGRAM_ALERTS_ENABLED=false`. The guard is not a substitute for separate
   credentials, private networking, and infrastructure isolation.
2. In Google Cloud, configure the OAuth consent screen and create a **Web
   application** OAuth client for staging. If the consent screen is in testing
   mode, add the owner as an approved test user. Register the staging origin as
   appropriate and register exactly
   `https://<STAGING_RAILWAY_HOST>/oauth2callback` as an authorized redirect URI,
   replacing the placeholder with the actual staging hostname. The matching
   Railway variable below must contain that same complete URI. Use a separate
   OAuth client and callback for production.
3. Configure these protected staging Railway variables without checking their
   values into source or sharing them in logs:

   - `DAYANCHOR_GOOGLE_CLIENT_ID`
   - `DAYANCHOR_GOOGLE_CLIENT_SECRET`
   - `DAYANCHOR_GOOGLE_REDIRECT_URI`
   - `DAYANCHOR_AUTH_COOKIE_SECRET` (a randomly generated secret of at least
     32 characters)
   - `DAYANCHOR_OWNER_GOOGLE_SUB` (the single approved Google subject)

   Keep Streamlit's `server.trustedUserHeaders` unset: trusted headers can
   override verified identity claims, so this application rejects them.
4. Obtain the owner's `sub` through Google's own OAuth 2.0 Playground or another
   trusted, owner-controlled Google OIDC client, using the `openid` scope and
   Google's authenticated userinfo endpoint. The owner should verify the Google
   account in the consent screen, privately copy only its `sub`, and set the
   staging owner variable. Do not copy tokens or full identity responses into
   tickets, chat, logs, or source. Confirm the subject belongs to the intended
   owner before approving it for production.

Staging acceptance before rollout:

- Confirm anonymous sessions cannot reach navigation or initialize the database.
  The approved owner can sign in; a different Google account is denied.
- Remove or corrupt each auth setting in staging and confirm the application
  fails closed. Check reruns, sign-out, expiration, and a fresh browser session.
- Verify the exact deployed commit, the staging callback, and that no public
  alternate hostname or proxy bypass exposes protected functionality.
- Confirm scheduler and integrations remain disabled and only staging data is
  accessible. Exercise recovery while retaining control-plane access.

Production rollout requires owner approval, independently verified backups and
recovery, a working staging test, a production OAuth client/callback, a securely
approved subject, and an intentional release of the reviewed commit. Production
currently auto-deploys pushes to `main`; do not push this branch or merge it until
the release is approved.

If the owner cannot sign in, use Railway control-plane access to check the exact
callback, client configuration, and owner subject, then deploy a reviewed fix.
Do not add an auth bypass or change the allowlist to admit everyone. Reverting to
the previous unauthenticated application would expose the public app; only do so
after an explicit risk decision and with independently verified protection on
every public entry point.

Streamlit's identity cookie may last up to 30 days and logging out does not
revoke other already-running sessions. This app checks the Google identity
token's expiration on every rerun, but cannot centrally revoke other active
sessions. Authentication does not protect the scheduler or replace an upstream
access gateway for non-Streamlit endpoints.

## Notes

- Outside staging, Postgres URL env var priority starts with `DATABASE_URL`,
  then `DATABASE_PUBLIC_URL`; the app tries configured alternatives until one
  connects. Staging instead requires the single guarded `DATABASE_URL`.
- No additional SSL env var is required; the app defaults to `sslmode=require` if omitted.
- Table creation is automatic on startup (`tasks`).
- Sidebar now shows detected DB variable names and health state for quick troubleshooting.
- AI is enabled by setting `OPENAI_API_KEY`.
- Optional model override: `OPENAI_MODEL` (default: `gpt-4o-mini`).
- Telegram alerts are enabled with `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` (or chat ID can be saved in Settings).
- Optional app deep-link env var for Telegram buttons: `DAYANCHOR_APP_URL`.

## Telegram Alerts (Phone Notifications)

DayAnchor can send Telegram notifications for:

- Upcoming scheduled tasks (default offsets: 60, 15, and 0 minutes).
- Quick reminders at due time with follow-up reminder nudges.
- Morning Ritual and Daily Review prompts at configured times.
- Critical escalation for high-priority clinic tasks that stay open past due.

Setup:

1. Set `TELEGRAM_BOT_TOKEN` in your deployment environment.
2. Set `TELEGRAM_CHAT_ID` in env, or save chat ID in **Settings -> Telegram Notification Channel**.
3. Configure quiet hours, ritual/review times, and check interval in Settings.
4. Use **Notifications -> Telegram Alerts -> Send test message** to validate delivery.

Notes:

- While the app is open, DayAnchor automatically runs Telegram alert checks at the configured interval.
- The container also runs a separate alert scheduler by default, so alerts continue while nobody is viewing the Streamlit app.
- Set `ALERT_SCHEDULER_ENABLED=false` to disable it, or adjust `ALERT_SCHEDULER_INTERVAL_SECONDS` (default: 60).
- The scheduler requires the same Postgres `DATABASE_URL` or `DATABASE_PUBLIC_URL` and Telegram variables as the app.

## Railway Troubleshooting

If the app shows `Detected DB vars: none`, the variables are not reaching the running web service container.

1. Open your **web app service** in Railway (not only the Postgres service).
2. In **Variables**, set one of:
	- `DATABASE_URL=${{Postgres.DATABASE_URL}}`
	- `DATABASE_PUBLIC_URL=${{Postgres.DATABASE_PUBLIC_URL}}`
3. Save and **redeploy** the web app service.
4. Confirm sidebar now shows detected vars and connected DB health.

## Build Roadmap: Make DayAnchor Your Only Productivity App

Concrete execution backlog: see [SPRINT_BACKLOG.md](SPRINT_BACKLOG.md) for ticket-level plan, priorities, estimates, and acceptance criteria.

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

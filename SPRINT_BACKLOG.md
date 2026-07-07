# DayAnchor Sprint Backlog

Owner: DayAnchor Team  
Date: 2026-07-07  
Primary objective: Improve app speed and maintainability immediately by shipping architecture split and feature flags first.

## Sprint 0 (ASAP, 3-5 days)

Goal: Reduce render overhead and unblock modular development in parallel.

### Ticket S0-1: Add centralized feature-flag model
- Priority: P0
- Estimate: 0.5 day
- Files:
  - streamlit_app.py
  - app_bootstrap.py
  - settings_serialization.py
- Scope:
  - Add app settings keys for feature flags under a single namespace.
  - Create helper functions for reading defaults and checking enabled state.
  - Move existing ad-hoc hidden surfaces to the flag helper.
- Acceptance criteria:
  - All gated views read from one feature-flag helper.
  - Flags are persisted in app settings.
  - Existing hidden dashboard behavior is preserved via flag default false.

### Ticket S0-2: Build Feature Flags settings panel
- Priority: P0
- Estimate: 0.5 day
- Files:
  - streamlit_app.py
  - page_sections.py
- Scope:
  - Add Feature Flags section in Settings with toggles and short descriptions.
  - Include reset-to-default action for flags.
- Acceptance criteria:
  - Flags can be changed without code edits.
  - Changes persist after reload and restart.
  - Clear messaging for experimental versus stable flags.

### Ticket S0-3: Add render-time diagnostics (quick perf visibility)
- Priority: P0
- Estimate: 1 day
- Files:
  - streamlit_app.py
  - app_bootstrap.py
- Scope:
  - Add lightweight timing decorator/context helper for major panels.
  - Add optional diagnostics panel showing top slow sections for current run.
- Acceptance criteria:
  - Can see top 5 slow sections with elapsed ms.
  - Diagnostics can be toggled off.
  - No impact on normal UI behavior.

### Ticket S0-4: Lazy-load advanced tabs
- Priority: P0
- Estimate: 1 day
- Files:
  - streamlit_app.py
  - page_sections.py
- Scope:
  - Avoid heavy data loads and expensive transforms for tabs not currently viewed.
  - Defer expensive AI calls until user action.
- Acceptance criteria:
  - Initial page load time improves versus baseline.
  - Hidden/closed advanced tabs do not trigger expensive work.

### Ticket S0-5: Baseline perf benchmark and regression budget
- Priority: P1
- Estimate: 0.5 day
- Files:
  - README.md
  - SPRINT_BACKLOG.md
- Scope:
  - Define baseline measures: cold start, overview render, MA Lead render.
  - Add target budget for each path.
- Acceptance criteria:
  - Performance budget documented.
  - Team can compare before/after on each sprint.

## Sprint 1 (Week 1)

Goal: Begin architecture split with minimal behavior change.

### Ticket S1-1: Create feature package structure and context contracts
- Priority: P0
- Estimate: 1 day
- New files:
  - features/__init__.py
  - features/common.py
  - features/types.py
- Scope:
  - Define shared context contract and dependency injection pattern.
  - Add thin adapters to existing functions.
- Acceptance criteria:
  - New package compiles and is imported by app entrypoint.
  - No functional changes visible to users.

### Ticket S1-2: Extract MA Lead module from streamlit_app.py
- Priority: P0
- Estimate: 2 days
- Files:
  - streamlit_app.py
  - features/ma_lead/panel.py (new)
  - features/ma_lead/services.py (new)
- Scope:
  - Move rendering and helpers for MA Lead to dedicated module.
  - Keep public function signature stable.
- Acceptance criteria:
  - MA Lead page behavior unchanged.
  - Streamlit app file line count reduced significantly.
  - Existing related tests still pass.

### Ticket S1-3: Extract Family Schedule module
- Priority: P1
- Estimate: 1.5 days
- Files:
  - streamlit_app.py
  - features/family/panel.py (new)
  - family_goals_core.py
- Scope:
  - Move family schedule rendering and non-core helper logic to module.
- Acceptance criteria:
  - Family page parity maintained.
  - No new diagnostics errors.

### Ticket S1-4: Extract Daily Review + Morning Ritual module
- Priority: P1
- Estimate: 1.5 days
- Files:
  - streamlit_app.py
  - features/review/panel.py (new)
  - ai_workflows.py
- Scope:
  - Move review-related rendering logic into cohesive module.
- Acceptance criteria:
  - Daily Review and Morning Ritual features unchanged.
  - Existing trend calculations still match current outputs.

## Sprint 2 (Week 2)

Goal: Finish split for highest-impact sections and harden with tests.

### Ticket S2-1: Extract Analytics + Notifications modules
- Priority: P1
- Estimate: 1.5 days
- Files:
  - streamlit_app.py
  - features/analytics/panel.py (new)
  - features/notifications/panel.py (new)
- Acceptance criteria:
  - Page parity maintained.
  - Entry file keeps only orchestration logic.

### Ticket S2-2: Add integration tests for feature flags and tab gating
- Priority: P0
- Estimate: 1 day
- Files:
  - tests/test_feature_flags.py (new)
  - tests/test_app_bootstrap.py (new)
- Scope:
  - Validate default flags, persistence, and conditional rendering rules.
- Acceptance criteria:
  - Tests fail if a hidden feature is accidentally rendered.
  - Flag persistence is verified across load/save flows.

### Ticket S2-3: Add snapshot-style tests for normalized settings contracts
- Priority: P1
- Estimate: 1 day
- Files:
  - tests/test_settings_serialization.py
  - tests/test_overview_core.py
- Scope:
  - Guard against accidental schema drift in app settings.
- Acceptance criteria:
  - Settings normalize deterministically.
  - Backward compatibility for existing keys is maintained.

### Ticket S2-4: Remove dead/duplicated helper paths after extraction
- Priority: P2
- Estimate: 0.5 day
- Files:
  - streamlit_app.py
  - page_sections.py
- Acceptance criteria:
  - No duplicate helpers across old and new modules.
  - Lint and tests remain clean.

## Backlog (Next Up After Sprint 2)

### Ticket B1: DB migration framework (Alembic)
- Priority: P1
- Estimate: 2 days
- Rationale: Safe schema evolution for growing persistence model.

### Ticket B2: Universal Inbox natural-language parser
- Priority: P1
- Estimate: 2-3 days
- Rationale: Biggest product UX gain for rapid capture.

### Ticket B3: Clinic automation rules engine
- Priority: P1
- Estimate: 3 days
- Rationale: Reduce manual follow-up creation and missed work.

### Ticket B4: SLA-style alerting layer
- Priority: P2
- Estimate: 2 days
- Rationale: Earlier warning on operational risks.

## Definition of Done (for each ticket)
- Behavior parity preserved unless explicitly changed in acceptance criteria.
- No new diagnostics errors in touched files.
- Tests updated for every new setting, flag, or module contract.
- README updated when user-visible controls are added.

## Execution Notes
- Start with Sprint 0 immediately to capture quick performance wins while refactor starts.
- Keep each extraction PR small and reversible.
- Prefer no schema/data changes during module extraction; isolate risk.

# Zema v0.4.0 Changelog

Comparison base: `v0.3.0`
Release target: `v0.4.0`

## Summary

Zema v0.4.0 turns the project from an API/CLI/Telegram tracker into a usable
browser-first treatment dashboard. The release adds a server-rendered dashboard,
single-user management flows, location thumbnails, dashboard treatment actions,
undo, improved adherence views, safer due-state behavior, and a more accurate
Telegram reminder/menu experience.

## Dashboard

- Added a FastAPI/Jinja dashboard with login, secure session cookie, CSRF
  protection, dark mode, and logout.
- Added `/dashboard` overview with Due Now, Upcoming, Active Locations, and
  Adherence sections.
- Added Due Now cards focused on location, thumbnail, phase, and phase-1 slot
  when relevant.
- Added thumbnail click-to-preview dialog for location images.
- Added Upcoming rows with date-only next/last treatment labels and phase-1
  morning/evening slot labels.
- Added `Log`, `Log all locations`, `Healed`, and `Relapsed` dashboard actions.
- Added dashboard undo for the latest dashboard-originated application log,
  healed action, or relapse action.
- Made undo event-based so newer API/agent logs do not get deleted by a
  dashboard undo.
- Added Account, Subject, Add Location, and Edit Locations as real settings
  tabs instead of one long anchor-scroll page.
- Added account username/password update from the dashboard.
- Added subject rename from the dashboard.
- Added location create/edit/delete flows from the dashboard.
- Added automatic episode creation when a new location is added.
- Added location image upload, replacement, fallback display, and management
  thumbnails.

## Adherence

- Aligned dashboard adherence with treatment timing instead of naive calendar
  counts.
- Added adherence ranges for last week, last month, last year, all time, and
  custom date ranges.
- Added auto-submit preset controls while keeping custom range apply separate.
- Added stable adherence card sizing so range changes do not shift the page.
- Added hover titles for adherence day markers.
- Added correct current-day `due` status instead of showing today as `missed`.
- Added pre-start gray markers so dates before treatment tracking do not look
  completed.
- Treated days with no expected treatment as green/not-due completion.
- Fixed last-week handling so unlogged due days are shown consistently.
- Added year reference markers and improved adherence heatmap presentation.

## Reliability And Due Logic

- Kept due state correct across backend restarts with startup/read catch-up.
- Preserved phase catch-up state and exposed successful catch-up status.
- Fixed phase-1 start-day expectations.
- Fixed phase-1 evening due logic.
- Fixed phase-1 due calculations to use the configured deployment timezone.
- Added guardrails so stale Telegram buttons cannot log against obsolete due
  state.
- Improved visible due error handling.

## Telegram

- Restored Telegram reply keyboard after episode creation.
- Fixed all Telegram start-episode entry paths.
- Fixed Telegram start episode creation flow.
- Streamlined Telegram episode and location creation.
- Fixed Telegram due prompt inline menus.
- Removed due empty-state inline menu.
- Polished Telegram adherence summary UX.
- Improved Telegram reminder behavior, button flows, and heatmap tests.

## API, CLI, And Data Model

- Added dashboard route package and templates/static assets.
- Added dashboard read model for due, upcoming, active locations, adherence,
  settings data, and image URLs.
- Extended services for location deletion with related episode/application/event
  cleanup.
- Improved application, adherence, episode, and location behavior covered by
  backend tests.
- Updated CLI/Telegram client behavior and docs for the changed backend surfaces.

## Documentation

- Reworked the README opening for first-time users.
- Added a five-minute dashboard start path.
- Added first-use checklist for account, subject, location, and treatment flow.
- Added product-oriented descriptions of dashboard, CLI, and Telegram surfaces.
- Updated persistent deployment clone path to `adriankae/zema`.

## Test Coverage

- Added broad dashboard tests for login, overview rendering, due cards,
  thumbnails, image fallback, settings tabs, account updates, subject/location
  management, treatment logging, log-all, undo, heal, relapse, delete location,
  and adherence rendering.
- Added adherence regression tests for current-day due status, pre-start days,
  not-due days, and rolling taper schedules.
- Added episode and Telegram regression coverage for phase transitions, due
  timing, reminders, menus, and workflow repairs.

## Commit Range

Included commits from `v0.3.0` through the v0.4.0 release line:

- `fc72a96` feat: complete dashboard ratchet 4
- `c7d7e79` feat: manage subjects and locations from dashboard
- `3694752` feat: add dashboard dark mode and adherence ranges
- `eb72600` fix: align dashboard adherence with treatment timing
- `159bcf7` feat: add treatment dashboard v0
- `94be88e` fix: keep due state reliable after restart
- `e87ef05` Remove due empty-state inline menu
- `89ceec3` Fix Telegram due prompt inline menus
- `d19b0ac` Polish Telegram adherence summary UX
- `bd0fe19` Restore Telegram keyboard after episode creation
- `891b7fe` Fix adherence phase one start-day expectations
- `7505ac8` Fix phase one due deployment timezone
- `d03dbf8` Fix phase one evening due logic
- `ca3116a` Fix all Telegram start episode entry paths
- `8a153b0` Fix Telegram start episode creation flow
- `54fbae9` Streamline Telegram episode and location creation

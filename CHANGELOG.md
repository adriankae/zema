# Changelog

## 0.6.0

### Added

- Added always-available dashboard quick action buttons for marking locations as healed or relapsed.
- Added location picker dialogs for healed Phase 1 locations and relapsed Phase 2+ locations.
- Added empty-state messages when no eligible locations exist for a quick action.

### Changed

- Kept quick action buttons visible across Overview, Settings, and Settings subtabs.
- Adjusted mobile topbar wrapping so quick action controls stay accessible on narrow screens.
- Improved empty Upcoming card spacing.

### Notes

- Quick actions reuse the existing dashboard heal and relapse episode endpoints.
- Browser QA covered desktop, mobile, Settings, all Settings subtabs, and end-to-end quick action flows against a copied database.

## 0.3.0

### Added

- Added guided Telegram bot setup through `zema setup telegram`.
- Added a Telegram bot runtime through `zema telegram run`.
- Added Telegram configuration validation, status, and connection test commands.
- Added a button-driven Telegram frontend for core Zema workflows.
- Added guided Telegram workflows for starting episodes, creating locations, setting location images, healing/relapsing episodes, logging treatments, due items, and adherence.
- Added Docker profile support for running Telegram separately from `zema-be`.
- Added Telegram chat/user allowlist configuration.
- Added Telegram write-permission controls.
- Added Telegram-specific environment variable loading while preserving existing `CZM_*` variables.
- Added `python-telegram-bot` with JobQueue support as a CLI dependency.
- Added always-available Telegram command/menu UX with private-chat persistent reply keyboard support.
- Added configurable morning and evening Telegram treatment reminders.
- Added reminder prompts with optional location images, quick log buttons, snooze, and open-menu actions.
- Added Telegram reminder config commands and environment variable overrides.
- Added location-image confirmation prompts for heal and relapse workflows.
- Added Telegram adherence heatmap images for summary range buttons.
- Added persistent Docker Compose deployment support with restart policies for `postgres`, `zema-be`, and `zema-telegram`.
- Added a named `zema-postgres-data` Docker volume for PostgreSQL persistence while preserving `zema-location-images`.
- Added `.env.example` for Docker Compose Telegram/API secret configuration.
- Added Telegram subject deletion from the button-guided Subjects flow.

### Changed

- Renamed the Telegram button label from `Due today` to `Due now` while preserving `/due` and stale `Due today` keyboard compatibility.
- Changed `DELETE /subjects/{subject_id}` to destructively remove subject-owned medical data instead of blocking subjects with episodes.
- Changed Telegram due/log treatment UX to use location-first due prompts with direct `Log application` actions.
- Changed phase-1 due logic to use morning/evening slots anchored to the current active phase start, including relapse-day behavior.
- Changed relapsed active-flare episodes so they can be healed again and become due immediately when appropriate.

### Security

- Added allowed-chat and optional allowed-user enforcement.
- Added masked secret display for Telegram config.
- Added allowlisted Telegram command/button dispatch instead of arbitrary shell execution.
- Added confirmation flows for state-changing episode actions.
- Reminder delivery is restricted to configured allowed chats and respects Telegram write-permission settings.
- Subject deletion remains account-scoped and deletes only subject-owned data; shared body locations, taper phases, account data, API keys, and other subjects remain untouched.

### Notes

- Telegram runtime runs outside the backend API container.
- Backend remains the source of truth.
- No LLM integration is required for Telegram bot usage.
- Telegram reminders use `/episodes/due`; snooze state is in-memory and resets on bot restart.
- Docker reboot persistence requires Docker itself to start on boot; Compose uses named volumes for database and location-image data.
- Subject deletion in 0.3.0 is intentionally destructive for that subject's medical history.
- Existing `zema setup`, `czm` compatibility, `CZM_*` variables, and `~/.config/czm/config.toml` remain supported.

## 0.2.0

### Added

- Added optional zero-or-one image support for body locations.
- Added filesystem-backed location image storage with database metadata.
- Added location image upload, download, and delete API endpoints.
- Added `zema location create --image` and `zema location image set|get|remove` CLI workflows.
- Added Docker Compose storage configuration and a backend-only named volume for location images.

### Changed

- Location responses now include nullable image metadata when an image is present.

### Notes

- Existing location records without images remain valid.
- The CLI still talks to the backend over HTTP and never writes directly into backend image storage.
- No multi-image galleries, thumbnails, image transformations, HEIC support, or object storage were added in this release.

## 0.1.3

### Added

- Imported the CLI package under `cli/` while preserving it as a separate Python package.
- Added `zema` as the preferred CLI executable and kept `czm` as a compatibility alias.
- Added a separate `zema-cli` Docker runtime for CLI, tooling, and agent usage.
- Added persisted daily adherence snapshots via `episode_daily_adherence`.
- Added backend adherence calculation and rebuild services.
- Added adherence API endpoints for calendar, summary, missed days, per-episode adherence, and rebuild workflows.
- Added `zema adherence` CLI commands for calendar, summary, missed days, per-episode views, and rebuild workflows.
- Added a dedicated `docker/api.Dockerfile` for the backend image while preserving the root Dockerfile.

### Changed

- Docker Compose now separates the backend API runtime (`zema-be`) from the CLI/agent runtime (`zema-cli`).
- Backend remains the source of truth for adherence calculations; the CLI only calls backend APIs.
- GET adherence endpoints default to dynamic read-only calculation; `persisted=true` reads stored audit snapshots.

### Notes

- Existing `/episodes/due` behavior is unchanged.
- Adherence snapshots use a fixed protocol schedule anchored to each phase start date.
- Extra applications are capped via `credited_applications` and do not inflate adherence score.
- No Telegram/Hermes/OpenClaw gateway code is included in the backend image.

## 0.1.2

### Changed

- Changed the supported Python runtime target to Python >=3.11,<4.0.
- Updated the container base image to `python:3.11-slim`.
- Added a dedicated Runtime Requirements section to the README.

### Notes

- This release does not intentionally change the API, authentication behavior, database schema, or treatment data model.

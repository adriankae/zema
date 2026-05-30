# Zema Changelog: v0.6.4

Release target: `v0.6.4`

## Changed

- Simplified the Telegram setup wizard so the user sees only the currently relevant step.
- Removed the redundant Enable Bot step from the standard setup path.
- Enabled the Telegram bot automatically after a chat is selected.
- Moved `zema-telegram` into the default Docker Compose stack so the runtime starts with normal `docker compose up -d --build`.
- Updated Telegram Docker docs and helper scripts to stop using the old `telegram` Compose profile.
- Updated the dashboard version tag to `v0.6.4`.

## Added

- Sends a Telegram confirmation message after setup succeeds: `Zema is connected. Send /menu to start using the bot.`
- Added tests for automatic Telegram enablement and default `zema-telegram` Compose inclusion.

## Fixed

- Fixed setups getting stuck at `Starting bot` when only `zema-be` and Postgres were running.

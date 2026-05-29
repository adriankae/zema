# Zema

Zema is a private eczema taper tracker. It tells you which body locations need treatment today, which ones are coming up next, and whether you stayed on schedule.

It is for people who have to follow a topical steroid taper and do not want to keep the plan in their head, in scattered notes, or in a generic habit app. Each location gets its own schedule, photo, phase, history, and adherence view.

Zema does not decide your treatment plan and does not replace medical advice. It helps you follow the plan you already have.

## What You Get

Open Zema and you get one practical dashboard:

- **Due Now** shows only the locations that need action.
- **Upcoming** shows what is next, so tomorrow does not surprise you.
- **Location cards** keep the photo, phase, last phase change, next phase change, and location-specific adherence together.
- **Adherence** shows missed, due, not-due, and completed days over week, month, year, all time, or a custom range.
- **Privacy mode** masks location names when someone else can see your screen.
- **Optional Telegram** can remind you and let you log from your phone.

The feeling Zema is aiming for is: "I know exactly what to do now, and I can trust the schedule."

## Is This For Me?

Zema is a good fit if:

- You are tracking eczema treatment across more than one body location.
- Different locations can be in different taper phases.
- Phase 1 can require morning and evening applications.
- Later phases are less frequent and easy to forget.
- You want a local, self-hosted record instead of a cloud habit app.

Zema is probably not the right fit if:

- You want a hosted app with no setup.
- You only need a simple daily checkbox.
- You want Zema to recommend medication, dosing, or a medical plan.

## Fastest Way To Try It

You need Docker Desktop, Docker Compose, and a terminal. If those words are unfamiliar, ask a technical friend to do this first setup with you; after it is running, day-to-day use is in the browser.

```bash
git clone https://github.com/adriankae/zema.git
cd zema
docker compose up -d postgres zema-be
curl -sS http://localhost:28173/health
```

If the health command prints `{"status":"ok"}`, open:

```text
http://localhost:28173/dashboard
```

Use the local default login:

```text
username: admin
password: admin
```

Change this password before using Zema for real.

## First 10 Minutes

1. Go to `Settings -> Account` and change `admin/admin`.
2. Go to `Settings -> Subject` and name the person being tracked.
3. Go to `Settings -> Add Location`.
4. Add a display name like `right foot`, `left hand`, or `neck`.
5. Add a photo if it helps you recognize the location quickly.
6. Return to `Overview`.

The location starts in phase 1 automatically. If it is due, it appears in `Due Now`.

Use the main actions like this:

- **Log** after you apply treatment to one location.
- **Log all locations** when you treated everything currently due.
- **Healed** when a phase-1 location is ready to start tapering.
- **Relapsed** when a tapering location needs to restart.
- **Undo** when the last dashboard action was a mistake.

## Daily Use

Most days should be simple:

1. Open `/dashboard`.
2. Check `Due Now`.
3. Treat those locations.
4. Click `Log` or `Log all locations`.
5. Ignore locations that are not due.

When you want context, click the `i` button next to a location. The info card shows the last phase change, next phase change, and adherence for only that location. You can switch the graph between week, month, year, and all time. Clicking a day in the graph shows what was logged and what was missed; missed treatments can be backfilled there.

## How The Taper Model Works

Zema tracks each body location separately.

- A new location starts in phase 1.
- Phase 1 can have morning and evening treatment slots.
- When you mark a location as healed, it enters tapering phases.
- Later phases are spaced farther apart.
- A relapse resets that location without changing the others.
- Zema catches up phase state after restarts, so the dashboard does not depend on the app being open all the time.

This is why a normal habit tracker is a poor fit: one person can have several active locations, each with a different phase and next due date.

## Optional: Telegram

The dashboard is the best place to start. Add Telegram later if you want phone reminders and button-based logging.

In the dashboard, go to `Settings -> Telegram`. The setup wizard allows one bot per Zema user. After setup, the page shows `Bot active`, the bot handle, and a reset button.

Telegram is optional. Zema works without it.

## What Runs On Your Machine

Zema is self-hosted:

- `zema-be` is the web app and backend.
- `postgres` stores treatment history, phase state, adherence, and account data.
- `zema-cli` is optional for command-line use.
- `zema-telegram` is optional for the Telegram bot.

Your local Docker volumes keep the database and location images. Do not run `docker compose down -v` unless you want to delete that data.

## Safety Checklist Before Real Use

- Change the default `admin/admin` login.
- Use a private machine or private server.
- Keep `.env`, API keys, Telegram tokens, and backups private.
- Back up the Postgres volume and location image volume.
- Do not expose the backend publicly without TLS, authentication, and reverse-proxy hardening.

## Technical Reference

The rest of this README is for installation, deployment, CLI/API usage, Telegram setup, development, and troubleshooting.

## Product Surfaces

- **Dashboard**: recommended first interface for browser-based use.
- **Telegram bot**: optional reminders and logging from allowlisted chats.
- **CLI/API**: scriptable commands and JSON output for power users, agents, and automation.

## Architecture

```text
Dashboard / User / Agent / Telegram / Hermes / OpenClaw
        |
        v
zema-be FastAPI backend
        |
        v
PostgreSQL
```

CLI and Telegram traffic uses the same backend:

```text
zema / czm CLI or zema-cli container
        |
        v
zema-be FastAPI backend
        |
        v
PostgreSQL
```

- `zema-be` is the FastAPI backend service.
- `zema-cli` is the CLI/agent runtime service.
- `postgres` is the canonical datastore.
- Docker Compose keeps the backend and CLI/agent runtime in separate services.
- The backend remains the source of truth for treatment, phase, due, and adherence logic.
- The CLI calls the backend over HTTP.
- Gateway code for Telegram, Hermes, OpenClaw, or similar tools should not run inside `zema-be`.

## Repository Layout

```text
app/                 Backend API, domain services, models, scheduler
alembic/             Database migrations
tests/               Backend tests
cli/                 Separate CLI package
cli/docs/            CLI-specific docs
cli/skills/          Agent Skills package
docker/              Backend and CLI Dockerfiles
docker-compose.yml   Local postgres, zema-be, and profiled zema-cli services
```

The CLI package is still named `czm-cli` and its internal Python package is still under `cli/src/czm_cli`. The public command name is `zema`, with `czm` kept as a compatibility alias.

## Feature Summary

- Account-scoped authentication with username/password login, JWT access tokens, and hashed API keys.
- Server-rendered dashboard with login, CSRF protection, dark mode, real settings tabs, thumbnails, and treatment actions.
- Subject and body-location management.
- Location image upload, replacement, fallback display, and deletion.
- Eczema episode lifecycle tracking.
- Dashboard heal and relapse actions.
- Taper protocol phases with phase history.
- Treatment application logging, editing, voiding, deleting, and listing.
- Dashboard `Log`, `Log all locations`, and undo for dashboard actions.
- Operational due reminders through `/episodes/due`.
- Event history and timelines.
- Daily adherence calculation and persisted audit snapshots.
- Adherence dashboard ranges with hover dates, pre-start days, due today vs missed past days, and not-due completion handling.
- Privacy mode for masked location names.
- Location info cards with phase changes, location-specific adherence, and missed-treatment backfill.
- Optional dashboard Telegram setup wizard for one bot per user.
- In-process scheduler for phase progression.
- Dockerized backend, PostgreSQL, and separate CLI/agent runtime.

Runtime requirements:

- Backend Python: `>=3.11,<4.0`
- CLI Python: `>=3.11`; package metadata lists Python 3.11 and 3.12 support
- Docker images: `python:3.11-slim`
- PostgreSQL required when running outside Docker

## Docker Quickstart

Start PostgreSQL and the backend:

```bash
docker compose up -d postgres zema-be
```

Check the services:

```bash
docker compose ps
docker compose logs --tail=100 zema-be
curl -sS http://localhost:28173/health
```

Expected health response:

```json
{"status":"ok"}
```

Open the dashboard:

```text
http://localhost:28173/dashboard
```

Local default login:

```text
username: admin
password: admin
```

The backend API is available at:

```text
http://localhost:28173
```

Run the CLI container:

```bash
docker compose run --rm zema-cli zema --help
```

Authenticated CLI container examples:

```bash
docker compose run --rm -e CZM_API_KEY="$CZM_API_KEY" zema-cli zema due list --json
docker compose run --rm -e CZM_API_KEY="$CZM_API_KEY" zema-cli zema adherence summary --last 30 --json
```

Inside Docker Compose, `zema-cli` uses:

```text
CZM_BASE_URL=http://zema-be:28173
```

Run the Telegram bot as a separate profiled service:

```bash
export CZM_API_KEY="..."
export ZEMA_TELEGRAM_BOT_TOKEN="..."
export ZEMA_TELEGRAM_ALLOWED_CHAT_IDS="123456789"

docker compose --profile telegram up -d zema-telegram
docker compose logs -f zema-telegram
```

`zema-telegram` uses the CLI image, talks to `zema-be` over HTTP, and exposes no public ports.

## Persistent Docker Deployment

For a server that should survive reboots, run the Compose stack from a stable directory and keep secrets in a private `.env` file.

Create a persistent server directory:

```bash
sudo mkdir -p /srv/zema
sudo chown -R czmbot:czmbot /srv/zema
sudo -iu czmbot
cd /srv/zema
git clone https://github.com/adriankae/zema.git
cd zema
```

Create `.env` from the placeholder file:

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

Fill in at least:

```env
CZM_API_KEY=replace-with-your-zema-api-key
CZM_TIMEZONE=Europe/Berlin
ZEMA_TELEGRAM_BOT_TOKEN=replace-with-your-telegram-bot-token
ZEMA_TELEGRAM_ALLOWED_CHAT_IDS=123456789
ZEMA_TELEGRAM_ALLOWED_USER_IDS=
ZEMA_TELEGRAM_ALLOW_WRITES=true
ZEMA_TELEGRAM_ALLOW_ADHERENCE_REBUILD=false
```

Docker Compose uses `CZM_TIMEZONE` for both backend due-slot logic and Telegram/CLI runtime behavior. For phase-1 AM/PM due checks, `zema-be` receives this value as `DEPLOYMENT_TIMEZONE`.

Start the persistent backend and Telegram bot:

```bash
docker compose --profile telegram up -d postgres zema-be zema-telegram
```

Inspect the stack:

```bash
docker compose ps
docker compose logs --tail=100 zema-be
docker compose logs --tail=100 zema-telegram
curl -sS http://localhost:28173/health
```

Ensure Docker starts on boot:

```bash
sudo systemctl enable docker
sudo systemctl status docker
```

Reboot test:

```bash
sudo reboot
```

After reconnecting:

```bash
cd /srv/zema/zema
docker compose ps
docker compose logs --tail=100 zema-telegram
curl -sS http://localhost:28173/health
```

The long-running services use `restart: unless-stopped`, so Docker restarts them after reboot as long as Docker itself starts. Data persists in named Docker volumes:

```text
zema-postgres-data
zema-location-images
```

Keep `.env` private. It contains secrets and should not be committed. Back up `.env`, the Postgres volume, and the location image volume. `docker compose down` stops containers but keeps named volumes; `docker compose down -v` deletes named volumes and destroys database/image data.

## Updating Zema

For a normal Docker Compose install, update from the repository root:

```bash
scripts/update.sh
```

The script:

1. Refuses to run if tracked local files have uncommitted changes.
2. Exports a JSON backup first when `CZM_API_KEY` is available in the environment or `.env`.
3. Fetches `origin`.
4. Fast-forwards to `origin/main`.
5. Rebuilds and restarts Docker Compose.
6. Waits for `/health` to pass.

If you already made a manual backup, or you cannot provide an API key, you can explicitly skip the backup step:

```bash
scripts/update.sh --skip-backup
```

To update to a specific tag or branch:

```bash
scripts/update.sh --ref v0.6.0
scripts/update.sh --ref origin/main
```

If you expose Zema through a reverse proxy on the same machine, keep the default local bind:

```env
ZEMA_HOST_BIND=127.0.0.1
```

If another device on your private network must reach Zema directly, set this intentionally before restarting:

```env
ZEMA_HOST_BIND=0.0.0.0
```

## Authentication And API Keys

The local Docker Compose setup seeds a default account when the database is empty:

```text
username: admin
password: admin
```

Override these with:

```text
INITIAL_USERNAME
INITIAL_PASSWORD
```

Create an API key manually:

```bash
export CZM_BASE_URL="http://localhost:28173"

export ACCESS_TOKEN="$(
  curl -sS "$CZM_BASE_URL/auth/login" \
    -H 'Content-Type: application/json' \
    -d '{"username":"admin","password":"admin"}' \
  | jq -r '.access_token'
)"

export CZM_API_KEY="$(
  curl -sS "$CZM_BASE_URL/api-keys" \
    -H "Authorization: Bearer $ACCESS_TOKEN" \
    -H 'Content-Type: application/json' \
    -d '{"name":"zema-cli"}' \
  | jq -r '.plaintext_key'
)"
```

Verify the API key:

```bash
curl -sS "$CZM_BASE_URL/auth/me" \
  -H "X-API-Key: $CZM_API_KEY"
```

The CLI can also create its config automatically with `zema setup`:

```bash
zema setup \
  --username admin \
  --password admin \
  --api-key-name zema-cli \
  --timezone Europe/Berlin \
  --base-url http://localhost:28173
```

`zema setup` logs in, creates an API key, and writes a config file under `~/.config/czm/config.toml` or `$XDG_CONFIG_HOME/czm/config.toml`.

## CLI

Install the CLI from the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e cli
zema --help
czm --help
zema adherence --help
```

If your pip index is unreachable, use PyPI explicitly:

```bash
PIP_INDEX_URL=https://pypi.org/simple python3 -m pip install -e cli
```

If `zema` is installed into a user bin directory that is not on `PATH`, activate the virtual environment or add the pip scripts directory shown by pip to your `PATH`.

CLI configuration precedence is:

```text
CLI flags > CZM_* environment variables > config file
```

The CLI uses these environment variables:

```text
CZM_BASE_URL
CZM_API_KEY
CZM_TIMEZONE
```

The default base URL is:

```text
http://localhost:28173
```

See the detailed CLI docs in [`cli/docs/`](cli/docs/).

## Common Workflows

After the backend is running and the CLI has an API key:

```bash
zema subject create --display-name "Child A"
zema location create --code left_elbow --display-name "Left elbow"
zema location image set left_elbow ./left-elbow.jpg
zema episode create --subject "Child A" --location left_elbow
zema application log --episode 1
zema due list
zema events list --episode 1
```

Notes:

- `zema application log --episode 1` records a minimal application.
- If omitted, `treatment_type` defaults to `other`.
- Optional application fields include `--applied-at`, `--treatment-type`, `--treatment-name`, `--quantity-text`, and `--notes`.
- Location images are optional and can be added during creation or later with `zema location image set`.
- Subject and location references may be numeric IDs or resolvable names/codes.

Location image examples:

```bash
zema location create --code left_elbow --display-name "Left elbow" --image ./left-elbow.jpg
zema location image set left_elbow ./left-elbow.jpg
zema location image get left_elbow --output ./left-elbow.jpg
zema location image remove left_elbow
```

## Telegram Bot

Zema 0.3.0 includes a Telegram frontend that runs outside the backend container:

```text
Telegram
   |
   v
zema telegram run / zema-telegram
   |
   v
zema-be
   |
   v
PostgreSQL
```

The Telegram runtime uses explicit handlers and the same backend HTTP client layer as the CLI. It does not execute shell commands, does not support arbitrary `/zema ...` passthrough, and does not run inside `zema-be`.

Local setup:

```bash
zema setup telegram
zema telegram test
zema telegram run
```

Non-interactive setup:

```bash
zema setup telegram \
  --base-url http://localhost:28173 \
  --api-key "$CZM_API_KEY" \
  --bot-token "$ZEMA_TELEGRAM_BOT_TOKEN" \
  --allowed-chat-id 123456789 \
  --timezone Europe/Berlin \
  --allow-writes \
  --yes
```

Setup notes:

- Create a Telegram bot token with BotFather.
- Send `/start` to the bot during setup so Zema can discover chat IDs with Telegram `getUpdates`.
- Config remains under `~/.config/czm/config.toml` or `$XDG_CONFIG_HOME/czm/config.toml`.
- Writes are enabled by default for allowlisted chats/users.
- Adherence rebuild remains disabled by default and must be explicitly enabled.
- Morning and evening reminders are enabled by default for newly created Telegram configs.

The primary Telegram UX is button-driven. Zema registers a Telegram command menu at runtime, shows inline menus on `/start` and `/menu`, and uses a persistent reply keyboard in private chats so common actions stay tappable without remembering slash commands. Group chats keep the quieter inline menu behavior.

The menu includes:

```text
[Start episode]   [Due now]
[Adherence]       [Heal episode]
[Relapse episode] [Locations]
[Subjects]
```

Guided workflows include:

- Start episode with subject/location selection or creation.
- Create subject.
- Delete subject when it has no related episodes.
- Create location.
- Set or replace a location image by sending a Telegram photo.
- Log due treatment.
- Heal episode.
- Relapse episode.
- View adherence summary, calendar, missed days, and Telegram heatmap images for summary ranges.
- Rebuild adherence snapshots when `allow_adherence_rebuild=true`.

Reminder behavior:

- `zema telegram run` schedules reminders in the Telegram runtime, not in `zema-be`.
- Morning reminders default to `07:00`; evening reminders default to `19:00`.
- Reminder times use `telegram.reminders.timezone`, falling back to the CLI timezone.
- Reminders use `/episodes/due` as the backend source of truth.
- Reminder prompts include location images from `GET /locations/{location_id}/image` when configured and available.
- Reminder prompts include `Log application` only when `allow_writes=true`.
- `Snooze` suppresses repeat Telegram reminders in memory for the configured snooze duration; it does not change backend due state and resets on bot restart.

Reminder config commands:

```bash
zema telegram config reminders show
zema telegram config reminders enable
zema telegram config reminders disable
zema telegram config reminders set-morning 07:00
zema telegram config reminders set-evening 19:00
zema telegram config reminders set-snooze 30
zema telegram config reminders images true
```

Typed slash commands remain available for power users:

```text
/start
/menu
/help
/status
/subjects
/subject_create Child A
/locations
/location_create left_elbow Left elbow
/location_image_set left_elbow
/episodes
/episode 12
/episode_create subject:"Child A" location:left_elbow
/due
/log episode:12
/events episode:12
/timeline episode:12
/adherence 30
/adherence_calendar episode:12 days:30
/adherence_missed episode:12 days:30
/adherence_rebuild episode:12 from:2026-04-01 to:2026-04-30
```

Telegram security:

- At least one allowed chat ID is required.
- Optional allowed user IDs can further restrict access.
- Unknown chats/users are rejected before backend calls.
- Write actions require `allow_writes=true`.
- Adherence rebuild requires `allow_adherence_rebuild=true`.
- Secrets are masked in config display.
- Do not commit Telegram bot tokens or Zema API keys.
- Do not bake secrets into Docker images.

Telegram limitations:

- Conversation state is in-memory and resets on bot restart.
- Reminder snooze state is in-memory and resets on bot restart.
- Webhook mode is not implemented.
- There is no LLM or natural-language mode.
- There is no arbitrary CLI passthrough.
- Rich episode labels depend on fields returned by backend episode endpoints.

## Adherence Tracking

Adherence is exposed through backend APIs and `zema adherence ...` commands.

Dynamic adherence:

- Is the default GET behavior.
- Is read-only.
- Is calculated live from phase history, taper protocol, and valid applications.
- Does not write rows.

Persisted adherence:

- Is stored in `episode_daily_adherence`.
- Is returned when `persisted=true` or `--persisted` is used.
- Reads stored rows only.
- May be empty before a rebuild has persisted snapshots.

Rebuild:

- `POST /adherence/rebuild` and `zema adherence rebuild` persist or update rows.
- CLI rebuild requires `--from` and `--to`.
- Rebuild without `episode_id` rebuilds active, non-obsolete episodes only.
- Broad all-episode rebuild with `active_only=false` is intentionally rejected in v1.

Schedule and scoring:

- Adherence snapshots use a fixed phase-start schedule for auditability.
- `/episodes/due` remains separate operational due/reminder logic.
- `completed_applications` is the raw valid logged application count for a day.
- `credited_applications = min(completed_applications, expected_applications)`.
- Score is `sum(credited_applications) / sum(expected_applications)`.
- If there are no expected applications, `adherence_score` is `null`.
- Telegram summary buttons also send a heatmap image: columns are dates, rows are location-first episode labels, colors represent completed/partial/missed/not-due/future, and 7/30 day views annotate cells as credited/expected.

Examples:

```bash
zema adherence summary --episode 1 --last 30 --json
zema adherence calendar --episode 1 --last 30
zema adherence missed --episode 1 --last 30 --include-partial
zema adherence rebuild --episode 1 --from 2026-04-01 --to 2026-04-30 --json
zema adherence summary --episode 1 --last 30 --persisted --json
```

## Backend API

Interactive FastAPI docs are available when the backend is running:

```text
http://localhost:28173/docs
```

Endpoint groups:

```text
GET /health

POST /auth/login
GET /auth/me

POST /api-keys
GET /api-keys
POST /api-keys/{api_key_id}/revoke

POST /subjects
GET /subjects
GET /subjects/{subject_id}

POST /locations
GET /locations
POST /locations/{location_id}/image
GET /locations/{location_id}/image
DELETE /locations/{location_id}/image

POST /episodes
GET /episodes
GET /episodes/{episode_id}
POST /episodes/{episode_id}/heal
POST /episodes/{episode_id}/relapse
POST /episodes/{episode_id}/advance
GET /episodes/due

POST /applications
PATCH /applications/{application_id}
DELETE /applications/{application_id}
POST /applications/{application_id}/void
GET /episodes/{episode_id}/applications

GET /episodes/{episode_id}/events
GET /episodes/{episode_id}/timeline

GET /adherence/calendar
GET /adherence/summary
GET /adherence/missed
GET /episodes/{episode_id}/adherence
POST /adherence/rebuild
```

Authenticated API requests can use either:

```text
Authorization: Bearer <jwt-access-token>
X-API-Key: <api-key>
```

## Local Development

Install backend dependencies from the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[dev]"
```

Start dependencies with Docker:

```bash
docker compose up -d postgres
```

Run migrations:

```bash
python3 -m alembic upgrade head
```

Run the backend locally:

```bash
python3 -m app.server
```

For most local manual testing, the Docker Quickstart is simpler because it starts PostgreSQL and `zema-be` with the expected environment.

## Testing

Backend tests:

```bash
python3 -m pytest tests
python3 -m pytest tests/test_location_images.py
python3 -m pytest tests/test_adherence.py
python3 -m pytest tests/test_adherence_api.py
```

CLI tests:

```bash
python3 -m pytest cli/tests
python3 -m pytest cli/tests/test_adherence_cli.py
```

## Database Migrations

Run migrations manually:

```bash
python3 -m alembic upgrade head
```

The `zema-be` Docker image runs this automatically on startup:

```bash
alembic upgrade head && python -m app.server
```

Current migrations include the initial schema, `episode_daily_adherence`, and location image metadata.

## Configuration

Backend environment variables:

```text
DATABASE_URL
APP_ENV
DEPLOYMENT_TIMEZONE
APP_PORT
ENABLE_SCHEDULER
JWT_SECRET
INITIAL_USERNAME
INITIAL_PASSWORD
LOCATION_IMAGE_DIR
LOCATION_IMAGE_MAX_BYTES
```

Docker Compose defaults:

```text
DATABASE_URL=postgresql+psycopg://eczema:eczema@postgres:5432/eczema
APP_ENV=local
DEPLOYMENT_TIMEZONE=${CZM_TIMEZONE:-Europe/Berlin}
APP_PORT=28173
ENABLE_SCHEDULER=true
JWT_SECRET=change-me-in-production
INITIAL_USERNAME=admin
INITIAL_PASSWORD=admin
LOCATION_IMAGE_DIR=/data/location-images
LOCATION_IMAGE_MAX_BYTES=5242880
```

Location images are stored on the `zema-be` filesystem under `LOCATION_IMAGE_DIR`. Docker Compose mounts a named volume at `/data/location-images` so uploaded images survive container restarts.

CLI environment variables:

```text
CZM_BASE_URL
CZM_API_KEY
CZM_TIMEZONE
ZEMA_TELEGRAM_BOT_TOKEN
ZEMA_TELEGRAM_ALLOWED_CHAT_IDS
ZEMA_TELEGRAM_ALLOWED_USER_IDS
ZEMA_TELEGRAM_ALLOW_WRITES
ZEMA_TELEGRAM_ALLOW_ADHERENCE_REBUILD
ZEMA_TELEGRAM_REMINDERS_ENABLED
ZEMA_TELEGRAM_REMINDER_MORNING_TIME
ZEMA_TELEGRAM_REMINDER_EVENING_TIME
ZEMA_TELEGRAM_REMINDER_SNOOZE_MINUTES
ZEMA_TELEGRAM_REMINDER_SEND_IMAGES
```

CLI config file locations:

```text
~/.config/czm/config.toml
$XDG_CONFIG_HOME/czm/config.toml
```

Example CLI config:

```toml
base_url = "http://localhost:28173"
api_key = "your-api-key"
timezone = "Europe/Berlin"

[telegram]
bot_token = "123456:telegram-token"
allowed_chat_ids = [123456789]
allowed_user_ids = []
allow_writes = true
allow_adherence_rebuild = false
default_subject = ""
default_location = ""
command_mode = "buttons"

[telegram.reminders]
enabled = true
morning_time = "07:00"
evening_time = "19:00"
timezone = "Europe/Berlin"
send_location_images = true
snooze_minutes = 30
```

Telegram setup/config and typed slash-command runtime:

```bash
zema setup telegram --help
zema telegram status
zema telegram test
zema telegram config show
zema telegram config reminders show
zema telegram run
zema config show
```

Secrets are masked by default in config display. Use `--show-secrets` only in a trusted local terminal.

## Agent / Telegram / Hermes / OpenClaw Integration

Agent and gateway integrations should call `zema` or `czm` externally, or run the `zema-cli` container as a tool.

Recommended agent pattern:

```bash
zema --json due list
zema --json adherence summary --last 30
zema --json application log --episode 1
```

Do not place Telegram, Hermes, OpenClaw, or other gateway code inside the `zema-be` backend image. Keep the backend focused on API, persistence, and domain logic.

The repository includes an Agent Skills package under:

```text
cli/skills/czm/
```

Manual Telegram smoke test:

```bash
docker compose up -d postgres zema-be
zema setup telegram
zema telegram test
zema telegram run
```

Docker Telegram smoke test:

```bash
docker compose --profile telegram up -d zema-telegram
docker compose logs -f zema-telegram
```

In Telegram, test `/start`, `/menu`, `/due`, `/adherence 30`, and the main menu buttons.

## Troubleshooting

`python` not found:

```bash
python3 --version
```

Pip index problems:

```bash
PIP_INDEX_URL=https://pypi.org/simple python3 -m pip install -e cli
```

`zema` not on `PATH`:

```bash
source .venv/bin/activate
.venv/bin/zema --help
```

Port `28173` already in use:

```bash
lsof -i :28173
```

Docker buildx warning:

- Docker Compose may warn that buildx is not installed.
- If the image still builds, you can continue.
- If builds fail, update Docker Desktop or install the buildx plugin.

Backend not ready:

```bash
docker compose ps
docker compose logs --tail=100 zema-be
curl -sS http://localhost:28173/health
```

Missing or invalid `CZM_API_KEY`:

- Run `zema setup`, or recreate an API key through `/auth/login` and `/api-keys`.
- Remember that the CLI uses `X-API-Key`, not the JWT bearer token.

Telegram bot does not answer:

- Confirm `ZEMA_TELEGRAM_BOT_TOKEN` is valid with `zema telegram test`.
- Confirm `ZEMA_TELEGRAM_ALLOWED_CHAT_IDS` includes the chat you are using.
- Send `/start` to the bot after changing tokens or allowlists.
- Check `docker compose logs -f zema-telegram` when using Docker.

Persisted adherence is empty:

- This is expected before `zema adherence rebuild`.
- Dynamic adherence remains available without persisted rows.

No adherence rows:

- Requested dates must be covered by episode phase history.
- A newly created episode usually has phase history starting on its creation date.

Wrong checkout:

```bash
test -d cli && test -f app/adherence.py && test -f docker/api.Dockerfile && test -f docker/cli.Dockerfile && echo "integrated checkout"
```

## Security Notes

- Change the default `admin/admin` credentials.
- Change `JWT_SECRET`.
- Do not commit API keys.
- Do not bake secrets into Docker images.
- Use environment variables or secret management for deployments.
- Do not expose `zema-be` publicly without TLS, authentication, and reverse-proxy hardening.

## Versioning / Changelog

The backend package version is tracked in `pyproject.toml`.

The CLI package is separate under `cli/pyproject.toml`.

See [`CHANGELOG.md`](CHANGELOG.md) for release notes.

## Roadmap / Non-Goals

- Telegram, Hermes, and OpenClaw gateway code is not included inside `zema-be`.
- The CLI does not own or duplicate business logic.
- The internal Python package rename from `czm_cli` to `zema` has not been done.
- `/episodes/due` is operational reminder logic, not historical adherence auditing.
- The project intentionally avoids GraphQL, Celery, Kafka, external workers, CQRS, and event sourcing.

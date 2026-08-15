# Eczema Treatment Tracker — Product Specification

Version: 1.0
Status: Normative
Scope: First production-ready version
Target: Codex implementation in one pass
Priority: Keep v1 simple, correct, and self-hostable

---

## 1. Goal

Build a self-hosted backend product for tracking eczema episodes per body location, managing treatment phase transitions, and exposing the system via a PostgreSQL-backed API server.

This first version must include:

* PostgreSQL database
* API server
* Dockerized local deployment
* automatic taper phase progression on the backend
* simple authentication
* support for multiple subjects under one authenticated account

This first version must **not** include:

* family model
* notifications
* CLI implementation
* agent wrapper implementation
* analytics
* image uploads
* advanced audit/correction workflows
* advanced protocol management

The product should be structured so that a CLI and agents can be added later.

---

## 2. Core Product Concept

A single authenticated account can manage several tracked subjects/patients.

For each subject, the system tracks eczema per body location.

At any given time, for a given subject and location:

* either there is no active/tapering episode
* or there is exactly one episode

Each episode goes through a deterministic treatment lifecycle:

* active flare
* healed
* taper phase 2
* taper phase 3
* taper phase 4
* taper phase 5
* taper phase 6
* taper phase 7
* obsolete

Relapse resets the episode to active flare.

---

## 3. Primary Use Cases

The first version must support these workflows:

1. account owner logs in
2. account owner creates one or more subjects
3. account owner creates body locations
4. account owner creates an episode for a subject at a location
5. account owner logs treatment applications
6. account owner marks episode as healed
7. system automatically advances through taper phases based on calendar rules
8. account owner reports relapse, which resets episode to active flare
9. system marks episode obsolete after phase 7 completes
10. account owner views episode state, applications, and event timeline
11. CLI and agents can later use the same API

---

## 4. Scope of v1

## 4.1 In scope

* PostgreSQL schema
* DB migrations
* FastAPI API server
* username/password authentication
* API key support for CLI/agent usage
* account model
* subject model
* body location model
* eczema episode model
* treatment application model
* basic event log
* dynamic and persisted historical Adherence with missed/partial-day reporting
* automatic backend phase progression
* Docker Compose setup
* initial protocol seeding
* simple tests

---

## 4.2 Out of scope

* family model
* protocol editing UI/API
* multiple protocols
* advanced audit workflows
* client reminders
* image/photo uploads
* food tracking
* gamification
* advanced RBAC
* subject sharing across accounts
* CLI implementation
* agent implementation

---

## 5. Technical Stack

Use this stack:

* **Backend language:** Python >=3.11,<4.0
* **API framework:** FastAPI
* **Database:** PostgreSQL
* **ORM / DB access:** SQLAlchemy 2.x
* **Migrations:** Alembic
* **Containerization:** Docker + Docker Compose

The backend must run in Docker containers.

---

## 6. Authentication and Authorization

## 6.1 Account model

A **user** is an authenticated actor.

Each account can create and manage multiple subjects.

There is no family model in v1.

---

## 6.2 Authentication

Support two authentication methods:

### A. Username/password

For human account login.

### B. API key

For CLI and agent usage.

Each API key belongs to exactly one:

* account
* or service/agent identity associated with one account

The simplest acceptable v1 implementation is:

* account login endpoint returning bearer token
* API key table for programmatic access
* bearer token auth for user requests
* API key auth for service/agent requests

---

## 6.3 Authorization

Keep authorization simple in v1:

* an authenticated actor can do anything within their own account scope
* an actor can manage all subjects belonging to their account
* an actor cannot access data belonging to another account

No finer-grained permissions are needed in v1.

---

## 7. Domain Model

## 7.1 Account

Authenticated owner of the data.

An account can:

* log in
* create subjects
* create body locations
* create/manage episodes
* create/manage applications
* create/manage API keys

---

## 7.2 Subject

A person/patient tracked by the account.

Examples:

* self
* child
* spouse

Subjects belong to exactly one account in v1.

---

## 7.3 Body Location

A normalized body location defined by the account.

Examples:

* left_elbow
* right_elbow
* neck
* glabella_ossis_frontalis

Rules:

* body locations are account-owned
* body locations are global across that account’s subjects
* hierarchy is not required
* versioning is not required
* bilateral areas are separate locations
* one episode references exactly one location

The body location table starts blank for each account.
The account must create its own locations.

---

## 7.4 Eczema Episode

An eczema episode is the tracked treatment lifecycle for one subject at one body location.

Rules:

* one subject + one location may not have multiple simultaneous active/tapering episodes
* obsolete episodes may be reactivated in the simplest possible way
* for v1, the exact distinction between “same episode” and “new recurrence” is not important
* the important product question is: is there an episode for this location or not

For v1, it is acceptable to reactivate an obsolete episode instead of creating a new historical recurrence model.

---

## 7.5 Treatment Application

A logged treatment application.

Rules:

* belongs to one episode
* has one timestamp
* only one application may exist per exact timestamp per episode
* medication name is optional
* amount/unit is optional
* free-text notes are optional
* application can be edited
* application can be deleted
* audit-grade correction workflow is not required in v1

---

## 7.6 Event Log

The system includes a simple event log in v1.

Purpose:

* timeline
* debugging
* explainability
* future agent support

This is **not** a strict full audit system for v1.

Events are useful history, but advanced audit policy is deferred.

---

## 8. Treatment Protocol

There is one active protocol in v1.

No protocol version management UI/API is required.

Treatment Protocol v1 is canonical in code. The `taper_protocol_phases` rows are a validated persistent mirror; bootstrap seeds them from the canonical values only when the mirror is empty, and validation does not repair a nonempty mismatch.

### Phase schedule

| Phase |   Duration | Frequency    |
| ----- | ---------: | ------------ |
| 1     | open-ended | 2x daily     |
| 2     |    28 days | every 2 days |
| 3     |    14 days | every 3 days |
| 4     |    14 days | every 4 days |
| 5     |    14 days | every 5 days |
| 6     |    14 days | every 6 days |
| 7     |    14 days | every 7 days |

Rules:

* phase 1 = active flare
* phase 2 starts immediately when user marks healed
* next due application occurs two calendar days later
* when phase 7 completes, episode becomes obsolete

---

## 9. Time and Calendar Semantics

This is important and must be implemented exactly.

## 9.1 Deployment timezone

* single global timezone per deployment

The deployment timezone must be configured in backend settings.

---

## 9.2 Storage timezone

* all timestamps are stored in UTC in PostgreSQL

---

## 9.3 Calendar behavior

Phase transitions and schedule dates use **deployment-local calendar-day semantics**, not fixed elapsed-hour intervals. Timezone-aware instants are stored in UTC and converted through the deployment timezone, so local boundaries remain safe across DST changes.

Rules:

* phase transitions use deployment-local calendar-day boundaries
* Phase 1 has local Treatment Slots: Morning is `[00:00, 14:00)` and Evening is `[14:00, next local midnight)`
* exact local 14:00 is Evening, and a partial first Phase 1 day exposes only the remaining slots
* taper expected dates use local calendar-day cadence; an application's time within that local date does not change its date credit once the application is valid
* deleted, voided, pre-phase, future, and phase-foreign Applications do not satisfy slots or move rolling schedules
* Due State is operational and rolling; Adherence is historical and auditable, and the two strategies share canonical primitives without being unified

---

## 9.4 Rolling taper anchor rule

For taper phases, the first expected date is the phase start's local calendar date plus the phase cadence. Each credited valid taper Application re-anchors the next expected date by that cadence. A missed scheduled date remains missed; a later valid Application does not retroactively satisfy it.

Example:

If phase 2 starts on April 1 at 18:00 local deployment time:

* the first expected date is April 3
* if a valid Application is credited on April 3, the next expected date is April 5
* if April 3 is missed and a valid Application is credited on April 4, April 3 remains missed and the next expected date is April 6

An Application can satisfy or re-anchor a taper schedule only when it is not deleted or voided, is not before the phase start or future at evaluation time, and has the current phase snapshot. An Application from another phase is phase-foreign and does not count.

This rule must be implemented exactly.

---

## 10. Episode Lifecycle

## 10.1 Status values

Allowed episode statuses in v1:

* `active_flare`
* `in_taper`
* `obsolete`

Do **not** use `relapsed` as a persistent status.
Relapse is an event that resets state back to `active_flare`.

---

## 10.2 Lifecycle

```text
active_flare (phase 1)
→ phase 2
→ phase 3
→ phase 4
→ phase 5
→ phase 6
→ phase 7
→ obsolete
```

Relapse:

```text
phase 2–7
→ active_flare (phase 1)
```

---

## 10.3 Allowed transitions

### Create episode

* enters `active_flare`
* `current_phase_number = 1`

### Mark healed

* allowed only from phase 1
* changes state immediately to phase 2
* `status = in_taper`

### Auto-advance

* applies only to phases 2–7
* runs on backend
* no client-side control required

### Relapse

* allowed from phases 2–7
* resets to phase 1
* `status = active_flare`

### Obsolete

* automatic after phase 7 completes

### Reactivate obsolete

For v1, the simplest implementation is acceptable:

* either reuse/reactivate obsolete episode
* or create a new active one while ensuring only one non-obsolete episode per subject/location

Implementation choice must remain simple and consistent.

Recommended simple rule for v1:

* if an obsolete episode exists for the same subject/location and a new create request is made, reactivate it in phase 1 instead of creating a second historical record

---

## 11. Phase Transition Rules

## 11.1 Manual transitions

Only these transitions are manual in v1:

* active flare → healed/phase 2
* relapse → phase 1 reset

There is also a manual API endpoint to mark healed.

---

## 11.2 Automatic transitions

The backend service must automatically advance:

* phase 2 → phase 3
* phase 3 → phase 4
* phase 4 → phase 5
* phase 5 → phase 6
* phase 6 → phase 7
* phase 7 → obsolete

This must happen in the backend service, not in the client.

---

## 11.3 Background scheduler

The backend must include a scheduler/job mechanism that periodically evaluates episodes and advances them when due.

This is part of v1.

A simple implementation is sufficient.

Recommended behavior:

* periodic scheduler inside backend container
* checks episodes in taper
* compares current local calendar day against expected phase end
* advances qualifying episodes
* scheduler runs once daily at local 00:05 deployment time

No separate worker container is required unless implementation strongly prefers it.

Keep it simple.

---

## 11.4 Manual advance endpoint

Even though auto-advance is the intended mechanism, the API may still expose a simple `/advance` endpoint for operational/debug use.

However, backend logic remains authoritative.

If implemented, the endpoint must obey the same domain rules.

---

## 12. Due Logic

The detailed due-treatment rules may live in a separate due spec.

For this product spec, the required minimum is:

* backend can determine whether an episode is due today
* backend can return next due date/timestamp
* due calculations use canonical phase and Treatment Slot rules and valid Applications
* Phase 1 uses the local half-open Morning/Evening windows; taper phases use rolling local calendar-day dates
* deleted, voided, pre-phase, future, and phase-foreign Applications do not satisfy slots or move rolling schedules
* Due State is operational and rolling; Adherence is historical and auditable but remains a separate strategy

---

## 13. Data Model

The v1 database must include at least these tables:

* `accounts`
* `account_api_keys`
* `subjects`
* `body_locations`
* `eczema_episodes`
* `taper_protocol_phases`
* `episode_phase_history`
* `treatment_applications`
* `episode_events`

No family tables.

---

## 14. Table Requirements

## 14.1 `accounts`

Purpose:

* authenticated users

Minimum fields:

* `id`
* `username` (unique)
* `password_hash`
* `is_active`
* `created_at`
* `updated_at`

---

## 14.2 `account_api_keys`

Purpose:

* API key authentication for CLI/agents

Minimum fields:

* `id`
* `account_id`
* `name`
* `key_hash`
* `is_active`
* `created_at`
* `last_used_at`

Store only hashed API keys, not plaintext.

---

## 14.3 `subjects`

Purpose:

* tracked people/patients per account

Minimum fields:

* `id`
* `account_id`
* `display_name`
* `created_at`
* `updated_at`

Rules:

* subject belongs to one account
* subject names need not be globally unique

---

## 14.4 `body_locations`

Purpose:

* account-defined reusable body locations

Minimum fields:

* `id`
* `account_id`
* `code`
* `display_name`
* `created_at`

Rules:

* unique per account on `code`
* no hierarchy required

---

## 14.5 `eczema_episodes`

Purpose:

* current operational episode state

Minimum fields:

* `id`
* `account_id`
* `subject_id`
* `location_id`
* `status`
* `current_phase_number`
* `phase_started_at`
* `phase_due_end_at`
* `healed_at`
* `obsolete_at`
* `created_at`
* `updated_at`

Rules:

* one non-obsolete episode at a time per subject+location
* status must be one of:

  * `active_flare`
  * `in_taper`
  * `obsolete`

Recommended DB constraint/index:

* unique partial index enforcing at most one episode where status != `obsolete` per `subject_id + location_id`

---

## 14.6 `taper_protocol_phases`

Purpose:

* stores the single active protocol

Minimum fields:

* `phase_number`
* `duration_days`
* `apply_every_n_days`
* `applications_per_day`

Seed with one protocol for phases 1–7.

For v1, this table can be simple and single-protocol.
No versioning required.

---

## 14.7 `episode_phase_history`

Purpose:

* track phase transitions

Minimum fields:

* `id`
* `episode_id`
* `phase_number`
* `started_at`
* `ended_at`
* `reason`
* `created_at`

Allowed reasons for v1:

* `episode_created`
* `healed_marked`
* `auto_advance`
* `relapse`

---

## 14.8 `treatment_applications`

Purpose:

* structured record of medication/cream applications

Minimum fields:

* `id`
* `episode_id`
* `applied_at`
* `treatment_type`
* `treatment_name`
* `quantity_text`
* `phase_number_snapshot`
* `notes`
* `created_at`
* `updated_at`

Optional fields for convenience:

* `is_deleted`
* `deleted_at`

Rules:

* `treatment_type` allowed values:

  * `steroid`
  * `emollient`
  * `other`
* only one application per exact timestamp per episode
* editable
* deletable in v1

Recommended simple v1 deletion strategy:

* soft delete with `is_deleted` + `deleted_at`

This keeps implementation simple and safer than hard delete.

---

## 14.9 `episode_events`

Purpose:

* simple event timeline

Minimum fields:

* `id`
* `event_uuid`
* `episode_id`
* `event_type`
* `actor_type`
* `actor_id`
* `occurred_at`
* `payload`
* `created_at`

Keep event model simple in v1.

Allowed event types:

* `episode_created`
* `healed_marked`
* `phase_entered`
* `relapse_marked`
* `application_logged`
* `application_updated`
* `application_deleted`
* `episode_obsoleted`

Because applications are editable/deletable in v1, the event model should reflect that.

This is enough for timeline/debugging.
Do not over-engineer audit behavior.

---

## 15. Event Model Requirements

Use the simple event model from `event_spec.md`, adapted for v1 simplicity.

### Key rules

* write events for meaningful actions
* keep events append-only in normal operation
* event payloads may remain small
* events are for timeline/debugging/explainability
* advanced audit policy is deferred

### Required write behavior

Emit these events at minimum:

* episode creation → `episode_created`
* healing → `healed_marked` and `phase_entered`
* relapse → `relapse_marked` and `phase_entered`
* automatic phase change → `phase_entered`
* application create → `application_logged`
* application edit → `application_updated`
* application delete → `application_deleted`
* obsolete transition → `episode_obsoleted`

Write the event in the same DB transaction as the related state change where practical.

---

## 16. API Requirements

Use the API contract from `api_contract.md`, updated for the clarified ownership model.

The API must be JSON over HTTP.

The API must support at least:

* auth login
* location create/list
* subject create/list/get
* episode create/list/get
* mark healed
* report relapse
* optional manual advance endpoint
* application create/update/delete/list
* episode events
* episode timeline
* due endpoint
* API key creation/list/revoke for account use

Keep the API simple.

---

## 17. Required API Endpoints

The backend must implement at least these endpoints.

## 17.1 Auth

* `POST /auth/login`

Optional but recommended:

* `GET /auth/me`

---

## 17.2 API keys

* `POST /api-keys`
* `GET /api-keys`
* `POST /api-keys/{id}/revoke`

Return plaintext API key only once at creation time.

---

## 17.3 Subjects

* `POST /subjects`
* `GET /subjects`
* `GET /subjects/{subject_id}`

---

## 17.4 Locations

* `POST /locations`
* `GET /locations`

---

## 17.5 Episodes

* `POST /episodes`
* `GET /episodes`
* `GET /episodes/{episode_id}`
* `POST /episodes/{episode_id}/heal`
* `POST /episodes/{episode_id}/relapse`
* optional: `POST /episodes/{episode_id}/advance`

---

## 17.6 Applications

* `POST /applications`
* `PATCH /applications/{application_id}`
* `DELETE /applications/{application_id}`
* `GET /episodes/{episode_id}/applications`

Use simple edit/delete semantics for v1.

---

## 17.7 Events / Timeline

* `GET /episodes/{episode_id}/events`
* `GET /episodes/{episode_id}/timeline`

Timeline may return the same data as events for v1.

---

## 17.8 Due view

* `GET /episodes/due`

---

## 18. API Behavior Rules

## 18.1 Subject/account scoping

Every API read/write must be scoped to the authenticated account.

A user must never access another account’s:

* subjects
* locations
* episodes
* applications
* events
* API keys

---

## 18.2 Episode uniqueness rule

On episode creation:

* if a non-obsolete episode exists for the same subject/location, reject with conflict
* if an obsolete episode exists for the same subject/location, v1 may reactivate it, obsolete episode must be reactivated, not recreated

---

## 18.3 Application uniqueness rule

For a given episode:

* only one application may exist for the same exact timestamp
* if a duplicate timestamp is submitted, return conflict

---

## 18.4 Application editing

Editing an application is allowed in v1.

Editable fields:

* `applied_at`
* `treatment_type`
* `treatment_name`
* `quantity_text`
* `notes`

If `applied_at` changes:

* uniqueness constraint must still hold

Emit `application_updated`.

---

## 18.5 Application deletion

Deletion is allowed in v1.

Recommended implementation:

* soft delete

Delete behavior:

* deleted applications should not count toward due calculations
* deleted applications should not appear in normal list responses unless explicitly requested later
* emit `application_deleted`

---

## 19. Scheduler Requirements

The backend must include a simple automatic phase-advance mechanism.

Required behavior:

* periodic job runs in backend service
* finds taper episodes whose current phase duration has ended at calendar boundary
* advances them
* if current phase is 7 and complete, marks episode obsolete

Keep scheduler implementation simple.

Acceptable approaches:

* APScheduler inside FastAPI app
* simple background loop launched on app startup

Do not build a distributed job system for v1.

---

## 20. Protocol Seeding

On first database setup, seed the taper protocol table with:

| Phase | Duration Days | Apply Every N Days | Applications Per Day |
| ----- | ------------: | -----------------: | -------------------: |
| 1     |          null |                  1 |                    2 |
| 2     |            28 |                  2 |                    1 |
| 3     |            14 |                  3 |                    1 |
| 4     |            14 |                  4 |                    1 |
| 5     |            14 |                  5 |                    1 |
| 6     |            14 |                  6 |                    1 |
| 7     |            14 |                  7 |                    1 |

Phase 1 is open-ended, so duration may be null.

---

## 21. Time Calculation Rules

These rules must be implemented consistently.

## 21.1 Phase 1

Open-ended until user marks healed.

---

## 21.2 Heal transition

When healed is marked:

* phase 2 starts immediately on that same local calendar day
* an Application is counted for phase 2 only when it is a valid phase-2 Application at or after the phase start
* the first phase 2 expected date is two local calendar days after phase 2 starts; each credited valid taper Application re-anchors the next expected date

---

## 21.3 Taper phase end

A taper phase ends at the relevant calendar day boundary.

Use deployment timezone to determine local day boundaries.

Store timestamps in UTC.

---

## 21.4 Due calculations

Phase 1 uses local Treatment Slots, so the exact instant determines whether an Application satisfies Morning or Evening. Taper phases use local calendar-day dates, so the time within a local date does not change that date's credit once the Application is valid.

Deleted, voided, pre-phase, future, and phase-foreign Applications do not satisfy slots or move rolling schedules.

---

## 22. Error Handling

All API errors must return JSON.

Use this shape:

```json
{
  "error": {
    "code": "string_code",
    "message": "Human-readable explanation"
  }
}
```

Use appropriate HTTP status codes:

* `400 Bad Request`
* `401 Unauthorized`
* `403 Forbidden`
* `404 Not Found`
* `409 Conflict`
* `422 Unprocessable Entity`
* `500 Internal Server Error`

---

## 23. Testing Requirements

The implementation must include tests for at least:

1. account login
2. API key authentication
3. subject creation
4. location creation
5. episode creation
6. duplicate active episode rejection
7. obsolete episode reactivation or consistent obsolete handling
8. heal transition to phase 2
9. relapse reset to phase 1
10. automatic phase advancement
11. automatic obsoletion after phase 7
12. application creation
13. duplicate application timestamp rejection
14. application update
15. application delete
16. due endpoint basic correctness
17. account scoping / unauthorized cross-account access
18. event creation for key actions

Keep tests focused on business rules.

---

## 24. Docker Requirements

The project must include a Docker-based local deployment.

Required services:

* `api`
* `postgres`

Use Docker Compose.

The stack must work with a single command such as:

```bash
docker compose up --build
```

Environment variables should be used for:

* database connection
* JWT secret or auth secret
* deployment timezone
* API configuration

---

## 25. Expected Project Deliverables

Codex should deliver a complete working repository including:

* FastAPI application
* PostgreSQL schema via Alembic migrations
* SQLAlchemy models
* Pydantic request/response schemas
* authentication implementation
* API key implementation
* scheduler for phase auto-advance
* seed logic for protocol
* Dockerfiles
* Docker Compose file
* README with setup and usage
* automated tests

---

## 26. Implementation Style Guidance

Keep the codebase clean and conventional.

Recommended structure:

```text
app/
  api/
  core/
  db/
  models/
  schemas/
  services/
  scheduler/
  auth/
  tests/
alembic/
docker-compose.yml
Dockerfile
README.md
```

Use a service layer for business logic.

Do not bury business rules directly in route handlers.

---

## 27. Explicit Simplicity Rules

To avoid over-engineering, follow these rules:

* no family model
* no protocol management UI
* no multiple protocols
* no advanced RBAC
* no distributed background worker system
* no event-sourcing architecture
* no advanced correction workflow
* no notification subsystem
* no client implementation
* no agent implementation

v1 should be a clean, working backend only.

---

## 28. Codex Implementation Instruction

Implement this product exactly as specified above.

Priority order:

1. correctness of domain rules
2. clean PostgreSQL schema
3. working FastAPI endpoints
4. correct auth/account scoping
5. correct phase progression and scheduler
6. Dockerized local run experience
7. tests

Where a minor implementation detail is unspecified, choose the simplest reasonable approach consistent with this spec.

Do not add unnecessary abstractions or features outside scope.

---

## 29. Summary

The first version of the Eczema Treatment Tracker is:

* a self-hosted backend
* account-based
* multi-subject
* PostgreSQL-backed
* FastAPI-based
* Dockerized
* calendar-day-driven
* simple but structured
* ready for later CLI and agent usage

The system must correctly support:

* account login
* subject management
* account-owned body locations
* one active/tapering episode per subject/location
* heal transition to phase 2
* relapse reset to phase 1
* automatic taper phase progression
* application CRUD
* due view
* simple event timeline
* API key auth for future CLI/agent clients

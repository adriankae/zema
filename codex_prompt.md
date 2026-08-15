You are implementing a complete backend system from a formal specification.

Your task is to build a **production-ready v1** of the product described below.

You must strictly follow the specification. Do not invent new features, abstractions, or architectural layers beyond what is defined.

---

# Objective

Implement a **self-hosted backend system** consisting of:

* PostgreSQL database schema (via migrations)
* FastAPI-based API server
* authentication (username/password + API keys)
* domain logic for eczema episode tracking
* background scheduler for automatic phase progression
* Dockerized deployment
* automated tests

The system must run locally via Docker Compose.

---

# Implementation Constraints

You must follow these rules:

## 1. No over-engineering

Do NOT introduce:

* microservices
* event sourcing architecture
* CQRS
* complex dependency injection frameworks
* distributed job systems (Celery, Kafka, etc.)
* GraphQL
* unnecessary abstraction layers

Keep the implementation clean, simple, and conventional.

---

## 2. Architecture

Use:

* FastAPI
* SQLAlchemy 2.x
* Alembic migrations
* PostgreSQL
* Pydantic models
* simple service layer for business logic

Organize code clearly (e.g. api/, models/, services/, db/, auth/, scheduler/).

---

## 3. Database

* Implement all tables exactly as specified
* Use proper constraints (foreign keys, uniqueness, partial indexes where required)
* Use Alembic for migrations
* Seed taper protocol on initial migration

---

## 4. Business Logic

You must implement all domain rules exactly:

* one active/taper episode per subject/location
* heal → phase 2 immediately
* relapse → reset to phase 1
* automatic phase progression (backend scheduler)
* phase transitions and taper schedule dates use deployment-local calendar-day semantics
* Phase 1 uses deployment-local Morning `[00:00, 14:00)` and Evening `[14:00, next local midnight)` Treatment Slots; exact 14:00 is Evening, and a partial first day exposes only the remaining slots
* correct phase durations
* due logic basics
* application CRUD with constraints

Do not approximate or simplify these rules.

---

## 5. Scheduler

Implement a simple in-process scheduler:

* runs periodically (once daily is sufficient)
* advances phases when calendar boundary is reached
* marks episodes obsolete after phase 7

Do not introduce external worker systems.

---

## 6. Authentication

Implement:

* username/password authentication
* password hashing (secure)
* JWT access token (simple, no refresh tokens required)
* API key support (hashed in DB)

Keep auth minimal but correct.

---

## 7. API

* Implement all endpoints defined in the spec
* Follow request/response shapes
* return proper HTTP status codes
* enforce account scoping (no cross-account access)

---

## 8. Events

* implement simple event logging as specified
* emit events on all key actions
* keep payloads simple
* do not build a full event-sourcing system

---

## 9. Docker

Provide:

* Dockerfile for API
* docker-compose.yml with:

  * api service
  * postgres service

System must start with:

```bash
docker compose up --build
```

---

## 10. Testing

Include tests for:

* core domain logic
* API endpoints
* authentication
* scheduler behavior
* constraints (uniqueness, transitions)

Tests must run successfully.

---

## 11. Output Format

You must output a **complete repository structure**, including:

* all source code files
* Docker configuration
* migration files
* seed logic
* tests
* README with setup instructions

Use clear file separators like:

```text
# path: app/main.py
<code>
```

Do not omit files.

---

## 12. Assumptions

If a minor detail is not specified:

* choose the simplest reasonable implementation
* stay consistent with the spec
* do not introduce new features

---

# Now implement the system defined in the following SPEC.

---

PASTE SPEC.md HERE

---

End of instructions.

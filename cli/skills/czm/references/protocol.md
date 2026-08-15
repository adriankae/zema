# Protocol

This reference describes the backend-owned treatment protocol that the `czm` skill should explain to users.

## Persistent Episode Statuses

The backend exposes these persistent statuses:

- `active_flare`
- `in_taper`
- `obsolete`

Relapse is not a persistent status. It is an event that returns the episode to `active_flare`.

## Phase Schedule

The phase schedule is:

- Phase 1: open-ended, `2x daily`
- Phase 2: `28 days`, every `2 days`
- Phase 3: `14 days`, every `3 days`
- Phase 4: `14 days`, every `4 days`
- Phase 5: `14 days`, every `5 days`
- Phase 6: `14 days`, every `6 days`
- Phase 7: `14 days`, every `7 days`

After phase 7 completes, the episode becomes `obsolete`.

## Manual Transitions

The agent may ask the CLI/backend to perform these transitions:

- create episode -> phase 1 / `active_flare`
- mark healed -> phase 2 / `in_taper`
- report relapse during taper -> phase 1 / `active_flare`

## Backend-Owned Automatic Transitions

The agent must not simulate or predict these transitions on its own:

- phase 2 -> phase 3
- phase 3 -> phase 4
- phase 4 -> phase 5
- phase 5 -> phase 6
- phase 6 -> phase 7
- phase 7 -> `obsolete`

Backend canonical code is authoritative for Due State and phase progression. The CLI is an HTTP adapter/consumer only: it must ask the backend for Due State and phase-progression outcomes rather than reconstructing schedule or phase logic locally.

## Due Semantics

- Ask the backend what is due instead of reconstructing a schedule locally.
- Use `czm due list` for day-by-day guidance.
- Use `czm events list` or `czm events timeline` when you need to explain how the episode reached its current state.
- Do not infer future due times if the backend has not returned them.

## Interpretation Rules

- Summarize backend state in plain language.
- Never claim a taper step has happened unless the CLI/backend returned success.
- Never invent a phase number, due item, or next due time.
- If the backend response is ambiguous or unexpected, stop and ask the user to inspect the episode with the CLI.

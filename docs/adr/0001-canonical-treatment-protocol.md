# Make Treatment Protocol v1 canonical in a pure domain module

Zema will define Treatment Protocol v1 in a pure domain module and treat the database protocol rows as a validated persistent mirror. Due State and Adherence will share phase and Treatment Slot primitives but remain separate strategies because they answer different questions; time and deployment timezone are explicit inputs so callers and tests do not depend on hidden global clocks.

## Considered options

- Database-only authority was rejected because several callers had already reconstructed protocol and slot rules independently.
- Code-only authority without database validation was rejected because persisted protocol rows could silently disagree with runtime calculations.
- One unified Due State and Adherence calculation was rejected because operational next action and historical credit have intentionally different semantics.

## Consequences

A mismatch between canonical v1 and its database mirror prevents Zema from becoming healthy after migration and bootstrap. Zema reports the differing phase and field, does not start the scheduler, and never overwrites protocol or treatment data automatically. Bootstrap adapts the immutable `CANONICAL_V1.phases` values only when the mirror is empty; an existing nonempty mirror is left untouched, and validation remains read-only.

The canonical primitives define deployment-local Phase 1 Morning/Evening Treatment Slots with a half-open 14:00 cutoff, rolling taper expectations, and calendar-day phase transitions. Timezone-aware instants remain explicit and safe across DST changes. Deleted, voided, pre-phase, future, and phase-foreign Applications cannot satisfy slots or move rolling schedules. Due State remains the operational rolling strategy; Adherence remains the historical, auditable strategy that preserves missed dates and re-anchors after each credited valid taper Application.

The hard-coded seed values in `alembic/versions/0001_initial.py` are immutable historical migration/initial mirror material, not a runtime schedule authority. `app.services._due_next_due_at` is a retained compatibility/response-projection adapter over canonical `DueState`, not a legacy schedule strategy. Issue #8 cleanup is satisfied when runtime calculations and schedule decisions use canonical code; retaining these migration and response-shaping artifacts is intentional and does not create a second authority.

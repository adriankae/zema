# Zema Treatment Tracking

Zema tracks an existing eczema treatment plan for each body location. It supports execution and historical review without recommending medical treatment.

## Language

**Treatment Protocol**:
The agreed sequence of Treatment Phases and their treatment cadence. Treatment Protocol v1 is canonical in code; the `taper_protocol_phases` table is a validated persistent mirror. Bootstrap may fill an empty mirror but does not repair or overwrite a nonempty one.
_Avoid_: Dosing recommendation, medical plan

**Protocol Mirror**:
The database representation of canonical Treatment Protocol v1. Validation compares it read-only with the code definition; a mismatch is a health/startup failure, not a repair trigger.
_Avoid_: Independent schedule, second source of truth

**Treatment Phase**:
One stage of a Treatment Protocol for a body location, with its own duration and application cadence.
_Avoid_: Step, level

**Treatment Slot**:
One expected treatment window within a deployment-local calendar day. In Phase 1, Morning is the half-open interval `[00:00, 14:00)` and Evening is `[14:00, next local midnight)`; exactly 14:00 is Evening, and a partial first day exposes only its remaining slots.
_Avoid_: Reminder, notification

**Application**:
A recorded treatment action for one active episode at a specific time. Deleted, voided, pre-phase, future, and phase-foreign Applications are not valid schedule inputs.
_Avoid_: Check-in, completion

**Due State**:
The operational state that indicates whether treatment is expected now. It follows the rolling schedule and can depend on the most recent valid taper Application. It shares canonical primitives with Adherence but is a separate strategy.
_Avoid_: Adherence, compliance

**Adherence**:
The historical, auditable comparison between recorded Applications and an auditable schedule. The initial taper anchor derives from the Treatment Phase start, missed scheduled dates remain missed, and each credited valid taper Application re-anchors the next expected date. It shares canonical primitives with Due State but is a separate strategy.
_Avoid_: Due State, compliance

**Calendar-Day Boundary**:
A deployment-local calendar boundary used for phase transitions and schedule dates. Stored instants remain timezone-aware and UTC-safe across DST changes.
_Avoid_: Fixed elapsed-hour interval

**Episode**:
The treatment lifecycle for one subject and one body location under a Treatment Protocol.
_Avoid_: Case, task

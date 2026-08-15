# Zema Treatment Tracking

Zema tracks an existing eczema treatment plan for each body location. It supports execution and historical review without recommending medical treatment.

## Language

**Treatment Protocol**:
The agreed sequence of Treatment Phases and their treatment cadence.
_Avoid_: Dosing recommendation, medical plan

**Treatment Phase**:
One stage of a Treatment Protocol for a body location, with its own duration and application cadence.
_Avoid_: Step, level

**Treatment Slot**:
One expected treatment window within a local calendar day.
_Avoid_: Reminder, notification

**Application**:
A recorded treatment action for one active episode at a specific time.
_Avoid_: Check-in, completion

**Due State**:
The operational state that indicates whether treatment is expected now. It follows the rolling schedule and can depend on the most recent valid Application.
_Avoid_: Adherence, compliance

**Adherence**:
The historical comparison between recorded Applications and an auditable schedule. The schedule starts from each Treatment Phase start, preserves missed dates, and rolls forward from each credited Application.
_Avoid_: Due State, compliance

**Episode**:
The treatment lifecycle for one subject and one body location under a Treatment Protocol.
_Avoid_: Case, task

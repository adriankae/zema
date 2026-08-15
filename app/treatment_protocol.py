"""Pure Treatment Protocol v1 domain primitives.

All current-time behavior receives an explicit aware datetime, and all local
calendar calculations receive an explicit timezone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone as utc_timezone, tzinfo
from enum import Enum
from typing import Iterable


__all__ = [
    "ApplicationInput",
    "CANONICAL_V1",
    "DailyExpectation",
    "DueState",
    "DueStatus",
    "PhaseDefinition",
    "PhaseProgressionResult",
    "ProtocolMirrorMismatchError",
    "RollingScheduleDay",
    "RollingScheduleStatus",
    "TreatmentProtocolV1",
    "TreatmentSlot",
    "TreatmentWindow",
    "validate_protocol_mirror",
]


@dataclass(frozen=True, slots=True)
class PhaseDefinition:
    phase_number: int
    duration_days: int | None
    apply_every_n_days: int
    applications_per_day: int


class TreatmentSlot(str, Enum):
    MORNING = "morning"
    EVENING = "evening"


@dataclass(frozen=True, slots=True)
class TreatmentWindow:
    slot: TreatmentSlot
    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class DailyExpectation:
    day: date
    windows: tuple[TreatmentWindow, ...]

    @property
    def expected_slots(self) -> tuple[TreatmentSlot, ...]:
        return tuple(window.slot for window in self.windows)

    @property
    def expected_count(self) -> int:
        return len(self.windows)


@dataclass(frozen=True, slots=True)
class ApplicationInput:
    applied_at: datetime
    phase_number_snapshot: int | None = None
    is_deleted: bool = False
    is_voided: bool = False


_CANONICAL_PHASES = (
    PhaseDefinition(1, None, 1, 2),
    PhaseDefinition(2, 28, 2, 1),
    PhaseDefinition(3, 14, 3, 1),
    PhaseDefinition(4, 14, 4, 1),
    PhaseDefinition(5, 14, 5, 1),
    PhaseDefinition(6, 14, 6, 1),
    PhaseDefinition(7, 14, 7, 1),
)


class ProtocolMirrorMismatchError(ValueError):
    """The persisted phase mirror differs from canonical Treatment Protocol v1."""

    def __init__(
        self,
        *,
        phase_number: int,
        field: str,
        expected: int | str | None,
        actual: int | str | None,
    ) -> None:
        self.phase_number = phase_number
        self.field = field
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"protocol mirror mismatch: phase {phase_number}, field {field}, "
            f"expected {expected}, actual {actual}"
        )

    @property
    def diagnostic(self) -> dict[str, int | str | None]:
        return {
            "phase_number": self.phase_number,
            "field": self.field,
            "expected": self.expected,
            "actual": self.actual,
        }


def validate_protocol_mirror(phases: Iterable[PhaseDefinition]) -> None:
    """Validate typed phase values against canonical Treatment Protocol v1."""

    actual_phases = tuple(phases)
    canonical_by_number = {phase.phase_number: phase for phase in _CANONICAL_PHASES}
    actual_by_number: dict[int, PhaseDefinition] = {}
    duplicate_numbers: set[int] = set()
    for phase in actual_phases:
        if phase.phase_number in actual_by_number:
            duplicate_numbers.add(phase.phase_number)
        actual_by_number.setdefault(phase.phase_number, phase)

    for expected_phase in _CANONICAL_PHASES:
        if expected_phase.phase_number not in actual_by_number:
            raise ProtocolMirrorMismatchError(
                phase_number=expected_phase.phase_number,
                field="phase",
                expected="present",
                actual="missing",
            )

    for phase_number in sorted(duplicate_numbers):
        raise ProtocolMirrorMismatchError(
            phase_number=phase_number,
            field="phase",
            expected="absent",
            actual="present",
        )

    for phase_number in sorted(actual_by_number):
        if phase_number not in canonical_by_number:
            raise ProtocolMirrorMismatchError(
                phase_number=phase_number,
                field="phase",
                expected="absent",
                actual="present",
            )

    for expected_phase in _CANONICAL_PHASES:
        actual_phase = actual_by_number[expected_phase.phase_number]
        for field in ("duration_days", "apply_every_n_days", "applications_per_day"):
            expected = getattr(expected_phase, field)
            actual = getattr(actual_phase, field)
            if actual != expected:
                raise ProtocolMirrorMismatchError(
                    phase_number=expected_phase.phase_number,
                    field=field,
                    expected=expected,
                    actual=actual,
                )


class RollingScheduleStatus(str, Enum):
    NOT_DUE = "not_due"
    DUE = "due"
    MISSED = "missed"
    CREDITED = "credited"
    FUTURE = "future"


@dataclass(frozen=True, slots=True)
class RollingScheduleDay:
    date: date
    status: RollingScheduleStatus
    credited_application: ApplicationInput | None = None


@dataclass(frozen=True, slots=True)
class PhaseProgressionResult:
    current_phase_number: int
    phase_started_at: datetime
    phase_due_end_at: datetime | None
    transition_count: int
    protocol_complete: bool
    protocol_completed_at: datetime | None


class DueStatus(str, Enum):
    DUE = "due"
    NOT_DUE = "not_due"
    FUTURE = "future"


@dataclass(frozen=True, slots=True)
class DueState:
    phase_number: int
    status: DueStatus
    as_of: datetime
    next_due_at: datetime
    due_slot: TreatmentSlot | None
    expected_slots: tuple[TreatmentSlot, ...]
    satisfied_slots: tuple[TreatmentSlot, ...]
    missed_slots: tuple[TreatmentSlot, ...]
    applications_completed_today: int
    applications_expected_today: int
    last_application_at: datetime | None

    @property
    def is_due(self) -> bool:
        return self.status == DueStatus.DUE


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value


def _utc(value: datetime) -> datetime:
    return _require_aware(value).astimezone(utc_timezone.utc)


def _require_timezone(value: tzinfo) -> tzinfo:
    if value is None:
        raise ValueError("timezone is required")
    return value


def _local_boundary(day: date, at: time, timezone: tzinfo) -> datetime:
    timezone = _require_timezone(timezone)
    boundary = datetime.combine(day, at, tzinfo=timezone)
    return _utc(boundary).astimezone(timezone)


def _add_local_calendar_days(value: datetime, days: int, timezone: tzinfo) -> datetime:
    timezone = _require_timezone(timezone)
    local_value = _require_aware(value).astimezone(timezone)
    wall_time = local_value.timetz().replace(tzinfo=None)
    target = datetime.combine(
        local_value.date() + timedelta(days=days),
        wall_time,
        tzinfo=timezone,
    )
    target = target.replace(fold=local_value.fold)
    return _utc(target).astimezone(timezone)


class TreatmentProtocolV1:
    @property
    def phases(self) -> tuple[PhaseDefinition, ...]:
        return _CANONICAL_PHASES

    def phase(self, phase_number: int) -> PhaseDefinition:
        for phase in self.phases:
            if phase.phase_number == phase_number:
                return phase
        raise ValueError(f"unknown treatment phase: {phase_number}")

    def valid_applications(
        self,
        applications: Iterable[ApplicationInput],
        *,
        phase_number: int,
        phase_started_at: datetime,
        through: datetime | None = None,
    ) -> tuple[ApplicationInput, ...]:
        self.phase(phase_number)
        phase_start_instant = _utc(phase_started_at)
        through_instant = _utc(through) if through is not None else None
        valid: list[ApplicationInput] = []
        for application in applications:
            applied_at = _require_aware(application.applied_at)
            snapshot_is_current = (
                application.phase_number_snapshot in {None, 1}
                if phase_number == 1
                else application.phase_number_snapshot == phase_number
            )
            if (
                not application.is_deleted
                and not application.is_voided
                and snapshot_is_current
                and _utc(applied_at) >= phase_start_instant
                and (through_instant is None or _utc(applied_at) <= through_instant)
            ):
                valid.append(application)
        return tuple(sorted(valid, key=lambda application: _utc(application.applied_at)))

    def rolling_schedule(
        self,
        *,
        phase_number: int,
        phase_started_at: datetime,
        applications: Iterable[ApplicationInput],
        from_date: date,
        to_date: date,
        as_of: datetime,
        timezone: tzinfo,
    ) -> tuple[RollingScheduleDay, ...]:
        timezone = _require_timezone(timezone)
        phase = self.phase(phase_number)
        if phase.phase_number == 1:
            raise ValueError("rolling schedules apply only to taper phases")
        if from_date > to_date:
            raise ValueError("from_date must not be after to_date")

        local_phase_start = _require_aware(phase_started_at).astimezone(timezone)
        local_as_of = _require_aware(as_of).astimezone(timezone)
        phase_start_date = local_phase_start.date()
        valid = self.valid_applications(
            applications,
            phase_number=phase_number,
            phase_started_at=phase_started_at,
            through=as_of,
        )
        applications_by_date: dict[date, list[ApplicationInput]] = {}
        for application in valid:
            applied_date = application.applied_at.astimezone(timezone).date()
            applications_by_date.setdefault(applied_date, []).append(application)

        next_due_date = phase_start_date + timedelta(days=phase.apply_every_n_days)
        current = phase_start_date
        result: list[RollingScheduleDay] = []
        while current <= to_date:
            credited_application = None
            if current > local_as_of.date():
                status = RollingScheduleStatus.FUTURE
            elif applications_by_date.get(current):
                credited_application = applications_by_date[current].pop(0)
                next_due_date = current + timedelta(days=phase.apply_every_n_days)
                status = RollingScheduleStatus.CREDITED
            elif current == next_due_date:
                status = (
                    RollingScheduleStatus.MISSED
                    if current < local_as_of.date()
                    else RollingScheduleStatus.DUE
                )
            else:
                status = RollingScheduleStatus.NOT_DUE

            if current >= from_date:
                result.append(RollingScheduleDay(current, status, credited_application))
            current += timedelta(days=1)
        return tuple(result)

    def next_rolling_due_date(
        self,
        *,
        phase_number: int,
        phase_started_at: datetime,
        applications: Iterable[ApplicationInput],
        now: datetime,
        timezone: tzinfo,
    ) -> date:
        timezone = _require_timezone(timezone)
        phase = self.phase(phase_number)
        if phase.phase_number == 1:
            raise ValueError("rolling schedules apply only to taper phases")

        local_now = _require_aware(now).astimezone(timezone)
        local_phase_start = _require_aware(phase_started_at).astimezone(timezone)
        valid = self.valid_applications(
            applications,
            phase_number=phase_number,
            phase_started_at=phase_started_at,
            through=now,
        )
        anchor_date = (
            valid[-1].applied_at.astimezone(timezone).date()
            if valid
            else local_phase_start.date()
        )
        scheduled_date = anchor_date + timedelta(days=phase.apply_every_n_days)
        return max(scheduled_date, local_now.date())

    def due_state(
        self,
        *,
        phase_number: int,
        phase_started_at: datetime,
        applications: Iterable[ApplicationInput],
        now: datetime,
        timezone: tzinfo,
    ) -> DueState:
        timezone = _require_timezone(timezone)
        phase = self.phase(phase_number)
        local_now = _require_aware(now).astimezone(timezone)
        local_phase_start = _require_aware(phase_started_at).astimezone(timezone)
        valid = self.valid_applications(
            applications,
            phase_number=phase_number,
            phase_started_at=phase_started_at,
            through=now,
        )
        last_application_at = valid[-1].applied_at if valid else None
        applications_completed_today = sum(
            1
            for application in valid
            if application.applied_at.astimezone(timezone).date() == local_now.date()
        )

        if phase.phase_number != 1:
            scheduled_date = (
                valid[-1].applied_at.astimezone(timezone).date()
                if valid
                else local_phase_start.date()
            ) + timedelta(days=phase.apply_every_n_days)
            is_future = _utc(local_now) < _utc(local_phase_start)
            is_due = not is_future and local_now.date() >= scheduled_date
            next_due_date = max(scheduled_date, local_now.date()) if is_due else scheduled_date
            return DueState(
                phase_number=phase.phase_number,
                status=DueStatus.FUTURE if is_future else DueStatus.DUE if is_due else DueStatus.NOT_DUE,
                as_of=local_now,
                next_due_at=_local_boundary(next_due_date, time.min, timezone),
                due_slot=None,
                expected_slots=(),
                satisfied_slots=(),
                missed_slots=(),
                applications_completed_today=applications_completed_today,
                applications_expected_today=phase.applications_per_day if is_due else 0,
                last_application_at=last_application_at,
            )

        expectation = self.daily_expectation(
            phase_started_at,
            local_now.date(),
            timezone=timezone,
        )
        satisfied_slots = tuple(
            window.slot
            for window in expectation.windows
            if any(
                _utc(window.start)
                <= _utc(application.applied_at.astimezone(timezone))
                < _utc(window.end)
                for application in valid
            )
        )
        satisfied = set(satisfied_slots)
        due_slot = next(
            (
                window.slot
                for window in expectation.windows
                if window.slot not in satisfied
                and _utc(window.start) <= _utc(local_now) < _utc(window.end)
            ),
            None,
        )
        missed_slots = tuple(
            window.slot
            for window in expectation.windows
            if window.slot not in satisfied and _utc(window.end) <= _utc(local_now)
        )
        if due_slot is not None:
            next_due_at = next(
                window.start for window in expectation.windows if window.slot == due_slot
            )
        else:
            next_due_at = next(
                (
                    window.start
                    for window in expectation.windows
                    if window.slot not in satisfied and _utc(local_now) < _utc(window.start)
                ),
                None,
            )
            if next_due_at is None:
                next_due_at = local_phase_start if _utc(local_now) < _utc(local_phase_start) else _local_boundary(
                    local_now.date() + timedelta(days=1), time.min, timezone
                )

        return DueState(
            phase_number=1,
            status=(
                DueStatus.FUTURE
                if _utc(local_now) < _utc(local_phase_start)
                else DueStatus.DUE
                if due_slot is not None
                else DueStatus.NOT_DUE
            ),
            as_of=local_now,
            next_due_at=next_due_at,
            due_slot=due_slot,
            expected_slots=expectation.expected_slots,
            satisfied_slots=satisfied_slots,
            missed_slots=missed_slots,
            applications_completed_today=applications_completed_today,
            applications_expected_today=expectation.expected_count,
            last_application_at=last_application_at,
        )

    def phase_progression(
        self,
        *,
        phase_number: int,
        phase_started_at: datetime,
        now: datetime,
        timezone: tzinfo,
    ) -> PhaseProgressionResult:
        timezone = _require_timezone(timezone)
        self.phase(phase_number)
        local_start = _require_aware(phase_started_at).astimezone(timezone)
        local_now = _require_aware(now).astimezone(timezone)

        if phase_number == 1:
            return PhaseProgressionResult(1, local_start, None, 0, False, None)

        current_phase = phase_number
        current_start = local_start
        transition_count = 0
        while True:
            phase = self.phase(current_phase)
            if phase.duration_days is None:
                raise ValueError("taper phase duration is required")
            phase_due_end = _add_local_calendar_days(current_start, phase.duration_days, timezone)
            if local_now.date() < phase_due_end.date():
                return PhaseProgressionResult(
                    current_phase,
                    current_start,
                    phase_due_end,
                    transition_count,
                    False,
                    None,
                )
            transition_count += 1
            if current_phase == 7:
                return PhaseProgressionResult(
                    7,
                    current_start,
                    None,
                    transition_count,
                    True,
                    phase_due_end,
                )
            current_phase += 1
            current_start = phase_due_end

    def daily_expectation(
        self,
        phase_started_at: datetime,
        day: date,
        *,
        timezone: tzinfo,
    ) -> DailyExpectation:
        timezone = _require_timezone(timezone)
        local_phase_start = _require_aware(phase_started_at).astimezone(timezone)
        day_start = _local_boundary(day, time.min, timezone)
        cutoff = _local_boundary(day, time(14, 0), timezone)
        next_day_start = _local_boundary(day + timedelta(days=1), time.min, timezone)
        if _utc(local_phase_start) >= _utc(next_day_start):
            return DailyExpectation(day, ())

        morning_start = day_start if _utc(day_start) >= _utc(local_phase_start) else local_phase_start
        evening_start = cutoff if _utc(cutoff) >= _utc(local_phase_start) else local_phase_start
        windows: list[TreatmentWindow] = []
        if _utc(morning_start) < _utc(cutoff):
            windows.append(TreatmentWindow(TreatmentSlot.MORNING, morning_start, cutoff))
        if _utc(evening_start) < _utc(next_day_start):
            windows.append(TreatmentWindow(TreatmentSlot.EVENING, evening_start, next_day_start))
        return DailyExpectation(day, tuple(windows))

    def classify_slot(
        self,
        applied_at: datetime,
        phase_started_at: datetime,
        *,
        timezone: tzinfo,
    ) -> TreatmentSlot | None:
        timezone = _require_timezone(timezone)
        local_applied_at = _require_aware(applied_at).astimezone(timezone)
        expectation = self.daily_expectation(
            phase_started_at,
            local_applied_at.date(),
            timezone=timezone,
        )
        for window in expectation.windows:
            if _utc(window.start) <= _utc(local_applied_at) < _utc(window.end):
                return window.slot
        return None


CANONICAL_V1 = TreatmentProtocolV1()

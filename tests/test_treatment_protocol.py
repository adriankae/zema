from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.treatment_protocol import (
    ApplicationInput,
    CANONICAL_V1,
    DueStatus,
    PhaseDefinition,
    PhaseProgressionResult,
    RollingScheduleStatus,
    TreatmentSlot,
)


def test_canonical_v1_exposes_the_seven_phase_definitions():
    assert CANONICAL_V1.phases == (
        PhaseDefinition(1, None, 1, 2),
        PhaseDefinition(2, 28, 2, 1),
        PhaseDefinition(3, 14, 3, 1),
        PhaseDefinition(4, 14, 4, 1),
        PhaseDefinition(5, 14, 5, 1),
        PhaseDefinition(6, 14, 6, 1),
        PhaseDefinition(7, 14, 7, 1),
    )


def test_phase_one_uses_local_half_open_morning_and_evening_windows():
    berlin = ZoneInfo("Europe/Berlin")
    phase_started_at = datetime(2026, 4, 6, 11, tzinfo=timezone.utc)

    expectation = CANONICAL_V1.daily_expectation(
        phase_started_at,
        date(2026, 4, 6),
        timezone=berlin,
    )

    assert expectation.expected_slots == (TreatmentSlot.MORNING, TreatmentSlot.EVENING)
    assert expectation.windows[0].start == datetime(2026, 4, 6, 13, tzinfo=berlin)
    assert expectation.windows[0].end == datetime(2026, 4, 6, 14, tzinfo=berlin)
    assert expectation.windows[1].start == datetime(2026, 4, 6, 14, tzinfo=berlin)
    assert expectation.windows[1].end == datetime(2026, 4, 7, 0, tzinfo=berlin)
    assert (
        CANONICAL_V1.classify_slot(
            datetime(2026, 4, 6, 12, tzinfo=timezone.utc),
            phase_started_at,
            timezone=berlin,
        )
        == TreatmentSlot.EVENING
    )


def test_phase_one_start_at_cutoff_has_evening_only_until_the_next_local_day():
    berlin = ZoneInfo("Europe/Berlin")
    phase_started_at = datetime(2026, 4, 6, 12, tzinfo=timezone.utc)

    start_day = CANONICAL_V1.daily_expectation(
        phase_started_at,
        date(2026, 4, 6),
        timezone=berlin,
    )
    next_day = CANONICAL_V1.daily_expectation(
        phase_started_at,
        date(2026, 4, 7),
        timezone=berlin,
    )

    assert start_day.expected_slots == (TreatmentSlot.EVENING,)
    assert start_day.expected_count == 1
    assert next_day.expected_slots == (TreatmentSlot.MORNING, TreatmentSlot.EVENING)
    assert next_day.expected_count == 2


def test_phase_one_start_at_1500_local_has_evening_only_until_the_next_local_day():
    berlin = ZoneInfo("Europe/Berlin")
    phase_started_at = datetime(2026, 4, 6, 13, tzinfo=timezone.utc)

    start_day = CANONICAL_V1.daily_expectation(
        phase_started_at,
        date(2026, 4, 6),
        timezone=berlin,
    )
    next_day = CANONICAL_V1.daily_expectation(
        phase_started_at,
        date(2026, 4, 7),
        timezone=berlin,
    )

    assert start_day.expected_slots == (TreatmentSlot.EVENING,)
    assert start_day.windows[0].start == datetime(2026, 4, 6, 15, tzinfo=berlin)
    assert next_day.expected_slots == (TreatmentSlot.MORNING, TreatmentSlot.EVENING)


def test_valid_applications_only_include_current_active_phase_inputs_through_now():
    phase_started_at = datetime(2026, 4, 5, 8, tzinfo=timezone.utc)
    now = datetime(2026, 4, 6, 12, tzinfo=timezone.utc)
    valid = ApplicationInput(
        applied_at=datetime(2026, 4, 6, 9, tzinfo=timezone.utc),
        phase_number_snapshot=2,
    )

    applications = (
        ApplicationInput(datetime(2026, 4, 4, 9, tzinfo=timezone.utc), phase_number_snapshot=1),
        ApplicationInput(datetime(2026, 4, 5, 7, tzinfo=timezone.utc), phase_number_snapshot=2),
        valid,
        ApplicationInput(datetime(2026, 4, 6, 10, tzinfo=timezone.utc), phase_number_snapshot=2, is_deleted=True),
        ApplicationInput(datetime(2026, 4, 6, 11, tzinfo=timezone.utc), phase_number_snapshot=2, is_voided=True),
        ApplicationInput(datetime(2026, 4, 7, 9, tzinfo=timezone.utc), phase_number_snapshot=2),
    )

    assert CANONICAL_V1.valid_applications(
        applications,
        phase_number=2,
        phase_started_at=phase_started_at,
        through=now,
    ) == (valid,)


def test_taper_rolling_schedule_preserves_missed_dates_and_anchors_on_credit():
    phase_started_at = datetime(2026, 5, 20, 8, tzinfo=timezone.utc)
    credited = ApplicationInput(
        datetime(2026, 5, 23, 9, tzinfo=timezone.utc),
        phase_number_snapshot=2,
    )

    days = CANONICAL_V1.rolling_schedule(
        phase_number=2,
        phase_started_at=phase_started_at,
        applications=(credited,),
        from_date=date(2026, 5, 21),
        to_date=date(2026, 5, 25),
        as_of=datetime(2026, 5, 26, 12, tzinfo=timezone.utc),
        timezone=timezone.utc,
    )
    by_date = {day.date: day for day in days}

    assert by_date[date(2026, 5, 21)].status == RollingScheduleStatus.NOT_DUE
    assert by_date[date(2026, 5, 22)].status == RollingScheduleStatus.MISSED
    assert by_date[date(2026, 5, 23)].status == RollingScheduleStatus.CREDITED
    assert by_date[date(2026, 5, 23)].credited_application == credited
    assert by_date[date(2026, 5, 24)].status == RollingScheduleStatus.NOT_DUE
    assert by_date[date(2026, 5, 25)].status == RollingScheduleStatus.MISSED


def test_early_taper_application_is_credited_on_its_date_and_reanchors_schedule():
    phase_started_at = datetime(2026, 5, 20, 8, tzinfo=timezone.utc)
    early = ApplicationInput(
        datetime(2026, 5, 21, 9, tzinfo=timezone.utc),
        phase_number_snapshot=2,
    )

    days = CANONICAL_V1.rolling_schedule(
        phase_number=2,
        phase_started_at=phase_started_at,
        applications=(early,),
        from_date=date(2026, 5, 20),
        to_date=date(2026, 5, 23),
        as_of=datetime(2026, 5, 24, 12, tzinfo=timezone.utc),
        timezone=timezone.utc,
    )
    by_date = {day.date: day for day in days}

    assert by_date[date(2026, 5, 21)].status == RollingScheduleStatus.CREDITED
    assert by_date[date(2026, 5, 21)].credited_application == early
    assert by_date[date(2026, 5, 22)].status == RollingScheduleStatus.NOT_DUE
    assert CANONICAL_V1.next_rolling_due_date(
        phase_number=2,
        phase_started_at=phase_started_at,
        applications=(early,),
        now=datetime(2026, 5, 22, 12, tzinfo=timezone.utc),
        timezone=timezone.utc,
    ) == date(2026, 5, 23)


def test_next_rolling_due_date_uses_latest_valid_application_and_catches_up_to_today():
    phase_started_at = datetime(2026, 5, 20, 8, tzinfo=timezone.utc)
    applications = (
        ApplicationInput(datetime(2026, 5, 21, 9, tzinfo=timezone.utc), phase_number_snapshot=1),
        ApplicationInput(datetime(2026, 5, 23, 9, tzinfo=timezone.utc), phase_number_snapshot=2),
    )

    assert CANONICAL_V1.next_rolling_due_date(
        phase_number=2,
        phase_started_at=phase_started_at,
        applications=applications,
        now=datetime(2026, 5, 24, 12, tzinfo=timezone.utc),
        timezone=timezone.utc,
    ) == date(2026, 5, 25)
    assert CANONICAL_V1.next_rolling_due_date(
        phase_number=2,
        phase_started_at=phase_started_at,
        applications=applications,
        now=datetime(2026, 5, 27, 12, tzinfo=timezone.utc),
        timezone=timezone.utc,
    ) == date(2026, 5, 27)


def test_due_state_reports_phase_one_evening_due_and_missed_morning_at_cutoff():
    berlin = ZoneInfo("Europe/Berlin")
    phase_started_at = datetime(2026, 4, 6, 9, tzinfo=timezone.utc)
    now = datetime(2026, 4, 6, 12, tzinfo=timezone.utc)

    state = CANONICAL_V1.due_state(
        phase_number=1,
        phase_started_at=phase_started_at,
        applications=(),
        now=now,
        timezone=berlin,
    )

    assert state.status == DueStatus.DUE
    assert state.due_slot == TreatmentSlot.EVENING
    assert state.expected_slots == (TreatmentSlot.MORNING, TreatmentSlot.EVENING)
    assert state.missed_slots == (TreatmentSlot.MORNING,)
    assert state.applications_expected_today == 2
    assert state.next_due_at == datetime(2026, 4, 6, 14, tzinfo=berlin)


def test_taper_due_state_uses_valid_application_anchor_and_catches_up():
    phase_started_at = datetime(2026, 5, 20, 8, tzinfo=timezone.utc)
    credited = ApplicationInput(datetime(2026, 5, 23, 9, tzinfo=timezone.utc), phase_number_snapshot=2)

    not_due = CANONICAL_V1.due_state(
        phase_number=2,
        phase_started_at=phase_started_at,
        applications=(credited,),
        now=datetime(2026, 5, 24, 12, tzinfo=timezone.utc),
        timezone=timezone.utc,
    )
    overdue = CANONICAL_V1.due_state(
        phase_number=2,
        phase_started_at=phase_started_at,
        applications=(credited,),
        now=datetime(2026, 5, 26, 12, tzinfo=timezone.utc),
        timezone=timezone.utc,
    )

    assert not_due.status == DueStatus.NOT_DUE
    assert not_due.next_due_at == datetime(2026, 5, 25, tzinfo=timezone.utc)
    assert overdue.status == DueStatus.DUE
    assert overdue.next_due_at == datetime(2026, 5, 26, tzinfo=timezone.utc)
    assert overdue.last_application_at == credited.applied_at


def test_berlin_window_boundaries_keep_local_times_across_dst_transitions():
    berlin = ZoneInfo("Europe/Berlin")
    phase_started_at = datetime(2026, 3, 28, 8, tzinfo=timezone.utc)

    spring = CANONICAL_V1.daily_expectation(
        phase_started_at,
        date(2026, 3, 29),
        timezone=berlin,
    )
    autumn = CANONICAL_V1.daily_expectation(
        phase_started_at,
        date(2026, 10, 25),
        timezone=berlin,
    )

    assert spring.windows[0].start == datetime(2026, 3, 29, 0, tzinfo=berlin)
    assert spring.windows[0].end == datetime(2026, 3, 29, 14, tzinfo=berlin)
    assert spring.windows[1].start == datetime(2026, 3, 29, 14, tzinfo=berlin)
    assert spring.windows[1].end == datetime(2026, 3, 30, 0, tzinfo=berlin)
    assert autumn.windows[0].start == datetime(2026, 10, 25, 0, tzinfo=berlin)
    assert autumn.windows[0].end == datetime(2026, 10, 25, 14, tzinfo=berlin)
    assert autumn.windows[1].start == datetime(2026, 10, 25, 14, tzinfo=berlin)
    assert autumn.windows[1].end == datetime(2026, 10, 26, 0, tzinfo=berlin)


def test_santiago_midnight_gap_normalizes_phase_one_window_boundaries():
    santiago = ZoneInfo("America/Santiago")
    expectation = CANONICAL_V1.daily_expectation(
        datetime(2026, 9, 5, 12, tzinfo=timezone.utc),
        date(2026, 9, 6),
        timezone=santiago,
    )

    assert expectation.windows[0].start == datetime(2026, 9, 6, 1, tzinfo=santiago)
    assert expectation.windows[0].start.astimezone(timezone.utc) == datetime(
        2026, 9, 6, 4, tzinfo=timezone.utc
    )
    for window in expectation.windows:
        for boundary in (window.start, window.end):
            assert boundary.tzinfo == santiago
            assert boundary.utcoffset() is not None
            assert boundary.astimezone(timezone.utc).astimezone(santiago) == boundary


def test_rolling_schedule_is_stable_for_short_and_long_query_windows():
    phase_started_at = datetime(2026, 5, 20, 8, tzinfo=timezone.utc)
    credited = ApplicationInput(datetime(2026, 5, 23, 9, tzinfo=timezone.utc), phase_number_snapshot=2)
    common = {
        "phase_number": 2,
        "phase_started_at": phase_started_at,
        "applications": (credited,),
        "as_of": datetime(2026, 5, 27, 12, tzinfo=timezone.utc),
        "timezone": timezone.utc,
    }

    short = CANONICAL_V1.rolling_schedule(
        **common,
        from_date=date(2026, 5, 23),
        to_date=date(2026, 5, 27),
    )
    long = CANONICAL_V1.rolling_schedule(
        **common,
        from_date=date(2026, 5, 20),
        to_date=date(2026, 5, 27),
    )

    assert short == tuple(day for day in long if day.date >= date(2026, 5, 23))


def test_invalid_applications_cannot_move_taper_schedule_or_due_state():
    phase_started_at = datetime(2026, 5, 20, 8, tzinfo=timezone.utc)
    invalid = (
        ApplicationInput(datetime(2026, 5, 23, 9, tzinfo=timezone.utc), phase_number_snapshot=1),
        ApplicationInput(datetime(2026, 5, 23, 10, tzinfo=timezone.utc), phase_number_snapshot=2, is_deleted=True),
        ApplicationInput(datetime(2026, 5, 23, 11, tzinfo=timezone.utc), phase_number_snapshot=2, is_voided=True),
    )

    schedule = CANONICAL_V1.rolling_schedule(
        phase_number=2,
        phase_started_at=phase_started_at,
        applications=invalid,
        from_date=date(2026, 5, 22),
        to_date=date(2026, 5, 23),
        as_of=datetime(2026, 5, 24, 12, tzinfo=timezone.utc),
        timezone=timezone.utc,
    )
    state = CANONICAL_V1.due_state(
        phase_number=2,
        phase_started_at=phase_started_at,
        applications=invalid,
        now=datetime(2026, 5, 23, 12, tzinfo=timezone.utc),
        timezone=timezone.utc,
    )

    assert schedule[0].status == RollingScheduleStatus.MISSED
    assert schedule[1].status == RollingScheduleStatus.NOT_DUE
    assert state.status == DueStatus.DUE
    assert state.last_application_at is None


def test_later_dst_fold_application_is_valid_classified_and_satisfies_morning_slot():
    berlin = ZoneInfo("Europe/Berlin")
    phase_started_at = datetime(2026, 10, 25, 2, 30, tzinfo=berlin, fold=0)
    application = ApplicationInput(
        datetime(2026, 10, 25, 2, 15, tzinfo=berlin, fold=1),
        phase_number_snapshot=1,
    )

    assert CANONICAL_V1.valid_applications(
        (application,),
        phase_number=1,
        phase_started_at=phase_started_at,
    ) == (application,)
    assert CANONICAL_V1.classify_slot(
        application.applied_at,
        phase_started_at,
        timezone=berlin,
    ) == TreatmentSlot.MORNING

    state = CANONICAL_V1.due_state(
        phase_number=1,
        phase_started_at=phase_started_at,
        applications=(application,),
        now=datetime(2026, 10, 25, 3, 0, tzinfo=berlin, fold=1),
        timezone=berlin,
    )
    assert state.satisfied_slots == (TreatmentSlot.MORNING,)


def test_phase_progression_transitions_phase_two_on_its_due_local_date():
    phase_started_at = datetime(2026, 1, 1, 18, tzinfo=timezone.utc)
    phase_two_due_end = datetime(2026, 1, 29, 18, tzinfo=timezone.utc)
    phase_three_due_end = datetime(2026, 2, 12, 18, tzinfo=timezone.utc)

    result = CANONICAL_V1.phase_progression(
        phase_number=2,
        phase_started_at=phase_started_at,
        now=phase_two_due_end,
        timezone=timezone.utc,
    )

    assert result == PhaseProgressionResult(
        current_phase_number=3,
        phase_started_at=phase_two_due_end,
        phase_due_end_at=phase_three_due_end,
        transition_count=1,
        protocol_complete=False,
        protocol_completed_at=None,
    )


def test_phase_progression_keeps_phase_one_open_ended_and_rejects_invalid_inputs():
    berlin = ZoneInfo("Europe/Berlin")

    phase_one = CANONICAL_V1.phase_progression(
        phase_number=1,
        phase_started_at=datetime(2026, 10, 25, 2, 30, tzinfo=berlin, fold=0),
        now=datetime(2027, 1, 1, 12, tzinfo=timezone.utc),
        timezone=berlin,
    )

    assert phase_one.current_phase_number == 1
    assert phase_one.phase_due_end_at is None
    assert phase_one.transition_count == 0
    assert phase_one.protocol_complete is False

    with pytest.raises(ValueError):
        CANONICAL_V1.phase_progression(
            phase_number=8,
            phase_started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            now=datetime(2026, 1, 2, tzinfo=timezone.utc),
            timezone=timezone.utc,
        )
    with pytest.raises(ValueError):
        CANONICAL_V1.phase_progression(
            phase_number=2,
            phase_started_at=datetime(2026, 1, 1),
            now=datetime(2026, 1, 2, tzinfo=timezone.utc),
            timezone=timezone.utc,
        )
    with pytest.raises(ValueError):
        CANONICAL_V1.phase_progression(
            phase_number=2,
            phase_started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            now=datetime(2026, 1, 2),
            timezone=timezone.utc,
        )


def test_phase_progression_uses_local_due_date_before_due_end_clock_time():
    phase_started_at = datetime(2026, 1, 1, 18, tzinfo=timezone.utc)
    phase_two_due_end = datetime(2026, 1, 29, 18, tzinfo=timezone.utc)

    before_due_date = CANONICAL_V1.phase_progression(
        phase_number=2,
        phase_started_at=phase_started_at,
        now=phase_two_due_end - timedelta(days=1, seconds=1),
        timezone=timezone.utc,
    )
    before_due_end_clock = CANONICAL_V1.phase_progression(
        phase_number=2,
        phase_started_at=phase_started_at,
        now=phase_two_due_end - timedelta(minutes=1),
        timezone=timezone.utc,
    )

    assert before_due_date.current_phase_number == 2
    assert before_due_date.transition_count == 0
    assert before_due_end_clock.current_phase_number == 3
    assert before_due_end_clock.transition_count == 1


def test_phase_progression_catches_up_multiple_elapsed_taper_phases():
    phase_started_at = datetime(2026, 1, 1, 18, tzinfo=timezone.utc)

    result = CANONICAL_V1.phase_progression(
        phase_number=2,
        phase_started_at=phase_started_at,
        now=datetime(2026, 2, 27, 12, tzinfo=timezone.utc),
        timezone=timezone.utc,
    )

    assert result.current_phase_number == 5
    assert result.phase_started_at == datetime(2026, 2, 26, 18, tzinfo=timezone.utc)
    assert result.phase_due_end_at == datetime(2026, 3, 12, 18, tzinfo=timezone.utc)
    assert result.transition_count == 3
    assert result.protocol_complete is False


def test_phase_progression_completes_after_phase_seven_while_retaining_phase_seven():
    phase_started_at = datetime(2026, 1, 1, 18, tzinfo=timezone.utc)

    result = CANONICAL_V1.phase_progression(
        phase_number=2,
        phase_started_at=phase_started_at,
        now=datetime(2026, 4, 10, 12, tzinfo=timezone.utc),
        timezone=timezone.utc,
    )

    assert result.current_phase_number == 7
    assert result.phase_started_at == datetime(2026, 3, 26, 18, tzinfo=timezone.utc)
    assert result.phase_due_end_at is None
    assert result.protocol_completed_at == datetime(2026, 4, 9, 18, tzinfo=timezone.utc)
    assert result.transition_count == 6
    assert result.protocol_complete is True


def test_phase_progression_adds_local_calendar_days_across_berlin_dst_change():
    berlin = ZoneInfo("Europe/Berlin")
    phase_started_at = datetime(2026, 3, 15, 18, 30, tzinfo=berlin)

    result = CANONICAL_V1.phase_progression(
        phase_number=2,
        phase_started_at=phase_started_at,
        now=datetime(2026, 4, 11, 12, tzinfo=timezone.utc),
        timezone=berlin,
    )

    assert result.current_phase_number == 2
    assert result.phase_due_end_at == datetime(2026, 4, 12, 18, 30, tzinfo=berlin)
    assert result.phase_due_end_at.astimezone(timezone.utc) == datetime(
        2026, 4, 12, 16, 30, tzinfo=timezone.utc
    )


def test_phase_progression_normalizes_spring_gap_before_multiple_transitions():
    berlin = ZoneInfo("Europe/Berlin")
    phase_started_at = datetime(2026, 3, 1, 2, 30, tzinfo=berlin)

    result = CANONICAL_V1.phase_progression(
        phase_number=2,
        phase_started_at=phase_started_at,
        now=datetime(2026, 4, 13, 12, tzinfo=timezone.utc),
        timezone=berlin,
    )

    assert result.current_phase_number == 4
    assert result.transition_count == 2
    assert result.phase_started_at == datetime(2026, 4, 12, 3, 30, tzinfo=berlin)
    assert result.phase_started_at.astimezone(timezone.utc) == datetime(
        2026, 4, 12, 1, 30, tzinfo=timezone.utc
    )
    assert result.phase_due_end_at == datetime(2026, 4, 26, 3, 30, tzinfo=berlin)
    assert result.phase_due_end_at.astimezone(timezone.utc) == datetime(
        2026, 4, 26, 1, 30, tzinfo=timezone.utc
    )


def test_due_state_does_not_auto_transition_at_a_phase_boundary():
    state = CANONICAL_V1.due_state(
        phase_number=2,
        phase_started_at=datetime(2026, 1, 1, 18, tzinfo=timezone.utc),
        applications=(),
        now=datetime(2026, 1, 29, 12, tzinfo=timezone.utc),
        timezone=timezone.utc,
    )

    assert state.phase_number == 2
    assert state.status == DueStatus.DUE

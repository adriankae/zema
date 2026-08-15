from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone as utc_timezone, tzinfo
from typing import Iterable

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import deployment_tz, local_date, to_local, utc_now
from app.models import Account, EpisodeDailyAdherence, EpisodePhaseHistory, TreatmentApplication
from app.services import get_episode, list_episodes
from app.treatment_protocol import ApplicationInput, CANONICAL_V1, RollingScheduleStatus


ADHERENCE_STATUSES = {"completed", "partial", "missed", "not_due", "future"}
ADHERENCE_SOURCES = {"calculated", "backfill", "rebuild", "system"}


@dataclass(frozen=True)
class CalculatedAdherenceDay:
    account_id: int
    episode_id: int
    subject_id: int
    location_id: int
    date: date
    phase_number: int
    expected_applications: int
    completed_applications: int
    credited_applications: int
    status: str
    calculated_at: datetime


@dataclass(frozen=True)
class AdherenceSummary:
    expected_total: int
    completed_total: int
    credited_total: int
    adherence_score: float | None
    completed_day_count: int
    partial_day_count: int
    missed_day_count: int
    not_due_day_count: int
    future_day_count: int


def _validate_date_range(from_date: date, to_date: date) -> None:
    if from_date > to_date:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid date range")


def _validate_source(source: str) -> None:
    if source not in ADHERENCE_SOURCES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid adherence source")


def _iter_dates(from_date: date, to_date: date) -> Iterable[date]:
    current = from_date
    while current <= to_date:
        yield current
        current += timedelta(days=1)


def _status_for(date_value: date, expected: int, completed: int, today: date) -> str:
    if date_value > today:
        return "future"
    if expected > 0 and completed >= expected:
        return "completed"
    if expected > 0 and 0 < completed < expected:
        return "partial"
    if expected > 0 and completed == 0:
        return "missed"
    return "not_due"


def _applications_for_episode(db: Session, episode_id: int) -> list[TreatmentApplication]:
    return list(
        db.execute(
            select(TreatmentApplication).where(
                TreatmentApplication.episode_id == episode_id,
                TreatmentApplication.is_deleted.is_(False),
                TreatmentApplication.is_voided.is_(False),
            )
        ).scalars()
    )


def _instant(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        value = to_local(value)
    return value.astimezone(utc_timezone.utc)


def _application_input(application: TreatmentApplication) -> ApplicationInput:
    return ApplicationInput(
        applied_at=to_local(application.applied_at),
        phase_number_snapshot=application.phase_number_snapshot,
        is_deleted=application.is_deleted,
        is_voided=application.is_voided,
    )


def _valid_phase_applications(
    applications: list[TreatmentApplication],
    history: EpisodePhaseHistory,
    phase_number: int,
    *,
    through: datetime | None = None,
) -> tuple[ApplicationInput, ...]:
    phase_started_at = to_local(history.started_at)
    valid = CANONICAL_V1.valid_applications(
        (_application_input(application) for application in applications),
        phase_number=phase_number,
        phase_started_at=phase_started_at,
        through=through,
    )
    if history.ended_at is None:
        return valid

    phase_ended_at = to_local(history.ended_at)
    phase_end_instant = _instant(phase_ended_at)
    return tuple(application for application in valid if _instant(application.applied_at) < phase_end_instant)


def _applications_by_local_date(
    applications: Iterable[ApplicationInput],
    timezone: tzinfo,
) -> dict[date, list[ApplicationInput]]:
    applications_by_date: dict[date, list[ApplicationInput]] = {}
    for application in applications:
        applied_date = application.applied_at.astimezone(timezone).date()
        applications_by_date.setdefault(applied_date, []).append(application)
    return applications_by_date


def _phase_one_adherence_counts(
    applications_by_date: dict[date, list[ApplicationInput]],
    phase_started_at: datetime,
    date_value: date,
    timezone: tzinfo,
) -> tuple[int, int, int]:
    expectation = CANONICAL_V1.daily_expectation(
        phase_started_at,
        date_value,
        timezone=timezone,
    )
    valid_applications = applications_by_date.get(date_value, [])
    credited = sum(
        1
        for window in expectation.windows
        if any(
            _instant(window.start) <= _instant(application.applied_at) < _instant(window.end)
            for application in valid_applications
        )
    )
    return expectation.expected_count, len(valid_applications), credited


def _canonical_schedule_as_of(
    phase_started_at: datetime,
    range_end: date,
    timezone: tzinfo,
) -> datetime:
    expectation = CANONICAL_V1.daily_expectation(
        phase_started_at,
        range_end,
        timezone=timezone,
    )
    return expectation.windows[-1].end


def calculate_episode_adherence(
    db: Session,
    account: Account,
    episode_id: int,
    from_date: date,
    to_date: date,
    now: datetime | None = None,
) -> list[CalculatedAdherenceDay]:
    _validate_date_range(from_date, to_date)
    episode = get_episode(db, account, episode_id)
    histories = list(
        db.execute(
            select(EpisodePhaseHistory)
            .where(EpisodePhaseHistory.episode_id == episode.id)
            .order_by(EpisodePhaseHistory.started_at.asc(), EpisodePhaseHistory.id.asc())
        ).scalars()
    )
    if not histories:
        return []

    applications = _applications_for_episode(db, episode.id)
    timezone = deployment_tz()
    if now is None:
        today = local_date(utc_now())
        calculated_at = utc_now()
        calculation_now = None
    else:
        calculation_now = _instant(now)
        today = local_date(calculation_now)
        calculated_at = calculation_now
    rows: list[CalculatedAdherenceDay] = []

    for history in histories:
        try:
            phase = CANONICAL_V1.phase(history.phase_number)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="protocol phase missing") from exc

        phase_started_at = to_local(history.started_at)
        phase_start_date = phase_started_at.date()
        phase_ended_at = to_local(history.ended_at) if history.ended_at is not None else None
        phase_end_date = phase_ended_at.date() if phase_ended_at is not None else to_date + timedelta(days=1)
        range_start = max(from_date, phase_start_date)
        range_end = min(to_date, phase_end_date - timedelta(days=1))
        if range_start > range_end:
            continue

        phase_applications = _valid_phase_applications(
            applications,
            history,
            phase.phase_number,
            through=calculation_now,
        )
        applications_by_date = _applications_by_local_date(phase_applications, timezone)

        if phase.phase_number == 1:
            schedule_days = (
                (date_value, None)
                for date_value in _iter_dates(range_start, range_end)
            )
        else:
            schedule_days = (
                (schedule_day.date, schedule_day)
                for schedule_day in CANONICAL_V1.rolling_schedule(
                    phase_number=phase.phase_number,
                    phase_started_at=phase_started_at,
                    applications=phase_applications,
                    from_date=phase_start_date,
                    to_date=range_end,
                    as_of=_canonical_schedule_as_of(phase_started_at, range_end, timezone),
                    timezone=timezone,
                )
                if schedule_day.date >= range_start
            )

        for date_value, schedule_day in schedule_days:
            if phase.phase_number == 1:
                expected, completed, credited = _phase_one_adherence_counts(
                    applications_by_date,
                    phase_started_at,
                    date_value,
                    timezone,
                )
            else:
                completed = len(applications_by_date.get(date_value, []))
                if schedule_day.status == RollingScheduleStatus.CREDITED:
                    expected = phase.applications_per_day
                    credited = min(expected, phase.applications_per_day)
                elif schedule_day.status in {RollingScheduleStatus.DUE, RollingScheduleStatus.MISSED}:
                    expected = phase.applications_per_day
                    credited = 0
                else:
                    expected = 0
                    credited = 0
            rows.append(
                CalculatedAdherenceDay(
                    account_id=episode.account_id,
                    episode_id=episode.id,
                    subject_id=episode.subject_id,
                    location_id=episode.location_id,
                    date=date_value,
                    phase_number=history.phase_number,
                    expected_applications=expected,
                    completed_applications=completed,
                    credited_applications=credited,
                    status=_status_for(date_value, expected, credited, today),
                    calculated_at=calculated_at,
                )
            )
    return rows


def calculate_filtered_adherence(
    db: Session,
    account: Account,
    from_date: date,
    to_date: date,
    *,
    episode_id: int | None = None,
    subject_id: int | None = None,
    location_id: int | None = None,
    now: datetime | None = None,
) -> list[CalculatedAdherenceDay]:
    _validate_date_range(from_date, to_date)
    if episode_id is not None:
        episode = get_episode(db, account, episode_id)
        if subject_id is not None and episode.subject_id != subject_id:
            return []
        if location_id is not None and episode.location_id != location_id:
            return []
        return calculate_episode_adherence(db, account, episode.id, from_date, to_date, now=now)

    rows: list[CalculatedAdherenceDay] = []
    for episode in list_episodes(db, account):
        if subject_id is not None and episode.subject_id != subject_id:
            continue
        if location_id is not None and episode.location_id != location_id:
            continue
        rows.extend(calculate_episode_adherence(db, account, episode.id, from_date, to_date, now=now))
    return sorted(rows, key=lambda row: (row.date, row.episode_id))


def list_persisted_adherence_rows(
    db: Session,
    account: Account,
    from_date: date,
    to_date: date,
    *,
    episode_id: int | None = None,
    subject_id: int | None = None,
    location_id: int | None = None,
) -> list[EpisodeDailyAdherence]:
    _validate_date_range(from_date, to_date)
    stmt = select(EpisodeDailyAdherence).where(
        EpisodeDailyAdherence.account_id == account.id,
        EpisodeDailyAdherence.date >= from_date,
        EpisodeDailyAdherence.date <= to_date,
    )
    if episode_id is not None:
        get_episode(db, account, episode_id)
        stmt = stmt.where(EpisodeDailyAdherence.episode_id == episode_id)
    if subject_id is not None:
        stmt = stmt.where(EpisodeDailyAdherence.subject_id == subject_id)
    if location_id is not None:
        stmt = stmt.where(EpisodeDailyAdherence.location_id == location_id)
    return list(db.execute(stmt.order_by(EpisodeDailyAdherence.date.asc(), EpisodeDailyAdherence.episode_id.asc())).scalars())


def list_adherence_rows(
    db: Session,
    account: Account,
    from_date: date,
    to_date: date,
    *,
    episode_id: int | None = None,
    subject_id: int | None = None,
    location_id: int | None = None,
    persisted: bool = False,
    now: datetime | None = None,
) -> list[CalculatedAdherenceDay | EpisodeDailyAdherence]:
    if persisted:
        return list_persisted_adherence_rows(
            db,
            account,
            from_date,
            to_date,
            episode_id=episode_id,
            subject_id=subject_id,
            location_id=location_id,
        )
    return calculate_filtered_adherence(
        db,
        account,
        from_date,
        to_date,
        episode_id=episode_id,
        subject_id=subject_id,
        location_id=location_id,
        now=now,
    )


def persist_episode_adherence(
    db: Session,
    account: Account,
    episode_id: int,
    from_date: date,
    to_date: date,
    source: str = "calculated",
) -> list[EpisodeDailyAdherence]:
    _validate_source(source)
    calculated_rows = calculate_episode_adherence(db, account, episode_id, from_date, to_date)
    today = local_date(utc_now())
    rows_to_persist = [row for row in calculated_rows if row.date <= today]
    if not rows_to_persist:
        return []

    existing_rows = list(
        db.execute(
            select(EpisodeDailyAdherence).where(
                EpisodeDailyAdherence.episode_id == episode_id,
                EpisodeDailyAdherence.date >= from_date,
                EpisodeDailyAdherence.date <= to_date,
            )
        ).scalars()
    )
    existing_by_date = {row.date: row for row in existing_rows}
    now = utc_now()
    persisted: list[EpisodeDailyAdherence] = []

    for calculated in rows_to_persist:
        adherence = existing_by_date.get(calculated.date)
        if adherence is None:
            adherence = EpisodeDailyAdherence(
                account_id=calculated.account_id,
                episode_id=calculated.episode_id,
                subject_id=calculated.subject_id,
                location_id=calculated.location_id,
                date=calculated.date,
                phase_number=calculated.phase_number,
                expected_applications=calculated.expected_applications,
                completed_applications=calculated.completed_applications,
                credited_applications=calculated.credited_applications,
                status=calculated.status,
                source=source,
                calculated_at=calculated.calculated_at,
                finalized_at=None,
            )
        else:
            adherence.account_id = calculated.account_id
            adherence.subject_id = calculated.subject_id
            adherence.location_id = calculated.location_id
            adherence.phase_number = calculated.phase_number
            adherence.expected_applications = calculated.expected_applications
            adherence.completed_applications = calculated.completed_applications
            adherence.credited_applications = calculated.credited_applications
            adherence.status = calculated.status
            adherence.source = source
            adherence.calculated_at = calculated.calculated_at
            adherence.updated_at = now
        db.add(adherence)
        persisted.append(adherence)

    db.commit()
    for adherence in persisted:
        db.refresh(adherence)
    return sorted(persisted, key=lambda row: row.date)


def rebuild_episode_adherence(
    db: Session,
    account: Account,
    episode_id: int,
    from_date: date,
    to_date: date,
    source: str = "rebuild",
) -> list[EpisodeDailyAdherence]:
    return persist_episode_adherence(db, account, episode_id, from_date, to_date, source=source)


def rebuild_active_episode_adherence(
    db: Session,
    account: Account,
    from_date: date,
    to_date: date,
    source: str = "rebuild",
) -> list[EpisodeDailyAdherence]:
    _validate_date_range(from_date, to_date)
    persisted: list[EpisodeDailyAdherence] = []
    for episode in list_episodes(db, account):
        if episode.status == "obsolete":
            continue
        persisted.extend(rebuild_episode_adherence(db, account, episode.id, from_date, to_date, source=source))
    return sorted(persisted, key=lambda row: (row.episode_id, row.date))


def summarize_adherence(rows: Iterable[CalculatedAdherenceDay | EpisodeDailyAdherence]) -> AdherenceSummary:
    expected_total = 0
    completed_total = 0
    credited_total = 0
    day_counts = {status_name: 0 for status_name in ADHERENCE_STATUSES}

    for row in rows:
        expected_total += row.expected_applications
        completed_total += row.completed_applications
        credited_total += row.credited_applications
        day_counts[row.status] = day_counts.get(row.status, 0) + 1

    adherence_score = None if expected_total == 0 else credited_total / expected_total
    return AdherenceSummary(
        expected_total=expected_total,
        completed_total=completed_total,
        credited_total=credited_total,
        adherence_score=adherence_score,
        completed_day_count=day_counts["completed"],
        partial_day_count=day_counts["partial"],
        missed_day_count=day_counts["missed"],
        not_due_day_count=day_counts["not_due"],
        future_day_count=day_counts["future"],
    )

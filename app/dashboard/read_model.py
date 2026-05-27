from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adherence import list_adherence_rows, summarize_adherence
from app.core.config import settings
from app.core.time import deployment_tz, local_date, local_midnight, to_local, utc_now
from app.models import Account, BodyLocation, EczemaEpisode, Subject, TaperProtocolPhase, TreatmentApplication
from app.services import catch_up_episode_phases, due_items, get_last_successful_phase_catch_up, list_episodes


@dataclass(frozen=True)
class DashboardEpisodeRow:
    episode_id: int
    subject_name: str
    location_id: int
    location_name: str
    location_code: str
    phase_number: int
    status: str
    next_due_at: datetime | None
    last_application_at: datetime | None
    due_slot: str | None = None
    next_due_slot: str | None = None
    last_application_slot: str | None = None
    missed_slots_today: tuple[str, ...] = ()
    applications_completed_today: int = 0
    applications_expected_today: int = 0
    image_url: str | None = None


@dataclass(frozen=True)
class DashboardHabitDay:
    date: date
    status: str
    is_today: bool


@dataclass(frozen=True)
class DashboardLocation:
    id: int
    code: str
    display_name: str
    image_url: str | None


@dataclass(frozen=True)
class DashboardAdherenceRange:
    key: str
    label: str
    from_date: date
    to_date: date


@dataclass(frozen=True)
class DashboardAdherence:
    label: str
    from_date: date
    to_date: date
    expected: int
    completed: int
    score: float | None
    missed_days: int
    partial_days: int
    habit_chain: tuple[DashboardHabitDay, ...] = ()

    @property
    def day_count(self) -> int:
        return (self.to_date - self.from_date).days + 1


@dataclass(frozen=True)
class DashboardOverview:
    due: list[DashboardEpisodeRow]
    upcoming: list[DashboardEpisodeRow]
    active_locations: list[DashboardEpisodeRow]
    subjects: list[Subject]
    locations: list[DashboardLocation]
    adherence: list[DashboardAdherence]
    adherence_range: DashboardAdherenceRange
    phase_catch_up: dict | None
    generated_at: datetime

    @property
    def all_clear(self) -> bool:
        return not self.due


def build_dashboard_overview(db: Session, account: Account, *, adherence_range: str = "month", from_date: date | None = None, to_date: date | None = None) -> DashboardOverview:
    catch_up_episode_phases(db, reason="dashboard-read", account=account)
    due_raw = due_items(db, account)
    due_ids = {item["episode_id"] for item in due_raw}
    subjects = _subjects_by_id(db, account)
    locations = _locations_by_id(db, account)
    applications = _applications_by_episode(db, [episode.id for episode in list_episodes(db, account)])

    episodes = [episode for episode in list_episodes(db, account) if episode.status != "obsolete"]
    due = [_row_from_due_item(item, episodes, subjects, locations) for item in due_raw]
    upcoming = [
        _row_from_episode(episode, subjects, locations, applications.get(episode.id, []))
        for episode in episodes
        if episode.id not in due_ids
    ]
    upcoming = sorted(upcoming, key=lambda row: (row.next_due_at is None, row.next_due_at or datetime.max.replace(tzinfo=timezone.utc), row.location_name))
    active_locations = sorted(
        [_row_from_episode(episode, subjects, locations, applications.get(episode.id, [])) for episode in episodes],
        key=lambda row: row.location_name,
    )
    selected_adherence_range = _resolve_adherence_range(db, account, adherence_range, from_date, to_date)
    return DashboardOverview(
        due=due,
        upcoming=upcoming,
        active_locations=active_locations,
        subjects=sorted(subjects.values(), key=lambda subject: subject.display_name),
        locations=sorted((_dashboard_location(location) for location in locations.values()), key=lambda location: location.display_name),
        adherence=_adherence_summaries(db, account, selected_adherence_range),
        adherence_range=selected_adherence_range,
        phase_catch_up=get_last_successful_phase_catch_up(),
        generated_at=utc_now(),
    )


def _subjects_by_id(db: Session, account: Account) -> dict[int, Subject]:
    subjects = db.execute(select(Subject).where(Subject.account_id == account.id)).scalars()
    return {subject.id: subject for subject in subjects}


def _locations_by_id(db: Session, account: Account) -> dict[int, BodyLocation]:
    locations = db.execute(select(BodyLocation).where(BodyLocation.account_id == account.id)).scalars()
    return {location.id: location for location in locations}


def _applications_by_episode(db: Session, episode_ids: list[int]) -> dict[int, list[TreatmentApplication]]:
    if not episode_ids:
        return {}
    rows = list(
        db.execute(
            select(TreatmentApplication)
            .where(
                TreatmentApplication.episode_id.in_(episode_ids),
                TreatmentApplication.is_deleted.is_(False),
                TreatmentApplication.is_voided.is_(False),
            )
            .order_by(TreatmentApplication.episode_id.asc(), TreatmentApplication.applied_at.asc(), TreatmentApplication.id.asc())
        ).scalars()
    )
    grouped: dict[int, list[TreatmentApplication]] = {}
    for row in rows:
        grouped.setdefault(row.episode_id, []).append(row)
    return grouped


def _row_from_due_item(
    item: dict,
    episodes: list[EczemaEpisode],
    subjects: dict[int, Subject],
    locations: dict[int, BodyLocation],
) -> DashboardEpisodeRow:
    episode = next(episode for episode in episodes if episode.id == item["episode_id"])
    location = locations[episode.location_id]
    is_phase_one = episode.current_phase_number == 1
    due_slot = item.get("due_slot")
    last_application_at = item.get("last_application_at")
    return DashboardEpisodeRow(
        episode_id=episode.id,
        subject_name=subjects[episode.subject_id].display_name,
        location_id=location.id,
        location_name=location.display_name,
        location_code=location.code,
        phase_number=episode.current_phase_number,
        status=episode.status,
        next_due_at=item.get("next_due_at"),
        last_application_at=last_application_at,
        due_slot=due_slot,
        next_due_slot=due_slot if is_phase_one else None,
        last_application_slot=_phase_one_slot_for_datetime(last_application_at) if is_phase_one else None,
        missed_slots_today=tuple(item.get("missed_slots_today") or []),
        applications_completed_today=item.get("applications_completed_today") or 0,
        applications_expected_today=item.get("applications_expected_today") or 0,
        image_url=_image_url(location),
    )


def _row_from_episode(
    episode: EczemaEpisode,
    subjects: dict[int, Subject],
    locations: dict[int, BodyLocation],
    applications: list[TreatmentApplication],
) -> DashboardEpisodeRow:
    location = locations[episode.location_id]
    last_application_at = applications[-1].applied_at if applications else None
    next_due_at = _next_due_at(episode, applications)
    is_phase_one = episode.current_phase_number == 1
    return DashboardEpisodeRow(
        episode_id=episode.id,
        subject_name=subjects[episode.subject_id].display_name,
        location_id=location.id,
        location_name=location.display_name,
        location_code=location.code,
        phase_number=episode.current_phase_number,
        status=episode.status,
        next_due_at=next_due_at,
        last_application_at=last_application_at,
        next_due_slot=_phase_one_slot_for_datetime(next_due_at) if is_phase_one else None,
        last_application_slot=_phase_one_slot_for_datetime(last_application_at) if is_phase_one else None,
        image_url=_image_url(location),
    )


def _next_due_at(episode: EczemaEpisode, applications: list[TreatmentApplication]) -> datetime | None:
    now = utc_now()
    if episode.current_phase_number == 1:
        return _next_phase_one_due_at(episode, applications, now)
    phase = _phase_for_number(episode.current_phase_number)
    if phase is None:
        return None
    anchor = applications[-1].applied_at if applications else episode.phase_started_at
    next_due_date = local_date(anchor) + timedelta(days=phase.apply_every_n_days)
    today = local_date(now)
    if next_due_date < today:
        next_due_date = today
    return local_midnight(next_due_date)


def _next_phase_one_due_at(episode: EczemaEpisode, applications: list[TreatmentApplication], now: datetime) -> datetime | None:
    local_now = to_local(now)
    tz = deployment_tz()
    local_start = to_local(episode.phase_started_at)
    today = local_now.date()
    today_start = datetime.combine(today, time.min, tzinfo=tz)
    cutoff = datetime.combine(today, time(14, 0), tzinfo=tz)
    tomorrow_start = datetime.combine(today + timedelta(days=1), time.min, tzinfo=tz)

    def satisfies(slot_start: datetime, slot_end: datetime) -> bool:
        return any(slot_start <= to_local(application.applied_at) < slot_end for application in applications)

    morning_start = max(today_start, local_start)
    evening_start = max(cutoff, local_start)
    morning_due = morning_start < cutoff and not satisfies(morning_start, cutoff)
    evening_due = evening_start < tomorrow_start and not satisfies(evening_start, tomorrow_start)
    if morning_due:
        return local_midnight(today)
    if evening_due:
        return cutoff.astimezone(timezone.utc)
    return local_midnight(today + timedelta(days=1))


def _phase_one_slot_for_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return "morning" if to_local(value).time() < time(14, 0) else "evening"


def _phase_for_number(phase_number: int) -> TaperProtocolPhase | None:
    values = {
        1: TaperProtocolPhase(phase_number=1, duration_days=None, apply_every_n_days=1, applications_per_day=2),
        2: TaperProtocolPhase(phase_number=2, duration_days=28, apply_every_n_days=2, applications_per_day=1),
        3: TaperProtocolPhase(phase_number=3, duration_days=14, apply_every_n_days=3, applications_per_day=1),
        4: TaperProtocolPhase(phase_number=4, duration_days=14, apply_every_n_days=4, applications_per_day=1),
        5: TaperProtocolPhase(phase_number=5, duration_days=14, apply_every_n_days=5, applications_per_day=1),
        6: TaperProtocolPhase(phase_number=6, duration_days=14, apply_every_n_days=6, applications_per_day=1),
        7: TaperProtocolPhase(phase_number=7, duration_days=14, apply_every_n_days=7, applications_per_day=1),
    }
    return values.get(phase_number)


def _dashboard_location(location: BodyLocation) -> DashboardLocation:
    return DashboardLocation(
        id=location.id,
        code=location.code,
        display_name=location.display_name,
        image_url=_image_url(location),
    )


def _image_url(location: BodyLocation) -> str | None:
    if location.image_storage_key is None:
        return None
    root = Path(settings.location_image_dir).expanduser().resolve()
    candidate = (root / location.image_storage_key).resolve()
    if root != candidate and root not in candidate.parents:
        return None
    if not candidate.is_file():
        return None
    return f"/dashboard/locations/{location.id}/image"


def _resolve_adherence_range(
    db: Session,
    account: Account,
    range_key: str,
    custom_from: date | None,
    custom_to: date | None,
) -> DashboardAdherenceRange:
    today = local_date(utc_now())
    if range_key == "week":
        return DashboardAdherenceRange("week", "Last week", today - timedelta(days=6), today)
    if range_key == "year":
        return DashboardAdherenceRange("year", "Last year", today - timedelta(days=364), today)
    if range_key == "all":
        earliest = _earliest_episode_date(db, account) or today
        return DashboardAdherenceRange("all", "All time", earliest, today)
    if range_key == "custom" and custom_from is not None and custom_to is not None and custom_from <= custom_to:
        return DashboardAdherenceRange("custom", "Custom range", custom_from, custom_to)
    return DashboardAdherenceRange("month", "Last month", today - timedelta(days=29), today)


def _earliest_episode_date(db: Session, account: Account) -> date | None:
    values = [local_date(episode.phase_started_at) for episode in list_episodes(db, account) if episode.status != "obsolete"]
    return min(values) if values else None


def _adherence_summaries(db: Session, account: Account, selected_range: DashboardAdherenceRange) -> list[DashboardAdherence]:
    today = local_date(utc_now())
    usage_start = _earliest_episode_date(db, account)
    rows = list_adherence_rows(db, account, selected_range.from_date, selected_range.to_date, persisted=False)
    summary = summarize_adherence(rows)
    return [
        DashboardAdherence(
            label=selected_range.label,
            from_date=selected_range.from_date,
            to_date=selected_range.to_date,
            expected=summary.expected_total,
            completed=summary.completed_total,
            score=summary.adherence_score,
            missed_days=summary.missed_day_count,
            partial_days=summary.partial_day_count,
            habit_chain=_habit_chain(rows, selected_range.from_date, selected_range.to_date, today, usage_start),
        )
    ]


def _habit_chain(rows, from_date: date, to_date: date, today: date, usage_start: date | None) -> tuple[DashboardHabitDay, ...]:
    by_date: dict[date, list] = {}
    for row in rows:
        by_date.setdefault(row.date, []).append(row)

    days: list[DashboardHabitDay] = []
    current = from_date
    while current <= to_date:
        if usage_start is not None and current < usage_start:
            status = "pre_start"
        else:
            day_rows = by_date.get(current, [])
            statuses = {row.status for row in day_rows}
            if current == today and "missed" in statuses:
                status = "due"
            elif "missed" in statuses:
                status = "missed"
            elif "partial" in statuses:
                status = "partial"
            elif "completed" in statuses:
                status = "completed"
            elif "future" in statuses:
                status = "future"
            else:
                status = "not_due"
        days.append(DashboardHabitDay(date=current, status=status, is_today=current == today))
        current += timedelta(days=1)
    return tuple(days)

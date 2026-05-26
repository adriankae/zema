from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adherence import list_adherence_rows, summarize_adherence
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
    missed_slots_today: tuple[str, ...] = ()
    applications_completed_today: int = 0
    applications_expected_today: int = 0
    image_url: str | None = None


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


@dataclass(frozen=True)
class DashboardOverview:
    due: list[DashboardEpisodeRow]
    upcoming: list[DashboardEpisodeRow]
    active_locations: list[DashboardEpisodeRow]
    adherence: list[DashboardAdherence]
    phase_catch_up: dict | None
    generated_at: datetime

    @property
    def all_clear(self) -> bool:
        return not self.due


def build_dashboard_overview(db: Session, account: Account) -> DashboardOverview:
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
    return DashboardOverview(
        due=due,
        upcoming=upcoming,
        active_locations=active_locations,
        adherence=_adherence_summaries(db, account),
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
    return DashboardEpisodeRow(
        episode_id=episode.id,
        subject_name=subjects[episode.subject_id].display_name,
        location_id=location.id,
        location_name=location.display_name,
        location_code=location.code,
        phase_number=episode.current_phase_number,
        status=episode.status,
        next_due_at=item.get("next_due_at"),
        last_application_at=item.get("last_application_at"),
        due_slot=item.get("due_slot"),
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
    return DashboardEpisodeRow(
        episode_id=episode.id,
        subject_name=subjects[episode.subject_id].display_name,
        location_id=location.id,
        location_name=location.display_name,
        location_code=location.code,
        phase_number=episode.current_phase_number,
        status=episode.status,
        next_due_at=_next_due_at(episode, applications),
        last_application_at=last_application_at,
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


def _image_url(location: BodyLocation) -> str | None:
    if location.image_storage_key is None:
        return None
    return f"/dashboard/locations/{location.id}/image"


def _adherence_summaries(db: Session, account: Account) -> list[DashboardAdherence]:
    today = local_date(utc_now())
    windows = [("7 days", today - timedelta(days=6), today), ("30 days", today - timedelta(days=29), today)]
    summaries: list[DashboardAdherence] = []
    for label, from_date, to_date in windows:
        rows = list_adherence_rows(db, account, from_date, to_date, persisted=False)
        summary = summarize_adherence(rows)
        summaries.append(
            DashboardAdherence(
                label=label,
                from_date=from_date,
                to_date=to_date,
                expected=summary.expected_total,
                completed=summary.completed_total,
                score=summary.adherence_score,
                missed_days=summary.missed_day_count,
                partial_days=summary.partial_day_count,
            )
        )
    return summaries

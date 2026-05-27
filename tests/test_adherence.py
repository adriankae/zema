from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.adherence import (
    calculate_episode_adherence,
    persist_episode_adherence,
    rebuild_active_episode_adherence,
    rebuild_episode_adherence,
    summarize_adherence,
)
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.core.time import local_date, utc_now
from app.models import Account, BodyLocation, EczemaEpisode, EpisodeDailyAdherence, EpisodePhaseHistory, Subject
from app.services import create_episode, create_location, create_subject, delete_application, heal_episode, log_application, void_application


def _account(db):
    return db.execute(select(Account).where(Account.username == "admin")).scalar_one()


def _make_episode(db, account, *, started_at: datetime | None = None):
    started_at = started_at or datetime(2026, 1, 1, 8, tzinfo=timezone.utc)
    suffix = len(db.execute(select(BodyLocation.id)).all()) + 1
    subject = create_subject(db, account, "Child")
    location = create_location(db, account, f"left_elbow_{suffix}", "Left elbow")
    episode = create_episode(db, account, subject.id, location.id, "v1", started_at, "user", "test")
    return episode, subject, location


def _log_application(db, account, episode_id: int, applied_at: datetime):
    return log_application(db, account, episode_id, applied_at, "steroid", None, None, None, "user", "test")


def test_model_create_duplicate_and_check_constraints():
    db = SessionLocal()
    try:
        account = _account(db)
        episode, _, _ = _make_episode(db, account)
        adherence = EpisodeDailyAdherence(
            account_id=account.id,
            episode_id=episode.id,
            subject_id=episode.subject_id,
            location_id=episode.location_id,
            date=date(2026, 1, 1),
            phase_number=1,
            expected_applications=2,
            completed_applications=2,
            credited_applications=2,
            status="completed",
            source="calculated",
            calculated_at=utc_now(),
        )
        db.add(adherence)
        db.commit()
        assert adherence.id is not None

        duplicate = EpisodeDailyAdherence(
            account_id=account.id,
            episode_id=episode.id,
            subject_id=episode.subject_id,
            location_id=episode.location_id,
            date=date(2026, 1, 1),
            phase_number=1,
            expected_applications=2,
            completed_applications=2,
            credited_applications=2,
            status="completed",
            source="calculated",
            calculated_at=utc_now(),
        )
        db.add(duplicate)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        invalid = EpisodeDailyAdherence(
            account_id=account.id,
            episode_id=episode.id,
            subject_id=episode.subject_id,
            location_id=episode.location_id,
            date=date(2026, 1, 2),
            phase_number=1,
            expected_applications=1,
            completed_applications=2,
            credited_applications=2,
            status="finished",
            source="calculated",
            calculated_at=utc_now(),
        )
        db.add(invalid)
        with pytest.raises(IntegrityError):
            db.commit()
    finally:
        db.close()


def test_completed_partial_missed_and_extra_applications_are_capped():
    db = SessionLocal()
    try:
        account = _account(db)
        episode, _, _ = _make_episode(db, account, started_at=datetime(2026, 1, 1, 8, tzinfo=timezone.utc))
        _log_application(db, account, episode.id, datetime(2026, 1, 1, 8, tzinfo=timezone.utc))
        _log_application(db, account, episode.id, datetime(2026, 1, 1, 12, tzinfo=timezone.utc))
        _log_application(db, account, episode.id, datetime(2026, 1, 1, 18, tzinfo=timezone.utc))
        _log_application(db, account, episode.id, datetime(2026, 1, 2, 8, tzinfo=timezone.utc))

        rows = calculate_episode_adherence(db, account, episode.id, date(2026, 1, 1), date(2026, 1, 3))
        by_date = {row.date: row for row in rows}

        assert by_date[date(2026, 1, 1)].status == "completed"
        assert by_date[date(2026, 1, 1)].completed_applications == 3
        assert by_date[date(2026, 1, 1)].credited_applications == 2
        assert by_date[date(2026, 1, 2)].status == "partial"
        assert by_date[date(2026, 1, 3)].status == "missed"

        summary = summarize_adherence(rows)
        assert summary.expected_total == 6
        assert summary.completed_total == 4
        assert summary.credited_total == 3
        assert summary.adherence_score == pytest.approx(0.5)
        assert summary.completed_day_count == 1
        assert summary.partial_day_count == 1
        assert summary.missed_day_count == 1
    finally:
        db.close()


def test_not_due_future_and_empty_expected_summary():
    db = SessionLocal()
    try:
        account = _account(db)
        episode, _, _ = _make_episode(db, account, started_at=datetime(2026, 1, 1, 8, tzinfo=timezone.utc))
        heal_episode(db, account, episode.id, datetime(2026, 1, 2, 8, tzinfo=timezone.utc), "user", "test")

        rows = calculate_episode_adherence(db, account, episode.id, date(2026, 1, 3), date(2026, 1, 3))
        assert len(rows) == 1
        assert rows[0].expected_applications == 0
        assert rows[0].status == "not_due"
        assert summarize_adherence(rows).adherence_score is None

        future_date = local_date(utc_now()) + timedelta(days=10)
        future_episode, _, _ = _make_episode(
            db,
            account,
            started_at=datetime(future_date.year, future_date.month, future_date.day, 8, tzinfo=timezone.utc),
        )
        future_rows = calculate_episode_adherence(db, account, future_episode.id, future_date, future_date)
        assert future_rows[0].status == "future"
    finally:
        db.close()


def test_deleted_and_voided_applications_are_ignored():
    db = SessionLocal()
    try:
        account = _account(db)
        episode, _, _ = _make_episode(db, account, started_at=datetime(2026, 1, 1, 8, tzinfo=timezone.utc))
        deleted = _log_application(db, account, episode.id, datetime(2026, 1, 1, 8, tzinfo=timezone.utc))
        voided = _log_application(db, account, episode.id, datetime(2026, 1, 1, 12, tzinfo=timezone.utc))
        _log_application(db, account, episode.id, datetime(2026, 1, 1, 18, tzinfo=timezone.utc))
        delete_application(db, account, deleted.id, datetime(2026, 1, 1, 19, tzinfo=timezone.utc), "user", "test")
        void_application(db, account, voided.id, datetime(2026, 1, 1, 20, tzinfo=timezone.utc), "mistake", "user", "test")

        rows = calculate_episode_adherence(db, account, episode.id, date(2026, 1, 1), date(2026, 1, 1))

        assert rows[0].completed_applications == 1
        assert rows[0].credited_applications == 1
        assert rows[0].status == "partial"
    finally:
        db.close()


def test_phase_one_start_after_cutoff_expects_evening_slot_only(monkeypatch):
    import app.adherence as adherence
    from app.core.config import settings

    monkeypatch.setattr(settings, "deployment_timezone", "Europe/Berlin")
    monkeypatch.setattr(adherence, "utc_now", lambda: datetime(2026, 4, 26, 13, tzinfo=timezone.utc))
    db = SessionLocal()
    try:
        account = _account(db)
        episode, _, _ = _make_episode(db, account, started_at=datetime(2026, 4, 26, 13, tzinfo=timezone.utc))

        rows = calculate_episode_adherence(db, account, episode.id, date(2026, 4, 26), date(2026, 4, 26))

        assert len(rows) == 1
        assert rows[0].expected_applications == 1
        assert rows[0].completed_applications == 0
        assert rows[0].credited_applications == 0
        assert rows[0].status == "missed"
    finally:
        db.close()


def test_phase_one_start_after_cutoff_evening_application_completes_day(monkeypatch):
    import app.adherence as adherence
    from app.core.config import settings

    monkeypatch.setattr(settings, "deployment_timezone", "Europe/Berlin")
    monkeypatch.setattr(adherence, "utc_now", lambda: datetime(2026, 4, 26, 14, tzinfo=timezone.utc))
    db = SessionLocal()
    try:
        account = _account(db)
        episode, _, _ = _make_episode(db, account, started_at=datetime(2026, 4, 26, 13, tzinfo=timezone.utc))
        _log_application(db, account, episode.id, datetime(2026, 4, 26, 13, 30, tzinfo=timezone.utc))

        rows = calculate_episode_adherence(db, account, episode.id, date(2026, 4, 26), date(2026, 4, 26))

        assert rows[0].expected_applications == 1
        assert rows[0].completed_applications == 1
        assert rows[0].credited_applications == 1
        assert rows[0].status == "completed"
    finally:
        db.close()


def test_phase_one_start_after_cutoff_extra_evening_applications_do_not_inflate_credit(monkeypatch):
    import app.adherence as adherence
    from app.core.config import settings

    monkeypatch.setattr(settings, "deployment_timezone", "Europe/Berlin")
    monkeypatch.setattr(adherence, "utc_now", lambda: datetime(2026, 4, 26, 14, tzinfo=timezone.utc))
    db = SessionLocal()
    try:
        account = _account(db)
        episode, _, _ = _make_episode(db, account, started_at=datetime(2026, 4, 26, 13, tzinfo=timezone.utc))
        _log_application(db, account, episode.id, datetime(2026, 4, 26, 13, 30, tzinfo=timezone.utc))
        _log_application(db, account, episode.id, datetime(2026, 4, 26, 14, 30, tzinfo=timezone.utc))

        rows = calculate_episode_adherence(db, account, episode.id, date(2026, 4, 26), date(2026, 4, 26))

        assert rows[0].expected_applications == 1
        assert rows[0].completed_applications == 2
        assert rows[0].credited_applications == 1
        assert rows[0].status == "completed"
    finally:
        db.close()


def test_phase_one_start_before_cutoff_keeps_two_expected_slots(monkeypatch):
    import app.adherence as adherence
    from app.core.config import settings

    monkeypatch.setattr(settings, "deployment_timezone", "Europe/Berlin")
    monkeypatch.setattr(adherence, "utc_now", lambda: datetime(2026, 4, 26, 13, tzinfo=timezone.utc))
    db = SessionLocal()
    try:
        account = _account(db)
        episode, _, _ = _make_episode(db, account, started_at=datetime(2026, 4, 26, 8, tzinfo=timezone.utc))

        rows = calculate_episode_adherence(db, account, episode.id, date(2026, 4, 26), date(2026, 4, 26))

        assert rows[0].expected_applications == 2
        assert rows[0].completed_applications == 0
        assert rows[0].credited_applications == 0
    finally:
        db.close()


def test_phase_one_start_before_cutoff_morning_application_is_partial(monkeypatch):
    import app.adherence as adherence
    from app.core.config import settings

    monkeypatch.setattr(settings, "deployment_timezone", "Europe/Berlin")
    monkeypatch.setattr(adherence, "utc_now", lambda: datetime(2026, 4, 26, 13, tzinfo=timezone.utc))
    db = SessionLocal()
    try:
        account = _account(db)
        episode, _, _ = _make_episode(db, account, started_at=datetime(2026, 4, 26, 8, tzinfo=timezone.utc))
        _log_application(db, account, episode.id, datetime(2026, 4, 26, 8, 30, tzinfo=timezone.utc))

        rows = calculate_episode_adherence(db, account, episode.id, date(2026, 4, 26), date(2026, 4, 26))

        assert rows[0].expected_applications == 2
        assert rows[0].completed_applications == 1
        assert rows[0].credited_applications == 1
        assert rows[0].status == "partial"
    finally:
        db.close()


def test_phase_one_application_before_phase_start_does_not_count(monkeypatch):
    import app.adherence as adherence
    from app.core.config import settings

    monkeypatch.setattr(settings, "deployment_timezone", "Europe/Berlin")
    monkeypatch.setattr(adherence, "utc_now", lambda: datetime(2026, 4, 26, 13, tzinfo=timezone.utc))
    db = SessionLocal()
    try:
        account = _account(db)
        episode, _, _ = _make_episode(db, account, started_at=datetime(2026, 4, 26, 13, tzinfo=timezone.utc))
        _log_application(db, account, episode.id, datetime(2026, 4, 26, 11, tzinfo=timezone.utc))

        rows = calculate_episode_adherence(db, account, episode.id, date(2026, 4, 26), date(2026, 4, 26))

        assert rows[0].expected_applications == 1
        assert rows[0].completed_applications == 0
        assert rows[0].credited_applications == 0
    finally:
        db.close()


def test_phase_one_next_day_after_late_start_resumes_two_expected_slots(monkeypatch):
    import app.adherence as adherence
    from app.core.config import settings

    monkeypatch.setattr(settings, "deployment_timezone", "Europe/Berlin")
    monkeypatch.setattr(adherence, "utc_now", lambda: datetime(2026, 4, 27, 13, tzinfo=timezone.utc))
    db = SessionLocal()
    try:
        account = _account(db)
        episode, _, _ = _make_episode(db, account, started_at=datetime(2026, 4, 26, 13, tzinfo=timezone.utc))

        rows = calculate_episode_adherence(db, account, episode.id, date(2026, 4, 26), date(2026, 4, 27))
        by_date = {row.date: row for row in rows}

        assert by_date[date(2026, 4, 26)].expected_applications == 1
        assert by_date[date(2026, 4, 27)].expected_applications == 2
    finally:
        db.close()


def test_phase_one_late_start_rebuild_and_summary_use_clipped_expected_count(monkeypatch):
    import app.adherence as adherence
    from app.core.config import settings

    monkeypatch.setattr(settings, "deployment_timezone", "Europe/Berlin")
    monkeypatch.setattr(adherence, "utc_now", lambda: datetime(2026, 4, 26, 14, tzinfo=timezone.utc))
    db = SessionLocal()
    try:
        account = _account(db)
        episode, _, _ = _make_episode(db, account, started_at=datetime(2026, 4, 26, 13, tzinfo=timezone.utc))

        rebuilt = rebuild_episode_adherence(db, account, episode.id, date(2026, 4, 26), date(2026, 4, 26))
        summary = summarize_adherence(rebuilt)

        assert len(rebuilt) == 1
        assert rebuilt[0].expected_applications == 1
        assert rebuilt[0].completed_applications == 0
        assert rebuilt[0].credited_applications == 0
        assert summary.expected_total == 1
        assert summary.credited_total == 0
        assert summary.missed_day_count == 1
    finally:
        db.close()


def test_taper_schedule_and_half_open_phase_boundaries():
    db = SessionLocal()
    try:
        account = _account(db)
        episode, _, _ = _make_episode(db, account, started_at=datetime(2026, 1, 1, 8, tzinfo=timezone.utc))
        heal_episode(db, account, episode.id, datetime(2026, 1, 2, 8, tzinfo=timezone.utc), "user", "test")
        phase_two = (
            db.execute(
                select(EpisodePhaseHistory).where(
                    EpisodePhaseHistory.episode_id == episode.id,
                    EpisodePhaseHistory.phase_number == 2,
                )
            )
            .scalars()
            .one()
        )
        phase_two.ended_at = datetime(2026, 1, 6, 8, tzinfo=timezone.utc)
        db.commit()

        rows = calculate_episode_adherence(db, account, episode.id, date(2026, 1, 2), date(2026, 1, 6))
        by_date = {row.date: row for row in rows}

        assert by_date[date(2026, 1, 2)].expected_applications == 0
        assert by_date[date(2026, 1, 3)].expected_applications == 0
        assert by_date[date(2026, 1, 4)].expected_applications == 1
        assert by_date[date(2026, 1, 5)].expected_applications == 1
        assert date(2026, 1, 6) not in by_date
    finally:
        db.close()


def test_taper_adherence_uses_last_application_rolling_schedule(monkeypatch):
    import app.adherence as adherence

    monkeypatch.setattr(adherence, "utc_now", lambda: datetime(2026, 5, 26, 12, tzinfo=timezone.utc))
    db = SessionLocal()
    try:
        account = _account(db)
        episode, _, _ = _make_episode(db, account, started_at=datetime(2026, 5, 1, 8, tzinfo=timezone.utc))
        heal_episode(db, account, episode.id, datetime(2026, 5, 20, 8, tzinfo=timezone.utc), "user", "test")
        _log_application(db, account, episode.id, datetime(2026, 5, 23, 9, tzinfo=timezone.utc))
        _log_application(db, account, episode.id, datetime(2026, 5, 25, 9, tzinfo=timezone.utc))

        rows = calculate_episode_adherence(db, account, episode.id, date(2026, 5, 20), date(2026, 5, 26))
        by_date = {row.date: row for row in rows}

        assert by_date[date(2026, 5, 22)].status == "completed"
        assert by_date[date(2026, 5, 22)].credited_applications == 1
        assert by_date[date(2026, 5, 23)].status == "not_due"
        assert by_date[date(2026, 5, 24)].status == "not_due"
        assert by_date[date(2026, 5, 25)].status == "completed"
        assert by_date[date(2026, 5, 26)].status == "not_due"
    finally:
        db.close()


def test_taper_adherence_rolling_schedule_is_stable_across_short_windows(monkeypatch):
    import app.adherence as adherence

    monkeypatch.setattr(adherence, "utc_now", lambda: datetime(2026, 5, 27, 12, tzinfo=timezone.utc))
    db = SessionLocal()
    try:
        account = _account(db)
        episode, _, _ = _make_episode(db, account, started_at=datetime(2026, 5, 1, 8, tzinfo=timezone.utc))
        heal_episode(db, account, episode.id, datetime(2026, 5, 20, 8, tzinfo=timezone.utc), "user", "test")
        _log_application(db, account, episode.id, datetime(2026, 5, 23, 9, tzinfo=timezone.utc))
        _log_application(db, account, episode.id, datetime(2026, 5, 25, 9, tzinfo=timezone.utc))

        week_rows = calculate_episode_adherence(db, account, episode.id, date(2026, 5, 21), date(2026, 5, 27))
        month_rows = calculate_episode_adherence(db, account, episode.id, date(2026, 4, 28), date(2026, 5, 27))
        week_by_date = {row.date: row for row in week_rows}
        month_by_date = {row.date: row for row in month_rows}

        assert week_by_date[date(2026, 5, 26)].status == "not_due"
        assert month_by_date[date(2026, 5, 26)].status == "not_due"
        assert week_by_date[date(2026, 5, 27)].status == "missed"
        assert month_by_date[date(2026, 5, 27)].status == "missed"
    finally:
        db.close()


def test_dates_outside_phase_history_are_omitted():
    db = SessionLocal()
    try:
        account = _account(db)
        episode, _, _ = _make_episode(db, account, started_at=datetime(2026, 1, 2, 8, tzinfo=timezone.utc))

        rows = calculate_episode_adherence(db, account, episode.id, date(2026, 1, 1), date(2026, 1, 3))

        assert [row.date for row in rows] == [date(2026, 1, 2), date(2026, 1, 3)]
    finally:
        db.close()


def test_persist_rebuild_idempotent_and_past_only():
    db = SessionLocal()
    try:
        account = _account(db)
        episode, _, _ = _make_episode(db, account, started_at=datetime(2026, 1, 1, 8, tzinfo=timezone.utc))

        rows = persist_episode_adherence(db, account, episode.id, date(2026, 1, 1), date(2026, 1, 2))
        assert len(rows) == 2
        first_id = rows[0].id

        _log_application(db, account, episode.id, datetime(2026, 1, 1, 8, tzinfo=timezone.utc))
        rebuilt = rebuild_episode_adherence(db, account, episode.id, date(2026, 1, 1), date(2026, 1, 2))
        assert rebuilt[0].id == first_id
        assert rebuilt[0].source == "rebuild"
        assert rebuilt[0].completed_applications == 1

        rebuilt_again = rebuild_episode_adherence(db, account, episode.id, date(2026, 1, 1), date(2026, 1, 2))
        assert [row.id for row in rebuilt_again] == [row.id for row in rebuilt]

        future_date = local_date(utc_now()) + timedelta(days=20)
        future_rows = persist_episode_adherence(db, account, episode.id, future_date, future_date)
        assert future_rows == []
    finally:
        db.close()


def test_rebuild_active_episode_adherence_is_account_scoped_and_excludes_obsolete():
    db = SessionLocal()
    try:
        account = _account(db)
        active_episode, _, _ = _make_episode(db, account, started_at=datetime(2026, 1, 1, 8, tzinfo=timezone.utc))

        obsolete_subject = create_subject(db, account, "Obsolete child")
        obsolete_location = create_location(db, account, "obsolete_elbow", "Obsolete elbow")
        obsolete_episode = create_episode(
            db,
            account,
            obsolete_subject.id,
            obsolete_location.id,
            "v1",
            datetime(2026, 1, 1, 8, tzinfo=timezone.utc),
            "user",
            "test",
        )
        obsolete_episode.status = "obsolete"
        db.add(obsolete_episode)

        other = Account(username="other", password_hash=hash_password("other"), is_active=True)
        db.add(other)
        db.flush()
        other_subject = Subject(account_id=other.id, display_name="Other child")
        other_location = BodyLocation(account_id=other.id, code="other_elbow", display_name="Other elbow")
        db.add_all([other_subject, other_location])
        db.flush()
        other_episode = EczemaEpisode(
            account_id=other.id,
            subject_id=other_subject.id,
            location_id=other_location.id,
            status="active_flare",
            current_phase_number=1,
            phase_started_at=datetime(2026, 1, 1, 8, tzinfo=timezone.utc),
            phase_due_end_at=None,
            protocol_version="v1",
        )
        db.add(other_episode)
        db.flush()
        db.add(
            EpisodePhaseHistory(
                episode_id=other_episode.id,
                phase_number=1,
                started_at=datetime(2026, 1, 1, 8, tzinfo=timezone.utc),
                ended_at=None,
                reason="episode_created",
            )
        )
        db.commit()

        rows = rebuild_active_episode_adherence(db, account, date(2026, 1, 1), date(2026, 1, 1))

        assert [row.episode_id for row in rows] == [active_episode.id]
        persisted_episode_ids = set(db.execute(select(EpisodeDailyAdherence.episode_id)).scalars())
        assert active_episode.id in persisted_episode_ids
        assert obsolete_episode.id not in persisted_episode_ids
        assert other_episode.id not in persisted_episode_ids
    finally:
        db.close()


def test_invalid_date_range_raises_422():
    db = SessionLocal()
    try:
        account = _account(db)
        episode, _, _ = _make_episode(db, account)

        with pytest.raises(HTTPException) as exc:
            calculate_episode_adherence(db, account, episode.id, date(2026, 1, 3), date(2026, 1, 1))

        assert exc.value.status_code == 422
        assert exc.value.detail == "invalid date range"
    finally:
        db.close()

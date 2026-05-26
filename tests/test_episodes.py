from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select


def _create_subject_location(client, headers):
    subject = client.post("/subjects", headers=headers, json={"display_name": "Child"}).json()
    location = client.post("/locations", headers=headers, json={"code": "left_elbow", "display_name": "Left elbow"}).json()
    return subject["id"], location["location"]["id"]


def _create_episode(client, headers, *, location_code="left_elbow", location_name="Left elbow"):
    subject = client.post("/subjects", headers=headers, json={"display_name": f"Child {location_code}"}).json()
    location = client.post("/locations", headers=headers, json={"code": location_code, "display_name": location_name}).json()
    return client.post(
        "/episodes",
        headers=headers,
        json={"subject_id": subject["id"], "location_id": location["location"]["id"]},
    ).json()["episode"]


def _create_taper_episode(client, headers, *, location_code: str, healed_at: str = "2026-04-05T08:00:00Z"):
    episode = _create_episode(client, headers, location_code=location_code, location_name=location_code.replace("_", " ").title())
    heal = client.post(f"/episodes/{episode['id']}/heal", headers=headers, json={"healed_at": healed_at})
    assert heal.status_code == 200
    return heal.json()["episode"]


def test_episode_lifecycle(client, auth_headers):
    subject_id, location_id = _create_subject_location(client, auth_headers)
    created = client.post("/episodes", headers=auth_headers, json={"subject_id": subject_id, "location_id": location_id})
    assert created.status_code == 201
    episode = created.json()["episode"]
    assert episode["current_phase_number"] == 1

    duplicate = client.post("/episodes", headers=auth_headers, json={"subject_id": subject_id, "location_id": location_id})
    assert duplicate.status_code == 409

    heal = client.post(f"/episodes/{episode['id']}/heal", headers=auth_headers, json={"healed_at": "2026-04-05T18:00:00Z"})
    assert heal.status_code == 200
    healed = heal.json()["episode"]
    assert healed["current_phase_number"] == 2
    assert healed["status"] == "in_taper"

    relapse = client.post(f"/episodes/{episode['id']}/relapse", headers=auth_headers, json={"reported_at": "2026-04-06T18:00:00Z", "reason": "symptoms_returned"})
    assert relapse.status_code == 200
    relapsed = relapse.json()["episode"]
    assert relapsed["current_phase_number"] == 1
    assert relapsed["status"] == "active_flare"
    assert relapsed["healed_at"] is None

    heal_again = client.post(f"/episodes/{episode['id']}/heal", headers=auth_headers, json={"healed_at": "2026-04-07T18:00:00Z"})
    assert heal_again.status_code == 200
    healed_again = heal_again.json()["episode"]
    assert healed_again["current_phase_number"] == 2
    assert healed_again["status"] == "in_taper"
    assert healed_again["healed_at"] is not None


def test_auto_advance_and_obsolete(client, auth_headers):
    subject_id, location_id = _create_subject_location(client, auth_headers)
    episode = client.post("/episodes", headers=auth_headers, json={"subject_id": subject_id, "location_id": location_id}).json()["episode"]
    client.post(f"/episodes/{episode['id']}/heal", headers=auth_headers, json={"healed_at": "2026-01-01T00:00:00Z"})

    from app.core.database import SessionLocal
    from app.core.time import utc_now
    from app.services import auto_advance_due_episodes
    from app.models import EczemaEpisode

    db = SessionLocal()
    try:
        ep = db.get(EczemaEpisode, episode["id"])
        ep.phase_started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        ep.phase_due_end_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
        db.commit()
        auto_advance_due_episodes(db, datetime(2026, 3, 15, tzinfo=timezone.utc))
        db.refresh(ep)
        assert ep.status == "obsolete"
        assert ep.current_phase_number == 7
    finally:
        db.close()


def test_phase_one_due_uses_morning_and_evening_slots(client, auth_headers, monkeypatch):
    import app.api as api
    import app.services as services

    monkeypatch.setattr(api, "utc_now", lambda: datetime(2026, 4, 6, 8, tzinfo=timezone.utc))
    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 4, 6, 8, tzinfo=timezone.utc))
    episode = _create_episode(client, auth_headers, location_code="slot_elbow", location_name="Slot elbow")
    episode_id = episode["id"]

    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 4, 6, 9, tzinfo=timezone.utc))
    morning_due = client.get("/episodes/due", headers=auth_headers)
    assert morning_due.status_code == 200
    assert morning_due.json()["due"] == [
        {
            "episode_id": episode_id,
            "subject_id": episode["subject_id"],
            "location_id": episode["location_id"],
            "current_phase_number": 1,
            "treatment_due_today": True,
            "next_due_at": "2026-04-06T00:00:00Z",
            "last_application_at": None,
            "due_slot": "morning",
            "missed_slots_today": [],
            "applications_completed_today": 0,
            "applications_expected_today": 2,
        }
    ]

    logged_morning = client.post(
        "/applications",
        headers=auth_headers,
        json={"episode_id": episode_id, "applied_at": "2026-04-06T09:30:00Z"},
    )
    assert logged_morning.status_code == 201
    assert client.get("/episodes/due", headers=auth_headers).json()["due"] == []

    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 4, 6, 15, tzinfo=timezone.utc))
    evening_due = client.get("/episodes/due", headers=auth_headers).json()["due"]
    assert len(evening_due) == 1
    assert evening_due[0]["episode_id"] == episode_id
    assert evening_due[0]["due_slot"] == "evening"
    assert evening_due[0]["missed_slots_today"] == []
    assert evening_due[0]["applications_completed_today"] == 1
    assert evening_due[0]["applications_expected_today"] == 2

    logged_evening = client.post(
        "/applications",
        headers=auth_headers,
        json={"episode_id": episode_id, "applied_at": "2026-04-06T16:30:00Z"},
    )
    assert logged_evening.status_code == 201
    assert client.get("/episodes/due", headers=auth_headers).json()["due"] == []


def test_phase_one_evening_due_in_berlin_after_morning_applications(client, auth_headers, monkeypatch):
    import app.api as api
    import app.services as services
    from app.core.config import settings

    monkeypatch.setattr(settings, "deployment_timezone", "Europe/Berlin")
    # 2026-04-06 is CEST: local 08:00 == 06:00 UTC.
    monkeypatch.setattr(api, "utc_now", lambda: datetime(2026, 4, 6, 6, tzinfo=timezone.utc))
    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 4, 6, 6, tzinfo=timezone.utc))
    episode_a = _create_episode(client, auth_headers, location_code="berlin_slot_a", location_name="Berlin slot A")
    episode_b = _create_episode(client, auth_headers, location_code="berlin_slot_b", location_name="Berlin slot B")
    episode_c = _create_episode(client, auth_headers, location_code="berlin_slot_c", location_name="Berlin slot C")

    logged_a = client.post(
        "/applications",
        headers=auth_headers,
        # local 09:00 == 07:00 UTC.
        json={"episode_id": episode_a["id"], "applied_at": "2026-04-06T07:00:00Z"},
    )
    assert logged_a.status_code == 201
    logged_b = client.post(
        "/applications",
        headers=auth_headers,
        # local 10:00 == 08:00 UTC.
        json={"episode_id": episode_b["id"], "applied_at": "2026-04-06T08:00:00Z"},
    )
    assert logged_b.status_code == 201

    # local 15:00 == 13:00 UTC. Morning applications must not satisfy evening.
    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 4, 6, 13, tzinfo=timezone.utc))
    due = client.get("/episodes/due", headers=auth_headers).json()["due"]
    due_by_episode_id = {item["episode_id"]: item for item in due}

    assert set(due_by_episode_id) == {episode_a["id"], episode_b["id"], episode_c["id"]}
    assert due_by_episode_id[episode_a["id"]]["due_slot"] == "evening"
    assert due_by_episode_id[episode_b["id"]]["due_slot"] == "evening"
    assert due_by_episode_id[episode_c["id"]]["due_slot"] == "evening"
    assert due_by_episode_id[episode_a["id"]]["missed_slots_today"] == []
    assert due_by_episode_id[episode_b["id"]]["missed_slots_today"] == []
    assert due_by_episode_id[episode_c["id"]]["missed_slots_today"] == ["morning"]
    assert due_by_episode_id[episode_a["id"]]["applications_completed_today"] == 1
    assert due_by_episode_id[episode_b["id"]]["applications_completed_today"] == 1
    assert due_by_episode_id[episode_c["id"]]["applications_completed_today"] == 0
    assert due_by_episode_id[episode_a["id"]]["applications_expected_today"] == 2
    assert due_by_episode_id[episode_b["id"]]["applications_expected_today"] == 2
    assert due_by_episode_id[episode_c["id"]]["applications_expected_today"] == 2


def test_phase_one_evening_due_returns_morning_logged_episodes(client, auth_headers, monkeypatch):
    import app.api as api
    import app.services as services
    from app.core.config import settings

    monkeypatch.setattr(settings, "deployment_timezone", "Europe/Berlin")
    # 2026-04-26 is CEST: local 08:00 == 06:00 UTC.
    monkeypatch.setattr(api, "utc_now", lambda: datetime(2026, 4, 26, 6, tzinfo=timezone.utc))
    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 4, 26, 6, tzinfo=timezone.utc))
    episode_a = _create_episode(client, auth_headers, location_code="hinterkopf_links", location_name="Hinterkopf links")
    episode_b = _create_episode(client, auth_headers, location_code="kotelette_rechts", location_name="Kotelette rechts")
    episode_c = _create_episode(client, auth_headers, location_code="mundwinkel_rechts", location_name="Mundwinkel rechts")

    logged_a = client.post(
        "/applications",
        headers=auth_headers,
        # local 09:00 == 07:00 UTC.
        json={"episode_id": episode_a["id"], "applied_at": "2026-04-26T07:00:00Z"},
    )
    assert logged_a.status_code == 201
    logged_c = client.post(
        "/applications",
        headers=auth_headers,
        # local 10:00 == 08:00 UTC.
        json={"episode_id": episode_c["id"], "applied_at": "2026-04-26T08:00:00Z"},
    )
    assert logged_c.status_code == 201

    # local 15:00 == 13:00 UTC.
    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 4, 26, 13, tzinfo=timezone.utc))
    due = client.get("/episodes/due", headers=auth_headers).json()["due"]
    due_by_episode_id = {item["episode_id"]: item for item in due}

    assert set(due_by_episode_id) == {episode_a["id"], episode_b["id"], episode_c["id"]}
    assert all(item["due_slot"] == "evening" for item in due_by_episode_id.values())
    assert due_by_episode_id[episode_a["id"]]["applications_completed_today"] == 1
    assert due_by_episode_id[episode_b["id"]]["applications_completed_today"] == 0
    assert due_by_episode_id[episode_c["id"]]["applications_completed_today"] == 1
    assert due_by_episode_id[episode_a["id"]]["missed_slots_today"] == []
    assert due_by_episode_id[episode_b["id"]]["missed_slots_today"] == ["morning"]
    assert due_by_episode_id[episode_c["id"]]["missed_slots_today"] == []
    assert all(item["applications_expected_today"] == 2 for item in due_by_episode_id.values())


def test_phase_one_berlin_evening_application_satisfies_evening_slot(client, auth_headers, monkeypatch):
    import app.api as api
    import app.services as services
    from app.core.config import settings

    monkeypatch.setattr(settings, "deployment_timezone", "Europe/Berlin")
    monkeypatch.setattr(api, "utc_now", lambda: datetime(2026, 4, 6, 6, tzinfo=timezone.utc))
    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 4, 6, 6, tzinfo=timezone.utc))
    episode = _create_episode(client, auth_headers, location_code="berlin_evening_done", location_name="Berlin evening done")

    logged_morning = client.post(
        "/applications",
        headers=auth_headers,
        json={"episode_id": episode["id"], "applied_at": "2026-04-06T07:00:00Z"},
    )
    assert logged_morning.status_code == 201
    logged_evening = client.post(
        "/applications",
        headers=auth_headers,
        # local 15:30 == 13:30 UTC.
        json={"episode_id": episode["id"], "applied_at": "2026-04-06T13:30:00Z"},
    )
    assert logged_evening.status_code == 201

    # local 16:00 == 14:00 UTC.
    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 4, 6, 14, tzinfo=timezone.utc))
    due = client.get("/episodes/due", headers=auth_headers).json()["due"]
    assert episode["id"] not in {item["episode_id"] for item in due}


def test_phase_one_berlin_episode_created_after_cutoff_expects_evening_only(client, auth_headers, monkeypatch):
    import app.api as api
    import app.services as services
    from app.core.config import settings

    monkeypatch.setattr(settings, "deployment_timezone", "Europe/Berlin")
    # local 15:00 == 13:00 UTC.
    monkeypatch.setattr(api, "utc_now", lambda: datetime(2026, 4, 6, 13, tzinfo=timezone.utc))
    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 4, 6, 13, tzinfo=timezone.utc))
    episode = _create_episode(client, auth_headers, location_code="berlin_after_cutoff", location_name="Berlin after cutoff")

    # local 15:05 == 13:05 UTC.
    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 4, 6, 13, 5, tzinfo=timezone.utc))
    due = client.get("/episodes/due", headers=auth_headers).json()["due"]
    assert len(due) == 1
    assert due[0]["episode_id"] == episode["id"]
    assert due[0]["due_slot"] == "evening"
    assert due[0]["applications_expected_today"] == 1
    assert due[0]["missed_slots_today"] == []


def test_phase_one_after_cutoff_marks_missed_morning_without_requiring_catchup(client, auth_headers, monkeypatch):
    import app.api as api
    import app.services as services

    monkeypatch.setattr(api, "utc_now", lambda: datetime(2026, 4, 6, 8, tzinfo=timezone.utc))
    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 4, 6, 8, tzinfo=timezone.utc))
    episode = _create_episode(client, auth_headers, location_code="missed_morning", location_name="Missed morning")
    episode_id = episode["id"]

    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 4, 6, 15, tzinfo=timezone.utc))
    due = client.get("/episodes/due", headers=auth_headers).json()["due"]
    assert len(due) == 1
    assert due[0]["episode_id"] == episode_id
    assert due[0]["due_slot"] == "evening"
    assert due[0]["missed_slots_today"] == ["morning"]
    assert due[0]["applications_completed_today"] == 0
    assert due[0]["applications_expected_today"] == 2

    logged_evening = client.post(
        "/applications",
        headers=auth_headers,
        json={"episode_id": episode_id, "applied_at": "2026-04-06T16:30:00Z"},
    )
    assert logged_evening.status_code == 201
    assert client.get("/episodes/due", headers=auth_headers).json()["due"] == []


def test_taper_due_returns_only_currently_due_items(client, auth_headers, monkeypatch):
    import app.services as services

    episode = _create_episode(client, auth_headers, location_code="taper_elbow", location_name="Taper elbow")
    episode_id = episode["id"]
    heal = client.post(f"/episodes/{episode_id}/heal", headers=auth_headers, json={"healed_at": "2026-04-05T08:00:00Z"})
    assert heal.status_code == 200

    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 4, 6, 8, tzinfo=timezone.utc))
    assert client.get("/episodes/due", headers=auth_headers).json()["due"] == []

    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 4, 7, 8, tzinfo=timezone.utc))
    due = client.get("/episodes/due", headers=auth_headers).json()["due"]
    assert len(due) == 1
    assert due[0]["episode_id"] == episode_id
    assert due[0]["current_phase_number"] == 2
    assert due[0]["due_slot"] is None
    assert due[0]["missed_slots_today"] == []


def test_due_read_catches_up_missed_taper_phase(client, auth_headers, monkeypatch):
    import app.api as api
    import app.services as services
    from app.core.database import SessionLocal
    from app.models import EczemaEpisode, EpisodeEvent, EpisodePhaseHistory

    episode = _create_taper_episode(client, auth_headers, location_code="missed_scheduler", healed_at="2026-01-01T08:00:00Z")
    now = datetime(2026, 2, 1, 8, tzinfo=timezone.utc)
    monkeypatch.setattr(api, "utc_now", lambda: now)
    monkeypatch.setattr(services, "utc_now", lambda: now)

    due = client.get("/episodes/due", headers=auth_headers)
    assert due.status_code == 200
    assert due.json()["due"][0]["episode_id"] == episode["id"]
    assert due.json()["due"][0]["current_phase_number"] == 3

    db = SessionLocal()
    try:
        stored = db.get(EczemaEpisode, episode["id"])
        assert stored.current_phase_number == 3
        histories = list(
            db.execute(
                select(EpisodePhaseHistory)
                .where(EpisodePhaseHistory.episode_id == episode["id"])
                .order_by(EpisodePhaseHistory.phase_number.asc())
            ).scalars()
        )
        assert [history.phase_number for history in histories] == [1, 2, 3]
        phase_events = list(
            db.execute(
                select(EpisodeEvent)
                .where(EpisodeEvent.episode_id == episode["id"], EpisodeEvent.event_type == "phase_entered")
                .order_by(EpisodeEvent.occurred_at.asc())
            ).scalars()
        )
        assert [event.payload["to_phase_number"] for event in phase_events] == [2, 3]
    finally:
        db.close()


def test_repeated_phase_catch_up_is_noop(client, auth_headers):
    from app.core.database import SessionLocal
    from app.models import EpisodeEvent, EpisodePhaseHistory
    from app.services import catch_up_episode_phases

    episode = _create_taper_episode(client, auth_headers, location_code="catchup_noop", healed_at="2026-01-01T08:00:00Z")
    now = datetime(2026, 2, 1, 8, tzinfo=timezone.utc)
    db = SessionLocal()
    try:
        first = catch_up_episode_phases(db, now, reason="startup")
        assert first.transition_count == 1
        history_count = db.execute(select(EpisodePhaseHistory).where(EpisodePhaseHistory.episode_id == episode["id"])).scalars().all()
        event_count = db.execute(
            select(EpisodeEvent).where(EpisodeEvent.episode_id == episode["id"], EpisodeEvent.event_type == "phase_entered")
        ).scalars().all()

        second = catch_up_episode_phases(db, now, reason="startup")
        assert second.transition_count == 0
        assert len(db.execute(select(EpisodePhaseHistory).where(EpisodePhaseHistory.episode_id == episode["id"])).scalars().all()) == len(history_count)
        assert (
            len(
                db.execute(
                    select(EpisodeEvent).where(EpisodeEvent.episode_id == episode["id"], EpisodeEvent.event_type == "phase_entered")
                )
                .scalars()
                .all()
            )
            == len(event_count)
        )
    finally:
        db.close()


def test_due_read_catch_up_is_account_scoped(client, auth_headers, monkeypatch):
    import app.api as api
    import app.services as services
    from app.core.database import SessionLocal
    from app.core.security import hash_password
    from app.models import Account, EczemaEpisode, EpisodeEvent, EpisodePhaseHistory

    account_episode = _create_taper_episode(client, auth_headers, location_code="scoped_account", healed_at="2026-01-01T08:00:00Z")
    db = SessionLocal()
    try:
        other = Account(username="other", password_hash=hash_password("pw"), is_active=True)
        db.add(other)
        db.commit()
        subject = services.create_subject(db, other, "Other child")
        location = services.create_location(db, other, "other_location", "Other location")
        other_episode = services.create_episode(
            db,
            other,
            subject.id,
            location.id,
            "v1",
            datetime(2026, 1, 1, 7, tzinfo=timezone.utc),
            "user",
            str(other.id),
        )
        other_episode = services.heal_episode(db, other, other_episode.id, datetime(2026, 1, 1, 8, tzinfo=timezone.utc), "user", str(other.id))
        other_episode_id = other_episode.id
    finally:
        db.close()

    now = datetime(2026, 2, 1, 8, tzinfo=timezone.utc)
    monkeypatch.setattr(api, "utc_now", lambda: now)
    monkeypatch.setattr(services, "utc_now", lambda: now)
    assert client.get("/episodes/due", headers=auth_headers).status_code == 200

    db = SessionLocal()
    try:
        own = db.get(EczemaEpisode, account_episode["id"])
        other = db.get(EczemaEpisode, other_episode_id)
        assert own.current_phase_number == 3
        assert other.current_phase_number == 2
        other_histories = list(db.execute(select(EpisodePhaseHistory).where(EpisodePhaseHistory.episode_id == other_episode_id)).scalars())
        other_phase_events = list(
            db.execute(select(EpisodeEvent).where(EpisodeEvent.episode_id == other_episode_id, EpisodeEvent.event_type == "phase_entered")).scalars()
        )
        assert [history.phase_number for history in other_histories] == [1, 2]
        assert [event.payload["to_phase_number"] for event in other_phase_events] == [2]
    finally:
        db.close()


def test_phase_catch_up_uses_europe_berlin_local_day(client, auth_headers, monkeypatch):
    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.models import EczemaEpisode
    from app.services import catch_up_episode_phases

    monkeypatch.setattr(settings, "deployment_timezone", "Europe/Berlin")
    episode = _create_taper_episode(client, auth_headers, location_code="berlin_catchup", healed_at="2026-01-01T23:30:00Z")
    db = SessionLocal()
    try:
        before_local_day = catch_up_episode_phases(db, datetime(2026, 1, 29, 22, 0, tzinfo=timezone.utc), reason="startup")
        assert before_local_day.transition_count == 0
        stored = db.get(EczemaEpisode, episode["id"])
        assert stored.current_phase_number == 2

        on_local_day = catch_up_episode_phases(db, datetime(2026, 1, 29, 23, 0, tzinfo=timezone.utc), reason="startup")
        assert on_local_day.transition_count == 1
        db.refresh(stored)
        assert stored.current_phase_number == 3
    finally:
        db.close()


def test_relapse_before_cutoff_is_immediately_due_for_morning_slot(client, auth_headers, monkeypatch):
    import app.services as services

    episode = _create_taper_episode(client, auth_headers, location_code="relapse_morning")
    episode_id = episode["id"]
    relapse = client.post(
        f"/episodes/{episode_id}/relapse",
        headers=auth_headers,
        json={"reported_at": "2026-04-06T10:00:00Z", "reason": "symptoms_returned"},
    )
    assert relapse.status_code == 200
    assert relapse.json()["episode"]["phase_started_at"].startswith("2026-04-06T10:00:00")

    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 4, 6, 10, 5, tzinfo=timezone.utc))
    due = client.get("/episodes/due", headers=auth_headers).json()["due"]
    assert len(due) == 1
    assert due[0]["episode_id"] == episode_id
    assert due[0]["due_slot"] == "morning"
    assert due[0]["applications_expected_today"] == 2


def test_relapse_before_cutoff_morning_then_evening_due(client, auth_headers, monkeypatch):
    import app.services as services

    episode = _create_taper_episode(client, auth_headers, location_code="relapse_morning_evening")
    episode_id = episode["id"]
    client.post(
        f"/episodes/{episode_id}/relapse",
        headers=auth_headers,
        json={"reported_at": "2026-04-06T10:00:00Z", "reason": "symptoms_returned"},
    )
    logged_morning = client.post(
        "/applications",
        headers=auth_headers,
        json={"episode_id": episode_id, "applied_at": "2026-04-06T10:30:00Z"},
    )
    assert logged_morning.status_code == 201

    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 4, 6, 11, tzinfo=timezone.utc))
    assert client.get("/episodes/due", headers=auth_headers).json()["due"] == []

    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 4, 6, 15, tzinfo=timezone.utc))
    due = client.get("/episodes/due", headers=auth_headers).json()["due"]
    assert len(due) == 1
    assert due[0]["episode_id"] == episode_id
    assert due[0]["due_slot"] == "evening"
    assert due[0]["missed_slots_today"] == []
    assert due[0]["applications_completed_today"] == 1
    assert due[0]["applications_expected_today"] == 2


def test_relapse_after_cutoff_is_immediately_due_for_evening_only(client, auth_headers, monkeypatch):
    import app.services as services

    episode = _create_taper_episode(client, auth_headers, location_code="relapse_evening")
    episode_id = episode["id"]
    relapse = client.post(
        f"/episodes/{episode_id}/relapse",
        headers=auth_headers,
        json={"reported_at": "2026-04-06T19:00:00Z", "reason": "symptoms_returned"},
    )
    assert relapse.status_code == 200
    assert relapse.json()["episode"]["phase_started_at"].startswith("2026-04-06T19:00:00")

    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 4, 6, 19, 5, tzinfo=timezone.utc))
    due = client.get("/episodes/due", headers=auth_headers).json()["due"]
    assert len(due) == 1
    assert due[0]["episode_id"] == episode_id
    assert due[0]["due_slot"] == "evening"
    assert due[0]["applications_expected_today"] == 1
    assert due[0]["missed_slots_today"] == []


def test_relapse_after_cutoff_evening_application_satisfies_day(client, auth_headers, monkeypatch):
    import app.services as services

    episode = _create_taper_episode(client, auth_headers, location_code="relapse_evening_done")
    episode_id = episode["id"]
    client.post(
        f"/episodes/{episode_id}/relapse",
        headers=auth_headers,
        json={"reported_at": "2026-04-06T19:00:00Z", "reason": "symptoms_returned"},
    )
    logged_evening = client.post(
        "/applications",
        headers=auth_headers,
        json={"episode_id": episode_id, "applied_at": "2026-04-06T19:15:00Z"},
    )
    assert logged_evening.status_code == 201

    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 4, 6, 20, tzinfo=timezone.utc))
    assert client.get("/episodes/due", headers=auth_headers).json()["due"] == []


def test_applications_before_relapse_do_not_satisfy_relapsed_phase_one_slot(client, auth_headers, monkeypatch):
    import app.services as services

    episode = _create_taper_episode(client, auth_headers, location_code="relapse_ignores_old_application")
    episode_id = episode["id"]
    old_application = client.post(
        "/applications",
        headers=auth_headers,
        json={"episode_id": episode_id, "applied_at": "2026-04-06T09:00:00Z"},
    )
    assert old_application.status_code == 201
    client.post(
        f"/episodes/{episode_id}/relapse",
        headers=auth_headers,
        json={"reported_at": "2026-04-06T10:00:00Z", "reason": "symptoms_returned"},
    )

    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 4, 6, 10, 5, tzinfo=timezone.utc))
    due = client.get("/episodes/due", headers=auth_headers).json()["due"]
    assert len(due) == 1
    assert due[0]["episode_id"] == episode_id
    assert due[0]["due_slot"] == "morning"
    assert due[0]["applications_completed_today"] == 0


def test_next_day_after_evening_relapse_resumes_two_phase_one_slots(client, auth_headers, monkeypatch):
    import app.services as services

    episode = _create_taper_episode(client, auth_headers, location_code="relapse_next_day")
    episode_id = episode["id"]
    client.post(
        f"/episodes/{episode_id}/relapse",
        headers=auth_headers,
        json={"reported_at": "2026-04-06T19:00:00Z", "reason": "symptoms_returned"},
    )
    client.post(
        "/applications",
        headers=auth_headers,
        json={"episode_id": episode_id, "applied_at": "2026-04-06T19:15:00Z"},
    )

    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 4, 7, 9, tzinfo=timezone.utc))
    due = client.get("/episodes/due", headers=auth_headers).json()["due"]
    assert len(due) == 1
    assert due[0]["episode_id"] == episode_id
    assert due[0]["due_slot"] == "morning"
    assert due[0]["applications_completed_today"] == 0
    assert due[0]["applications_expected_today"] == 2

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select


def _as_utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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


def test_calculate_phase_due_end_uses_canonical_progression_public_seam(monkeypatch):
    import app.services as services
    from app.core.config import settings
    from app.treatment_protocol import CANONICAL_V1

    berlin = ZoneInfo("Europe/Berlin")
    monkeypatch.setattr(settings, "deployment_timezone", "Europe/Berlin")
    calls = []
    original_progression = CANONICAL_V1.phase_progression

    def spy(**kwargs):
        calls.append(kwargs)
        return original_progression(**kwargs)

    monkeypatch.setattr(CANONICAL_V1, "phase_progression", spy)

    phase_started_at = datetime(2026, 3, 1, 2, 30, tzinfo=berlin)
    due_end_at = services.calculate_phase_due_end_at(phase_started_at, 2)

    assert due_end_at == datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc)
    assert due_end_at.astimezone(berlin) == datetime(2026, 3, 29, 3, 30, tzinfo=berlin)
    assert len(calls) == 1
    assert calls[0]["phase_number"] == 2
    assert calls[0]["phase_started_at"] == phase_started_at
    assert calls[0]["now"] == phase_started_at
    assert calls[0]["timezone"] == berlin


def test_calculate_phase_due_end_matches_canonical_v1_for_every_phase(monkeypatch):
    import app.services as services
    from app.core.config import settings

    monkeypatch.setattr(settings, "deployment_timezone", "UTC")
    phase_started_at = datetime(2026, 1, 1, 18, tzinfo=timezone.utc)
    expected_due_ends = {
        1: None,
        2: datetime(2026, 1, 29, 18, tzinfo=timezone.utc),
        3: datetime(2026, 1, 15, 18, tzinfo=timezone.utc),
        4: datetime(2026, 1, 15, 18, tzinfo=timezone.utc),
        5: datetime(2026, 1, 15, 18, tzinfo=timezone.utc),
        6: datetime(2026, 1, 15, 18, tzinfo=timezone.utc),
        7: datetime(2026, 1, 15, 18, tzinfo=timezone.utc),
        8: None,
    }

    for phase_number, expected_due_end_at in expected_due_ends.items():
        assert services.calculate_phase_due_end_at(phase_started_at, phase_number) == expected_due_end_at


def test_advance_episode_uses_canonical_local_date_trigger(client, auth_headers, monkeypatch):
    import app.api as api
    from app.core.config import settings
    from app.treatment_protocol import CANONICAL_V1

    monkeypatch.setattr(settings, "deployment_timezone", "UTC")
    episode = _create_taper_episode(client, auth_headers, location_code="canonical_advance", healed_at="2026-01-01T18:00:00Z")
    phase_started_at = datetime(2026, 1, 1, 18, tzinfo=timezone.utc)
    before_local_trigger = datetime(2026, 1, 28, 23, 59, tzinfo=timezone.utc)
    monkeypatch.setattr(api, "utc_now", lambda: before_local_trigger)

    before = client.post(f"/episodes/{episode['id']}/advance", headers=auth_headers)
    assert before.status_code == 409

    calls = []
    original_progression = CANONICAL_V1.phase_progression

    def spy(**kwargs):
        calls.append(kwargs)
        return original_progression(**kwargs)

    monkeypatch.setattr(CANONICAL_V1, "phase_progression", spy)
    exact_local_trigger = datetime(2026, 1, 29, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(api, "utc_now", lambda: exact_local_trigger)

    on_trigger = client.post(f"/episodes/{episode['id']}/advance", headers=auth_headers)

    assert on_trigger.status_code == 200
    assert on_trigger.json()["episode"]["current_phase_number"] == 3
    assert any(
        call["phase_number"] == 2
        and call["phase_started_at"] == phase_started_at
        and call["now"] == exact_local_trigger
        and call["timezone"] == ZoneInfo("UTC")
        for call in calls
    )


def test_auto_advance_and_obsolete(client, auth_headers):
    subject_id, location_id = _create_subject_location(client, auth_headers)
    episode = client.post("/episodes", headers=auth_headers, json={"subject_id": subject_id, "location_id": location_id}).json()["episode"]
    client.post(f"/episodes/{episode['id']}/heal", headers=auth_headers, json={"healed_at": "2025-12-05T00:00:00Z"})

    from app.core.database import SessionLocal
    from app.core.time import utc_now
    from app.services import auto_advance_due_episodes
    from app.models import EczemaEpisode

    db = SessionLocal()
    try:
        ep = db.get(EczemaEpisode, episode["id"])
        ep.phase_started_at = datetime(2025, 12, 5, tzinfo=timezone.utc)
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
    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 4, 6, 10, tzinfo=timezone.utc))
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
    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 4, 6, 17, tzinfo=timezone.utc))
    assert client.get("/episodes/due", headers=auth_headers).json()["due"] == []


def test_due_items_accepts_explicit_now_without_reading_service_clock(monkeypatch):
    import app.services as services
    from app.core.database import SessionLocal
    from app.models import Account
    from app.services import create_episode, create_location, create_subject, due_items

    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.username == "admin").one()
        subject = create_subject(db, account, "Explicit due clock")
        location = create_location(db, account, "explicit_due_clock", "Explicit due clock")
        episode = create_episode(
            db,
            account,
            subject.id,
            location.id,
            "v1",
            datetime(2026, 4, 6, 8, tzinfo=timezone.utc),
            "user",
            "test",
        )
        monkeypatch.setattr(services, "utc_now", lambda: (_ for _ in ()).throw(AssertionError("unexpected service clock read")))

        items = due_items(db, account, now=datetime(2026, 4, 6, 9, tzinfo=timezone.utc))

        assert [item["episode_id"] for item in items] == [episode.id]
    finally:
        db.close()


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


def test_phase_one_exact_cutoff_is_evening_due_with_missed_morning(client, auth_headers, monkeypatch):
    import app.api as api
    import app.services as services
    from app.core.config import settings

    monkeypatch.setattr(settings, "deployment_timezone", "Europe/Berlin")
    creation_now = datetime(2026, 4, 6, 6, tzinfo=timezone.utc)
    monkeypatch.setattr(api, "utc_now", lambda: creation_now)
    episode = _create_episode(client, auth_headers, location_code="berlin_exact_cutoff", location_name="Berlin exact cutoff")

    exact_cutoff = datetime(2026, 4, 6, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(api, "utc_now", lambda: exact_cutoff)
    monkeypatch.setattr(services, "utc_now", lambda: exact_cutoff)

    due = client.get("/episodes/due", headers=auth_headers).json()["due"]

    assert due == [
        {
            "episode_id": episode["id"],
            "subject_id": episode["subject_id"],
            "location_id": episode["location_id"],
            "current_phase_number": 1,
            "treatment_due_today": True,
            "next_due_at": "2026-04-06T12:00:00Z",
            "last_application_at": None,
            "due_slot": "evening",
            "missed_slots_today": ["morning"],
            "applications_completed_today": 0,
            "applications_expected_today": 2,
        }
    ]


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
    assert due[0]["next_due_at"] == "2026-04-06T12:00:00Z"
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
    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 4, 6, 17, tzinfo=timezone.utc))
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


def test_due_adapter_routes_each_active_episode_through_public_canonical_due_state(client, auth_headers, monkeypatch):
    import app.api as api
    import app.services as services
    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.models import TreatmentApplication
    from app.treatment_protocol import ApplicationInput, CANONICAL_V1

    monkeypatch.setattr(settings, "deployment_timezone", "Europe/Berlin")
    now = datetime(2026, 4, 6, 13, tzinfo=timezone.utc)
    monkeypatch.setattr(api, "utc_now", lambda: now)
    monkeypatch.setattr(services, "utc_now", lambda: now)
    phase_one = _create_episode(client, auth_headers, location_code="canonical_due_phase_one", location_name="Canonical due phase one")
    _create_taper_episode(
        client,
        auth_headers,
        location_code="canonical_due_taper",
        healed_at="2026-04-05T08:00:00Z",
    )
    db = SessionLocal()
    try:
        db.add(
            TreatmentApplication(
                episode_id=phase_one["id"],
                applied_at=datetime(2026, 4, 6, 7, tzinfo=timezone.utc),
                treatment_type="other",
                phase_number_snapshot=1,
                is_deleted=False,
                is_voided=False,
            )
        )
        db.commit()
    finally:
        db.close()

    calls = []
    original_due_state = CANONICAL_V1.due_state

    def spy(**kwargs):
        calls.append(kwargs)
        return original_due_state(**kwargs)

    monkeypatch.setattr(CANONICAL_V1, "due_state", spy)

    response = client.get("/episodes/due", headers=auth_headers)

    assert response.status_code == 200
    assert len(calls) == 2
    assert [call["phase_number"] for call in calls] == [1, 2]
    for call in calls:
        assert call["timezone"] == ZoneInfo("Europe/Berlin")
        assert call["now"].tzinfo is not None
        assert call["now"].utcoffset() is not None
        assert call["phase_started_at"].tzinfo is not None
        assert call["phase_started_at"].utcoffset() is not None
        assert all(isinstance(application, ApplicationInput) for application in call["applications"])
        assert all(application.applied_at.tzinfo is not None for application in call["applications"])


def test_due_adapter_uses_latest_valid_taper_application_as_anchor(client, auth_headers, monkeypatch):
    import app.api as api
    import app.services as services
    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.models import TreatmentApplication

    monkeypatch.setattr(settings, "deployment_timezone", "UTC")
    creation_now = datetime(2026, 5, 20, 8, tzinfo=timezone.utc)
    monkeypatch.setattr(api, "utc_now", lambda: creation_now)
    episode = _create_taper_episode(
        client,
        auth_headers,
        location_code="canonical_due_anchor",
        healed_at="2026-05-20T08:00:00Z",
    )

    db = SessionLocal()
    try:
        db.add_all(
            [
                TreatmentApplication(
                    episode_id=episode["id"],
                    applied_at=datetime(2026, 5, 21, 9, tzinfo=timezone.utc),
                    treatment_type="other",
                    phase_number_snapshot=2,
                    is_deleted=False,
                    is_voided=False,
                ),
                TreatmentApplication(
                    episode_id=episode["id"],
                    applied_at=datetime(2026, 5, 23, 9, tzinfo=timezone.utc),
                    treatment_type="other",
                    phase_number_snapshot=1,
                    is_deleted=False,
                    is_voided=False,
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    now = datetime(2026, 5, 24, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(api, "utc_now", lambda: now)
    monkeypatch.setattr(services, "utc_now", lambda: now)

    due = client.get("/episodes/due", headers=auth_headers).json()["due"]

    assert due == [
        {
            "episode_id": episode["id"],
            "subject_id": episode["subject_id"],
            "location_id": episode["location_id"],
            "current_phase_number": 2,
            "treatment_due_today": True,
            "next_due_at": "2026-05-24T00:00:00Z",
            "last_application_at": "2026-05-21T09:00:00Z",
            "due_slot": None,
            "missed_slots_today": [],
            "applications_completed_today": 0,
            "applications_expected_today": 1,
        }
    ]


def test_due_phase_one_ignores_invalid_applications_and_future_after_now(client, auth_headers, monkeypatch):
    import app.api as api
    import app.services as services
    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.models import TreatmentApplication

    monkeypatch.setattr(settings, "deployment_timezone", "UTC")
    creation_now = datetime(2026, 4, 6, 8, tzinfo=timezone.utc)
    monkeypatch.setattr(api, "utc_now", lambda: creation_now)
    episode = _create_episode(client, auth_headers, location_code="invalid_phase_one_apps", location_name="Invalid phase one apps")

    db = SessionLocal()
    try:
        db.add_all(
            [
                TreatmentApplication(
                    episode_id=episode["id"],
                    applied_at=datetime(2026, 4, 6, 7, 30, tzinfo=timezone.utc),
                    treatment_type="other",
                    phase_number_snapshot=1,
                    is_deleted=False,
                    is_voided=False,
                ),
                TreatmentApplication(
                    episode_id=episode["id"],
                    applied_at=datetime(2026, 4, 6, 8, 15, tzinfo=timezone.utc),
                    treatment_type="other",
                    phase_number_snapshot=2,
                    is_deleted=False,
                    is_voided=False,
                ),
                TreatmentApplication(
                    episode_id=episode["id"],
                    applied_at=datetime(2026, 4, 6, 8, 30, tzinfo=timezone.utc),
                    treatment_type="other",
                    phase_number_snapshot=1,
                    is_deleted=True,
                    is_voided=False,
                ),
                TreatmentApplication(
                    episode_id=episode["id"],
                    applied_at=datetime(2026, 4, 6, 8, 45, tzinfo=timezone.utc),
                    treatment_type="other",
                    phase_number_snapshot=1,
                    is_deleted=False,
                    is_voided=True,
                ),
                TreatmentApplication(
                    episode_id=episode["id"],
                    applied_at=datetime(2026, 4, 6, 10, tzinfo=timezone.utc),
                    treatment_type="other",
                    phase_number_snapshot=1,
                    is_deleted=False,
                    is_voided=False,
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    now = datetime(2026, 4, 6, 9, tzinfo=timezone.utc)
    monkeypatch.setattr(api, "utc_now", lambda: now)
    monkeypatch.setattr(services, "utc_now", lambda: now)

    due = client.get("/episodes/due", headers=auth_headers).json()["due"]

    assert due == [
        {
            "episode_id": episode["id"],
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


def test_due_taper_ignores_invalid_applications_for_anchor_and_last_application(client, auth_headers, monkeypatch):
    import app.api as api
    import app.services as services
    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.models import TreatmentApplication

    monkeypatch.setattr(settings, "deployment_timezone", "UTC")
    creation_now = datetime(2026, 5, 20, 8, tzinfo=timezone.utc)
    monkeypatch.setattr(api, "utc_now", lambda: creation_now)
    episode = _create_taper_episode(
        client,
        auth_headers,
        location_code="invalid_taper_apps",
        healed_at="2026-05-20T08:00:00Z",
    )

    db = SessionLocal()
    try:
        db.add_all(
            [
                TreatmentApplication(
                    episode_id=episode["id"],
                    applied_at=datetime(2026, 5, 20, 7, tzinfo=timezone.utc),
                    treatment_type="other",
                    phase_number_snapshot=2,
                    is_deleted=False,
                    is_voided=False,
                ),
                TreatmentApplication(
                    episode_id=episode["id"],
                    applied_at=datetime(2026, 5, 21, 9, tzinfo=timezone.utc),
                    treatment_type="other",
                    phase_number_snapshot=2,
                    is_deleted=True,
                    is_voided=False,
                ),
                TreatmentApplication(
                    episode_id=episode["id"],
                    applied_at=datetime(2026, 5, 21, 10, tzinfo=timezone.utc),
                    treatment_type="other",
                    phase_number_snapshot=2,
                    is_deleted=False,
                    is_voided=True,
                ),
                TreatmentApplication(
                    episode_id=episode["id"],
                    applied_at=datetime(2026, 5, 23, 9, tzinfo=timezone.utc),
                    treatment_type="other",
                    phase_number_snapshot=1,
                    is_deleted=False,
                    is_voided=False,
                ),
                TreatmentApplication(
                    episode_id=episode["id"],
                    applied_at=datetime(2026, 5, 25, 9, tzinfo=timezone.utc),
                    treatment_type="other",
                    phase_number_snapshot=2,
                    is_deleted=False,
                    is_voided=False,
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    now = datetime(2026, 5, 24, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(api, "utc_now", lambda: now)
    monkeypatch.setattr(services, "utc_now", lambda: now)

    due = client.get("/episodes/due", headers=auth_headers).json()["due"]

    assert due == [
        {
            "episode_id": episode["id"],
            "subject_id": episode["subject_id"],
            "location_id": episode["location_id"],
            "current_phase_number": 2,
            "treatment_due_today": True,
            "next_due_at": "2026-05-24T00:00:00Z",
            "last_application_at": None,
            "due_slot": None,
            "missed_slots_today": [],
            "applications_completed_today": 0,
            "applications_expected_today": 1,
        }
    ]


def test_due_uses_canonical_taper_cadence_not_mutated_database_mirror(client, auth_headers, monkeypatch):
    import app.api as api
    import app.services as services
    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.models import TaperProtocolPhase

    monkeypatch.setattr(settings, "deployment_timezone", "UTC")
    creation_now = datetime(2026, 5, 20, 8, tzinfo=timezone.utc)
    monkeypatch.setattr(api, "utc_now", lambda: creation_now)
    episode = _create_taper_episode(
        client,
        auth_headers,
        location_code="mutated_due_mirror",
        healed_at="2026-05-20T08:00:00Z",
    )

    db = SessionLocal()
    try:
        phase_two = db.get(TaperProtocolPhase, 2)
        phase_two.apply_every_n_days = 7
        phase_two.applications_per_day = 9
        db.commit()
    finally:
        db.close()

    now = datetime(2026, 5, 22, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(api, "utc_now", lambda: now)
    monkeypatch.setattr(services, "utc_now", lambda: now)

    due = client.get("/episodes/due", headers=auth_headers).json()["due"]

    assert due == [
        {
            "episode_id": episode["id"],
            "subject_id": episode["subject_id"],
            "location_id": episode["location_id"],
            "current_phase_number": 2,
            "treatment_due_today": True,
            "next_due_at": "2026-05-22T00:00:00Z",
            "last_application_at": None,
            "due_slot": None,
            "missed_slots_today": [],
            "applications_completed_today": 0,
            "applications_expected_today": 1,
        }
    ]


def test_due_serializes_berlin_fold_boundaries_as_utc_instants(client, auth_headers, monkeypatch):
    import app.api as api
    import app.services as services
    from app.core.config import settings

    monkeypatch.setattr(settings, "deployment_timezone", "Europe/Berlin")
    creation_now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(api, "utc_now", lambda: creation_now)
    episode = _create_episode(client, auth_headers, location_code="berlin_fold_due", location_name="Berlin fold due")

    now = datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(api, "utc_now", lambda: now)
    monkeypatch.setattr(services, "utc_now", lambda: now)

    due = client.get("/episodes/due", headers=auth_headers).json()["due"]

    assert due == [
        {
            "episode_id": episode["id"],
            "subject_id": episode["subject_id"],
            "location_id": episode["location_id"],
            "current_phase_number": 1,
            "treatment_due_today": True,
            "next_due_at": "2026-10-24T22:00:00Z",
            "last_application_at": None,
            "due_slot": "morning",
            "missed_slots_today": [],
            "applications_completed_today": 0,
            "applications_expected_today": 2,
        }
    ]


def test_due_serializes_santiago_midnight_gap_as_normalized_utc_instant(client, auth_headers, monkeypatch):
    import app.api as api
    import app.services as services
    from app.core.config import settings

    monkeypatch.setattr(settings, "deployment_timezone", "America/Santiago")
    creation_now = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(api, "utc_now", lambda: creation_now)
    episode = _create_episode(client, auth_headers, location_code="santiago_gap_due", location_name="Santiago gap due")

    now = datetime(2026, 9, 6, 4, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(api, "utc_now", lambda: now)
    monkeypatch.setattr(services, "utc_now", lambda: now)

    due = client.get("/episodes/due", headers=auth_headers).json()["due"]

    assert due == [
        {
            "episode_id": episode["id"],
            "subject_id": episode["subject_id"],
            "location_id": episode["location_id"],
            "current_phase_number": 1,
            "treatment_due_today": True,
            "next_due_at": "2026-09-06T04:00:00Z",
            "last_application_at": None,
            "due_slot": "morning",
            "missed_slots_today": [],
            "applications_completed_today": 0,
            "applications_expected_today": 2,
        }
    ]


def test_due_contract_preserves_keys_types_scope_order_and_obsolete_exclusion(client, auth_headers, monkeypatch):
    import app.api as api
    import app.services as services
    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.core.security import hash_password
    from app.models import Account, EczemaEpisode

    monkeypatch.setattr(settings, "deployment_timezone", "UTC")
    now = datetime(2026, 4, 6, 9, tzinfo=timezone.utc)
    monkeypatch.setattr(api, "utc_now", lambda: now)
    monkeypatch.setattr(services, "utc_now", lambda: now)
    first = _create_episode(client, auth_headers, location_code="contract_first", location_name="Contract first")
    second = _create_episode(client, auth_headers, location_code="contract_second", location_name="Contract second")
    obsolete = _create_episode(client, auth_headers, location_code="contract_obsolete", location_name="Contract obsolete")

    db = SessionLocal()
    try:
        other = Account(username="contract-other", password_hash=hash_password("pw"), is_active=True)
        db.add(other)
        db.commit()
        other_subject = services.create_subject(db, other, "Other child")
        other_location = services.create_location(db, other, "contract_other", "Contract other")
        services.create_episode(
            db,
            other,
            other_subject.id,
            other_location.id,
            "v1",
            now,
            "user",
            str(other.id),
        )
        stored_obsolete = db.get(EczemaEpisode, obsolete["id"])
        stored_obsolete.status = "obsolete"
        db.commit()
    finally:
        db.close()

    response = client.get("/episodes/due", headers=auth_headers)
    assert response.status_code == 200
    due = response.json()["due"]
    assert [item["episode_id"] for item in due] == [first["id"], second["id"]]
    assert [
        "episode_id",
        "subject_id",
        "location_id",
        "current_phase_number",
        "treatment_due_today",
        "next_due_at",
        "last_application_at",
        "due_slot",
        "missed_slots_today",
        "applications_completed_today",
        "applications_expected_today",
    ] == list(due[0])
    assert due[0] == {
        "episode_id": first["id"],
        "subject_id": first["subject_id"],
        "location_id": first["location_id"],
        "current_phase_number": 1,
        "treatment_due_today": True,
        "next_due_at": "2026-04-06T00:00:00Z",
        "last_application_at": None,
        "due_slot": "morning",
        "missed_slots_today": [],
        "applications_completed_today": 0,
        "applications_expected_today": 2,
    }
    assert isinstance(due[0]["episode_id"], int)
    assert isinstance(due[0]["subject_id"], int)
    assert isinstance(due[0]["location_id"], int)
    assert isinstance(due[0]["current_phase_number"], int)
    assert isinstance(due[0]["treatment_due_today"], bool)
    assert isinstance(due[0]["next_due_at"], str) and due[0]["next_due_at"].endswith("Z")
    assert due[0]["last_application_at"] is None
    assert isinstance(due[0]["missed_slots_today"], list)
    assert isinstance(due[0]["applications_completed_today"], int)
    assert isinstance(due[0]["applications_expected_today"], int)

    scoped = client.get(f"/episodes/due?subject_id={second['subject_id']}", headers=auth_headers)
    assert [item["episode_id"] for item in scoped.json()["due"]] == [second["id"]]


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


def test_phase_catch_up_uses_one_canonical_decision_for_all_due_transitions(client, auth_headers, monkeypatch):
    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.models import EczemaEpisode, EpisodeEvent, EpisodePhaseHistory
    from app.services import catch_up_episode_phases
    from app.treatment_protocol import CANONICAL_V1

    monkeypatch.setattr(settings, "deployment_timezone", "UTC")
    episode = _create_taper_episode(client, auth_headers, location_code="canonical_catchup", healed_at="2026-01-01T18:00:00Z")
    run_at = datetime(2026, 4, 10, 12, tzinfo=timezone.utc)
    calls = []
    original_progression = CANONICAL_V1.phase_progression

    def spy(**kwargs):
        calls.append(kwargs)
        return original_progression(**kwargs)

    monkeypatch.setattr(CANONICAL_V1, "phase_progression", spy)
    db = SessionLocal()
    try:
        result = catch_up_episode_phases(db, run_at, reason="startup")

        assert result.transition_count == 6
        assert result.transitions[0].transition_count == 6
        assert result.transitions[0].resulting_phase == 7
        assert result.transitions[0].status == "obsolete"
        decision_calls = [call for call in calls if call["now"] == run_at]
        assert len(decision_calls) == 1
        assert decision_calls[0]["phase_number"] == 2
        assert decision_calls[0]["timezone"] == ZoneInfo("UTC")
        stored = db.get(EczemaEpisode, episode["id"])
        assert stored.status == "obsolete"
        assert stored.current_phase_number == 7
        assert _as_utc(stored.obsolete_at) == datetime(2026, 4, 9, 18, tzinfo=timezone.utc)

        histories = list(
            db.execute(
                select(EpisodePhaseHistory)
                .where(EpisodePhaseHistory.episode_id == episode["id"])
                .order_by(EpisodePhaseHistory.id.asc())
            ).scalars()
        )
        assert [history.phase_number for history in histories] == [1, 2, 3, 4, 5, 6, 7]
        assert [history.reason for history in histories] == [
            "episode_created",
            "healed_marked",
            "auto_advance",
            "auto_advance",
            "auto_advance",
            "auto_advance",
            "auto_advance",
        ]
        phase_starts = [
            datetime(2026, 1, 1, 18, tzinfo=timezone.utc),
            datetime(2026, 1, 29, 18, tzinfo=timezone.utc),
            datetime(2026, 2, 12, 18, tzinfo=timezone.utc),
            datetime(2026, 2, 26, 18, tzinfo=timezone.utc),
            datetime(2026, 3, 12, 18, tzinfo=timezone.utc),
            datetime(2026, 3, 26, 18, tzinfo=timezone.utc),
        ]
        assert [_as_utc(history.started_at) for history in histories[1:]] == phase_starts
        assert [_as_utc(history.ended_at) for history in histories[1:]] == [
            datetime(2026, 1, 29, 18, tzinfo=timezone.utc),
            datetime(2026, 2, 12, 18, tzinfo=timezone.utc),
            datetime(2026, 2, 26, 18, tzinfo=timezone.utc),
            datetime(2026, 3, 12, 18, tzinfo=timezone.utc),
            datetime(2026, 3, 26, 18, tzinfo=timezone.utc),
            datetime(2026, 4, 9, 18, tzinfo=timezone.utc),
        ]

        events = list(
            db.execute(
                select(EpisodeEvent)
                .where(EpisodeEvent.episode_id == episode["id"])
                .order_by(EpisodeEvent.id.asc())
            ).scalars()
        )
        assert [event.event_type for event in events] == [
            "episode_created",
            "healed_marked",
            "phase_entered",
            "phase_entered",
            "phase_entered",
            "phase_entered",
            "phase_entered",
            "phase_entered",
            "episode_obsoleted",
        ]
        phase_events = [event for event in events if event.event_type == "phase_entered"]
        assert [
            (
                event.payload["from_phase_number"],
                event.payload["to_phase_number"],
                event.payload["reason"],
            )
            for event in phase_events
        ] == [
            (1, 2, "healed_marked"),
            (2, 3, "auto_advance"),
            (3, 4, "auto_advance"),
            (4, 5, "auto_advance"),
            (5, 6, "auto_advance"),
            (6, 7, "auto_advance"),
        ]
        assert [event.actor_type for event in phase_events] == ["user", "system", "system", "system", "system", "system"]
        assert [event.actor_id for event in phase_events[1:]] == ["system:phase-advance"] * 5
        # Heal retains aware API-input serialization; auto-transition starts are SQLite-reloaded naive timestamps.
        assert [event.payload["started_at"] for event in phase_events] == [
            "2026-01-01T18:00:00+00:00",
            "2026-01-29T18:00:00",
            "2026-02-12T18:00:00",
            "2026-02-26T18:00:00",
            "2026-03-12T18:00:00",
            "2026-03-26T18:00:00",
        ]
        # Newly calculated due-end values are serialized from aware UTC datetimes.
        assert [event.payload["due_end_at"] for event in phase_events] == [
            "2026-01-29T18:00:00+00:00",
            "2026-02-12T18:00:00+00:00",
            "2026-02-26T18:00:00+00:00",
            "2026-03-12T18:00:00+00:00",
            "2026-03-26T18:00:00+00:00",
            "2026-04-09T18:00:00+00:00",
        ]
        assert [_as_utc(event.occurred_at) for event in phase_events] == [
            datetime(2026, 1, 1, 18, tzinfo=timezone.utc),
            datetime(2026, 1, 29, 18, tzinfo=timezone.utc),
            datetime(2026, 2, 12, 18, tzinfo=timezone.utc),
            datetime(2026, 2, 26, 18, tzinfo=timezone.utc),
            datetime(2026, 3, 12, 18, tzinfo=timezone.utc),
            datetime(2026, 3, 26, 18, tzinfo=timezone.utc),
        ]
        obsoleted = events[-1]
        assert obsoleted.event_type == "episode_obsoleted"
        assert _as_utc(obsoleted.occurred_at) == datetime(2026, 4, 9, 18, tzinfo=timezone.utc)
        assert obsoleted.actor_type == "system"
        assert obsoleted.actor_id == "system:phase-advance"
        assert obsoleted.payload == {
            "final_phase_number": 7,
            "obsoleted_at": "2026-04-09T18:00:00",
            "reason": "protocol_completed",
        }

        history_count = len(histories)
        event_count = len(events)
        repeated = catch_up_episode_phases(db, run_at, reason="startup")
        assert repeated.changed_count == 0
        assert repeated.transition_count == 0
        assert repeated.transitions == []
        assert len(db.execute(select(EpisodePhaseHistory).where(EpisodePhaseHistory.episode_id == episode["id"])).scalars().all()) == history_count
        assert len(db.execute(select(EpisodeEvent).where(EpisodeEvent.episode_id == episode["id"])).scalars().all()) == event_count
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


def test_phase_catch_up_missing_due_end_is_a_noop(client, auth_headers, monkeypatch):
    from app.core.database import SessionLocal
    from app.models import EczemaEpisode, EpisodeEvent, EpisodePhaseHistory
    from app.services import catch_up_episode_phases
    from app.treatment_protocol import CANONICAL_V1

    episode = _create_taper_episode(client, auth_headers, location_code="catchup_missing_due_end", healed_at="2026-01-01T08:00:00Z")
    run_at = datetime(2026, 2, 1, 8, tzinfo=timezone.utc)
    calls = []
    original_progression = CANONICAL_V1.phase_progression

    def spy(**kwargs):
        calls.append(kwargs)
        return original_progression(**kwargs)

    monkeypatch.setattr(CANONICAL_V1, "phase_progression", spy)
    db = SessionLocal()
    try:
        stored = db.get(EczemaEpisode, episode["id"])
        stored.phase_due_end_at = None
        db.commit()
        db.refresh(stored)
        before_episode = (stored.status, stored.current_phase_number, stored.phase_started_at, stored.phase_due_end_at)
        before_histories = [
            (history.phase_number, history.started_at, history.ended_at, history.reason)
            for history in db.execute(
                select(EpisodePhaseHistory)
                .where(EpisodePhaseHistory.episode_id == episode["id"])
                .order_by(EpisodePhaseHistory.id.asc())
            ).scalars()
        ]
        before_events = [
            (event.event_type, event.actor_type, event.actor_id, event.occurred_at, event.payload)
            for event in db.execute(
                select(EpisodeEvent)
                .where(EpisodeEvent.episode_id == episode["id"])
                .order_by(EpisodeEvent.id.asc())
            ).scalars()
        ]

        result = catch_up_episode_phases(db, run_at, reason="startup")

        assert result.changed_count == 0
        assert result.transition_count == 0
        assert result.transitions == []
        assert calls == []
        db.refresh(stored)
        assert (stored.status, stored.current_phase_number, stored.phase_started_at, stored.phase_due_end_at) == before_episode
        after_histories = [
            (history.phase_number, history.started_at, history.ended_at, history.reason)
            for history in db.execute(
                select(EpisodePhaseHistory)
                .where(EpisodePhaseHistory.episode_id == episode["id"])
                .order_by(EpisodePhaseHistory.id.asc())
            ).scalars()
        ]
        after_events = [
            (event.event_type, event.actor_type, event.actor_id, event.occurred_at, event.payload)
            for event in db.execute(
                select(EpisodeEvent)
                .where(EpisodeEvent.episode_id == episode["id"])
                .order_by(EpisodeEvent.id.asc())
            ).scalars()
        ]
        assert after_histories == before_histories
        assert after_events == before_events
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
    assert due[0]["next_due_at"] == "2026-04-07T00:00:00Z"
    assert due[0]["applications_completed_today"] == 0
    assert due[0]["applications_expected_today"] == 2

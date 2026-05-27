from __future__ import annotations

import io
from datetime import datetime, timezone

from app.core.database import SessionLocal
from app.models import BodyLocation, EczemaEpisode, EpisodeEvent, Subject, TreatmentApplication


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _create_tracking_snapshot(client, auth_headers) -> tuple[int, int, int]:
    subject = client.post("/subjects", headers=auth_headers, json={"display_name": "Child"}).json()
    location = client.post("/locations", headers=auth_headers, json={"code": "left_elbow", "display_name": "Left elbow"}).json()["location"]
    image = client.post(
        f"/locations/{location['id']}/image",
        headers=auth_headers,
        files={"image": ("left-elbow.png", PNG_BYTES, "image/png")},
    )
    assert image.status_code == 200
    episode = client.post(
        "/episodes",
        headers=auth_headers,
        json={"subject_id": subject["id"], "location_id": location["id"]},
    ).json()["episode"]
    application = client.post(
        "/applications",
        headers=auth_headers,
        json={
            "episode_id": episode["id"],
            "applied_at": "2026-05-27T07:00:00+00:00",
            "treatment_type": "steroid",
            "notes": "before export",
        },
    )
    assert application.status_code == 201
    return subject["id"], location["id"], episode["id"]


def test_api_export_import_roundtrip_replaces_tracking_data(client, auth_headers):
    _, original_location_id, _ = _create_tracking_snapshot(client, auth_headers)

    exported = client.get("/export", headers=auth_headers)
    assert exported.status_code == 200
    assert exported.headers["content-disposition"].startswith('attachment; filename="zema-export-')
    payload = exported.json()
    assert payload["format"] == "zema.account-export"
    assert payload["data"]["locations"][0]["image"]["data_base64"]

    extra_subject = client.post("/subjects", headers=auth_headers, json={"display_name": "Extra"}).json()
    extra_location = client.post("/locations", headers=auth_headers, json={"code": "extra", "display_name": "Extra"}).json()["location"]
    extra_episode = client.post(
        "/episodes",
        headers=auth_headers,
        json={"subject_id": extra_subject["id"], "location_id": extra_location["id"]},
    )
    assert extra_episode.status_code == 201

    imported = client.post(
        "/import",
        headers=auth_headers,
        files={"file": ("backup.json", io.BytesIO(exported.content), "application/json")},
    )
    assert imported.status_code == 200
    assert imported.json()["imported"] == {
        "subjects": 1,
        "locations": 1,
        "episodes": 1,
        "applications": 1,
        "events": 2,
        "adherence_days": 0,
    }

    subjects = client.get("/subjects", headers=auth_headers).json()["subjects"]
    locations = client.get("/locations", headers=auth_headers).json()["locations"]
    episodes = client.get("/episodes", headers=auth_headers).json()["episodes"]
    assert [subject["display_name"] for subject in subjects] == ["Child"]
    assert [location["display_name"] for location in locations] == ["Left elbow"]
    assert len(episodes) == 1
    assert episodes[0]["subject_id"] == subjects[0]["id"]
    assert episodes[0]["location_id"] == locations[0]["id"]
    assert locations[0]["id"] != extra_location["id"]

    image = client.get(f"/locations/{locations[0]['id']}/image", headers=auth_headers)
    assert image.status_code == 200
    assert image.content == PNG_BYTES
    assert client.get(f"/locations/{original_location_id}/image", headers=auth_headers).status_code in {200, 404}

    apps = client.get(f"/episodes/{episodes[0]['id']}/applications", headers=auth_headers).json()["applications"]
    assert len(apps) == 1
    assert apps[0]["notes"] == "before export"

    db = SessionLocal()
    try:
        assert db.query(Subject).count() == 1
        assert db.query(BodyLocation).count() == 1
        assert db.query(EczemaEpisode).count() == 1
        assert db.query(TreatmentApplication).count() == 1
        logged_event = db.query(EpisodeEvent).filter(EpisodeEvent.event_type == "application_logged").one()
        assert logged_event.payload["application_id"] == apps[0]["id"]
    finally:
        db.close()


def test_import_rejects_non_zema_json(client, auth_headers):
    response = client.post(
        "/import",
        headers=auth_headers,
        files={"file": ("bad.json", io.BytesIO(b'{"hello": "world"}'), "application/json")},
    )

    assert response.status_code == 422

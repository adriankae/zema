from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now
from app.location_images import ALLOWED_IMAGE_TYPES
from app.models import (
    Account,
    BodyLocation,
    EczemaEpisode,
    EpisodeDailyAdherence,
    EpisodeEvent,
    EpisodePhaseHistory,
    Subject,
    TreatmentApplication,
)


EXPORT_FORMAT = "zema.account-export"
EXPORT_VERSION = 1


def export_account_data(db: Session, account: Account) -> dict[str, Any]:
    subjects = list(db.execute(select(Subject).where(Subject.account_id == account.id).order_by(Subject.id.asc())).scalars())
    locations = list(db.execute(select(BodyLocation).where(BodyLocation.account_id == account.id).order_by(BodyLocation.id.asc())).scalars())
    episodes = list(db.execute(select(EczemaEpisode).where(EczemaEpisode.account_id == account.id).order_by(EczemaEpisode.id.asc())).scalars())
    episode_ids = [episode.id for episode in episodes]

    phase_history = _for_episode_ids(db, EpisodePhaseHistory, episode_ids)
    applications = _for_episode_ids(db, TreatmentApplication, episode_ids)
    events = _for_episode_ids(db, EpisodeEvent, episode_ids)
    adherence = list(
        db.execute(
            select(EpisodeDailyAdherence)
            .where(EpisodeDailyAdherence.account_id == account.id)
            .order_by(EpisodeDailyAdherence.date.asc(), EpisodeDailyAdherence.id.asc())
        ).scalars()
    )

    return {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "exported_at": utc_now().isoformat(),
        "account": {"username": account.username},
        "data": {
            "subjects": [_subject_to_dict(subject) for subject in subjects],
            "locations": [_location_to_dict(location) for location in locations],
            "episodes": [_episode_to_dict(episode) for episode in episodes],
            "phase_history": [_phase_history_to_dict(row) for row in phase_history],
            "applications": [_application_to_dict(row) for row in applications],
            "events": [_event_to_dict(row) for row in events],
            "adherence": [_adherence_to_dict(row) for row in adherence],
        },
    }


def import_account_data(db: Session, account: Account, payload: dict[str, Any]) -> dict[str, int]:
    _validate_payload(payload)
    data = payload["data"]
    now = utc_now()
    old_image_keys = _clear_account_tracking_data(db, account)

    subject_map: dict[int, int] = {}
    location_map: dict[int, int] = {}
    episode_map: dict[int, int] = {}
    application_map: dict[int, int] = {}

    for item in data.get("subjects", []):
        subject = Subject(
            account_id=account.id,
            display_name=_required_str(item, "display_name"),
            created_at=_parse_datetime(item.get("created_at")) or now,
            updated_at=_parse_datetime(item.get("updated_at")) or now,
        )
        db.add(subject)
        db.flush()
        subject_map[_required_int(item, "id")] = subject.id

    for item in data.get("locations", []):
        image = item.get("image") if isinstance(item.get("image"), dict) else None
        location = BodyLocation(
            account_id=account.id,
            code=_required_str(item, "code"),
            display_name=_required_str(item, "display_name"),
            created_at=_parse_datetime(item.get("created_at")) or now,
        )
        db.add(location)
        db.flush()
        if image:
            _restore_location_image(location, image)
        location_map[_required_int(item, "id")] = location.id

    for item in data.get("episodes", []):
        episode = EczemaEpisode(
            account_id=account.id,
            subject_id=_mapped_id(subject_map, item, "subject_id"),
            location_id=_mapped_id(location_map, item, "location_id"),
            status=_required_str(item, "status"),
            current_phase_number=_required_int(item, "current_phase_number"),
            phase_started_at=_required_datetime(item, "phase_started_at"),
            phase_due_end_at=_parse_datetime(item.get("phase_due_end_at")),
            protocol_version=_required_str(item, "protocol_version"),
            healed_at=_parse_datetime(item.get("healed_at")),
            obsolete_at=_parse_datetime(item.get("obsolete_at")),
            created_at=_parse_datetime(item.get("created_at")) or now,
            updated_at=_parse_datetime(item.get("updated_at")) or now,
        )
        db.add(episode)
        db.flush()
        episode_map[_required_int(item, "id")] = episode.id

    for item in data.get("phase_history", []):
        db.add(
            EpisodePhaseHistory(
                episode_id=_mapped_id(episode_map, item, "episode_id"),
                phase_number=_required_int(item, "phase_number"),
                started_at=_required_datetime(item, "started_at"),
                ended_at=_parse_datetime(item.get("ended_at")),
                reason=_required_str(item, "reason"),
                created_at=_parse_datetime(item.get("created_at")) or now,
            )
        )

    for item in data.get("applications", []):
        application = TreatmentApplication(
            episode_id=_mapped_id(episode_map, item, "episode_id"),
            applied_at=_required_datetime(item, "applied_at"),
            treatment_type=_required_str(item, "treatment_type"),
            treatment_name=item.get("treatment_name"),
            quantity_text=item.get("quantity_text"),
            phase_number_snapshot=_required_int(item, "phase_number_snapshot"),
            notes=item.get("notes"),
            is_voided=bool(item.get("is_voided", False)),
            voided_at=_parse_datetime(item.get("voided_at")),
            is_deleted=bool(item.get("is_deleted", False)),
            deleted_at=_parse_datetime(item.get("deleted_at")),
            created_at=_parse_datetime(item.get("created_at")) or now,
            updated_at=_parse_datetime(item.get("updated_at")) or now,
        )
        db.add(application)
        db.flush()
        application_map[_required_int(item, "id")] = application.id

    for item in data.get("events", []):
        payload_data = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        db.add(
            EpisodeEvent(
                event_uuid=str(item.get("event_uuid") or uuid.uuid4()),
                episode_id=_mapped_id(episode_map, item, "episode_id"),
                event_type=_required_str(item, "event_type"),
                actor_type=_required_str(item, "actor_type"),
                actor_id=item.get("actor_id"),
                occurred_at=_required_datetime(item, "occurred_at"),
                payload=_remap_payload(payload_data, subject_map, location_map, episode_map, application_map),
                created_at=_parse_datetime(item.get("created_at")) or now,
            )
        )

    for item in data.get("adherence", []):
        db.add(
            EpisodeDailyAdherence(
                account_id=account.id,
                episode_id=_mapped_id(episode_map, item, "episode_id"),
                subject_id=_mapped_id(subject_map, item, "subject_id"),
                location_id=_mapped_id(location_map, item, "location_id"),
                date=_required_date(item, "date"),
                phase_number=_required_int(item, "phase_number"),
                expected_applications=_required_int(item, "expected_applications"),
                completed_applications=_required_int(item, "completed_applications"),
                credited_applications=_required_int(item, "credited_applications"),
                status=_required_str(item, "status"),
                source=_required_str(item, "source"),
                calculated_at=_required_datetime(item, "calculated_at"),
                finalized_at=_parse_datetime(item.get("finalized_at")),
                created_at=_parse_datetime(item.get("created_at")) or now,
                updated_at=_parse_datetime(item.get("updated_at")) or now,
            )
        )

    db.commit()
    for storage_key in old_image_keys:
        _delete_location_image_file(storage_key)
    return {
        "subjects": len(subject_map),
        "locations": len(location_map),
        "episodes": len(episode_map),
        "applications": len(application_map),
        "events": len(data.get("events", [])),
        "adherence_days": len(data.get("adherence", [])),
    }


def _for_episode_ids(db: Session, model, episode_ids: list[int]):
    if not episode_ids:
        return []
    return list(db.execute(select(model).where(model.episode_id.in_(episode_ids)).order_by(model.id.asc())).scalars())


def _clear_account_tracking_data(db: Session, account: Account) -> list[str]:
    locations = list(db.execute(select(BodyLocation).where(BodyLocation.account_id == account.id)).scalars())
    image_keys = [location.image_storage_key for location in locations if location.image_storage_key]
    episode_ids = list(db.execute(select(EczemaEpisode.id).where(EczemaEpisode.account_id == account.id)).scalars())
    if episode_ids:
        db.execute(delete(EpisodeDailyAdherence).where(EpisodeDailyAdherence.account_id == account.id))
        db.execute(delete(TreatmentApplication).where(TreatmentApplication.episode_id.in_(episode_ids)))
        db.execute(delete(EpisodePhaseHistory).where(EpisodePhaseHistory.episode_id.in_(episode_ids)))
        db.execute(delete(EpisodeEvent).where(EpisodeEvent.episode_id.in_(episode_ids)))
        db.execute(delete(EczemaEpisode).where(EczemaEpisode.account_id == account.id))
    else:
        db.execute(delete(EpisodeDailyAdherence).where(EpisodeDailyAdherence.account_id == account.id))
    db.execute(delete(Subject).where(Subject.account_id == account.id))
    db.execute(delete(BodyLocation).where(BodyLocation.account_id == account.id))
    db.flush()
    return image_keys


def _location_image_root() -> Path:
    root = Path(settings.location_image_dir).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _safe_image_path(storage_key: str) -> Path:
    root = _location_image_root()
    candidate = (root / storage_key).resolve()
    if root != candidate and root not in candidate.parents:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid image path in import")
    return candidate


def _delete_location_image_file(storage_key: str | None) -> None:
    if not storage_key:
        return
    try:
        _safe_image_path(storage_key).unlink(missing_ok=True)
    except OSError:
        pass


def _restore_location_image(location: BodyLocation, image: dict[str, Any]) -> None:
    mime_type = _required_str(image, "mime_type")
    if mime_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="unsupported image type in import")
    raw = base64.b64decode(_required_str(image, "data_base64"), validate=True)
    expected_sha = image.get("sha256")
    actual_sha = hashlib.sha256(raw).hexdigest()
    if expected_sha and expected_sha != actual_sha:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="image checksum mismatch in import")
    storage_key = f"account-{location.account_id}/location-{location.id}/{uuid.uuid4().hex}{ALLOWED_IMAGE_TYPES[mime_type]}"
    path = _safe_image_path(storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    location.image_storage_key = storage_key
    location.image_mime_type = mime_type
    location.image_size_bytes = len(raw)
    location.image_sha256 = actual_sha
    location.image_original_filename = image.get("original_filename")
    location.image_uploaded_at = _parse_datetime(image.get("uploaded_at")) or utc_now()


def _image_to_dict(location: BodyLocation) -> dict[str, Any] | None:
    if not location.image_storage_key or not location.image_mime_type:
        return None
    path = _safe_image_path(location.image_storage_key)
    if not path.exists() or not path.is_file():
        return None
    data = path.read_bytes()
    return {
        "mime_type": location.image_mime_type,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "original_filename": location.image_original_filename,
        "uploaded_at": _dt(location.image_uploaded_at),
        "data_base64": base64.b64encode(data).decode("ascii"),
    }


def _subject_to_dict(row: Subject) -> dict[str, Any]:
    return {"id": row.id, "display_name": row.display_name, "created_at": _dt(row.created_at), "updated_at": _dt(row.updated_at)}


def _location_to_dict(row: BodyLocation) -> dict[str, Any]:
    return {"id": row.id, "code": row.code, "display_name": row.display_name, "created_at": _dt(row.created_at), "image": _image_to_dict(row)}


def _episode_to_dict(row: EczemaEpisode) -> dict[str, Any]:
    return {
        "id": row.id,
        "subject_id": row.subject_id,
        "location_id": row.location_id,
        "status": row.status,
        "current_phase_number": row.current_phase_number,
        "phase_started_at": _dt(row.phase_started_at),
        "phase_due_end_at": _dt(row.phase_due_end_at),
        "protocol_version": row.protocol_version,
        "healed_at": _dt(row.healed_at),
        "obsolete_at": _dt(row.obsolete_at),
        "created_at": _dt(row.created_at),
        "updated_at": _dt(row.updated_at),
    }


def _phase_history_to_dict(row: EpisodePhaseHistory) -> dict[str, Any]:
    return {"id": row.id, "episode_id": row.episode_id, "phase_number": row.phase_number, "started_at": _dt(row.started_at), "ended_at": _dt(row.ended_at), "reason": row.reason, "created_at": _dt(row.created_at)}


def _application_to_dict(row: TreatmentApplication) -> dict[str, Any]:
    return {
        "id": row.id,
        "episode_id": row.episode_id,
        "applied_at": _dt(row.applied_at),
        "treatment_type": row.treatment_type,
        "treatment_name": row.treatment_name,
        "quantity_text": row.quantity_text,
        "phase_number_snapshot": row.phase_number_snapshot,
        "notes": row.notes,
        "is_voided": row.is_voided,
        "voided_at": _dt(row.voided_at),
        "is_deleted": row.is_deleted,
        "deleted_at": _dt(row.deleted_at),
        "created_at": _dt(row.created_at),
        "updated_at": _dt(row.updated_at),
    }


def _event_to_dict(row: EpisodeEvent) -> dict[str, Any]:
    return {"id": row.id, "event_uuid": row.event_uuid, "episode_id": row.episode_id, "event_type": row.event_type, "actor_type": row.actor_type, "actor_id": row.actor_id, "occurred_at": _dt(row.occurred_at), "payload": row.payload, "created_at": _dt(row.created_at)}


def _adherence_to_dict(row: EpisodeDailyAdherence) -> dict[str, Any]:
    return {
        "id": row.id,
        "episode_id": row.episode_id,
        "subject_id": row.subject_id,
        "location_id": row.location_id,
        "date": row.date.isoformat(),
        "phase_number": row.phase_number,
        "expected_applications": row.expected_applications,
        "completed_applications": row.completed_applications,
        "credited_applications": row.credited_applications,
        "status": row.status,
        "source": row.source,
        "calculated_at": _dt(row.calculated_at),
        "finalized_at": _dt(row.finalized_at),
        "created_at": _dt(row.created_at),
        "updated_at": _dt(row.updated_at),
    }


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _validate_payload(payload: dict[str, Any]) -> None:
    if payload.get("format") != EXPORT_FORMAT or payload.get("version") != EXPORT_VERSION or not isinstance(payload.get("data"), dict):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="unsupported import file")


def _required_str(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or value == "":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"missing {key} in import")
    return value


def _required_int(item: dict[str, Any], key: str) -> int:
    value = item.get(key)
    if not isinstance(value, int):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"missing {key} in import")
    return value


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid datetime in import")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _required_datetime(item: dict[str, Any], key: str) -> datetime:
    value = _parse_datetime(item.get(key))
    if value is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"missing {key} in import")
    return value


def _required_date(item: dict[str, Any], key: str) -> date:
    value = item.get(key)
    if not isinstance(value, str):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"missing {key} in import")
    return date.fromisoformat(value)


def _mapped_id(mapping: dict[int, int], item: dict[str, Any], key: str) -> int:
    old_id = _required_int(item, key)
    if old_id not in mapping:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"unknown {key} in import")
    return mapping[old_id]


def _remap_payload(payload: dict[str, Any], subject_map: dict[int, int], location_map: dict[int, int], episode_map: dict[int, int], application_map: dict[int, int]) -> dict[str, Any]:
    remapped = dict(payload)
    for key, mapping in {
        "subject_id": subject_map,
        "location_id": location_map,
        "episode_id": episode_map,
        "application_id": application_map,
    }.items():
        value = remapped.get(key)
        if isinstance(value, int) and value in mapping:
            remapped[key] = mapping[value]
    return remapped

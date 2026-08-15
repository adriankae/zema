from dataclasses import replace
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import main as main_module
from app.core.database import SessionLocal
from app.models import Account, BodyLocation, EczemaEpisode, Subject, TaperProtocolPhase, TreatmentApplication
from app.services import validate_protocol_mirror as validate_database_protocol_mirror
from app.treatment_protocol import (
    CANONICAL_V1,
    PhaseDefinition,
    ProtocolMirrorMismatchError,
    validate_protocol_mirror,
)


def test_protocol_mirror_validator_accepts_the_exact_canonical_mirror():
    phases = tuple(CANONICAL_V1.phases)

    assert validate_protocol_mirror(phases) is None
    assert phases == CANONICAL_V1.phases


@pytest.mark.parametrize(
    ("phase_number", "field", "expected", "actual"),
    [
        (2, "duration_days", 28, 27),
        (2, "apply_every_n_days", 2, 3),
        (2, "applications_per_day", 1, 2),
    ],
)
def test_protocol_mirror_validator_reports_the_first_mismatching_field(
    phase_number, field, expected, actual
):
    phases = list(CANONICAL_V1.phases)
    phases[phase_number - 1] = replace(phases[phase_number - 1], **{field: actual})

    with pytest.raises(ProtocolMirrorMismatchError) as raised:
        validate_protocol_mirror(phases)

    mismatch = raised.value
    assert mismatch.phase_number == phase_number
    assert mismatch.field == field
    assert mismatch.expected == expected
    assert mismatch.actual == actual
    assert mismatch.diagnostic == {
        "phase_number": phase_number,
        "field": field,
        "expected": expected,
        "actual": actual,
    }
    assert str(mismatch) == (
        f"protocol mirror mismatch: phase {phase_number}, field {field}, "
        f"expected {expected}, actual {actual}"
    )


def test_protocol_mirror_validator_reports_a_missing_canonical_phase():
    phases = tuple(phase for phase in CANONICAL_V1.phases if phase.phase_number != 4)

    with pytest.raises(ProtocolMirrorMismatchError) as raised:
        validate_protocol_mirror(phases)

    assert raised.value.diagnostic == {
        "phase_number": 4,
        "field": "phase",
        "expected": "present",
        "actual": "missing",
    }


def test_protocol_mirror_validator_reports_an_unexpected_extra_phase():
    phases = (*CANONICAL_V1.phases, PhaseDefinition(8, 7, 8, 1))

    with pytest.raises(ProtocolMirrorMismatchError) as raised:
        validate_protocol_mirror(phases)

    assert raised.value.diagnostic == {
        "phase_number": 8,
        "field": "phase",
        "expected": "absent",
        "actual": "present",
    }


def test_database_protocol_mirror_adapter_accepts_canonical_rows_without_session_writes():
    db = SessionLocal()
    try:
        before_rows = [
            (row.phase_number, row.duration_days, row.apply_every_n_days, row.applications_per_day)
            for row in db.execute(select(TaperProtocolPhase).order_by(TaperProtocolPhase.phase_number.asc())).scalars()
        ]
        pending_treatment = TreatmentApplication(
            episode_id=999,
            applied_at=datetime(2026, 1, 3, 8, tzinfo=timezone.utc),
            treatment_type="other",
            phase_number_snapshot=1,
        )
        db.add(pending_treatment)
        before_state = (tuple(db.new), tuple(db.dirty), tuple(db.deleted))

        assert validate_database_protocol_mirror(db) is None

        after_rows = [
            (row.phase_number, row.duration_days, row.apply_every_n_days, row.applications_per_day)
            for row in db.execute(select(TaperProtocolPhase).order_by(TaperProtocolPhase.phase_number.asc())).scalars()
        ]
        assert after_rows == before_rows
        assert (tuple(db.new), tuple(db.dirty), tuple(db.deleted)) == before_state
    finally:
        db.close()


def test_database_protocol_mirror_adapter_reports_mismatch_without_repairing_protocol_or_treatment_data():
    db = SessionLocal()
    try:
        account = db.execute(select(Account).order_by(Account.id.asc())).scalar_one()
        subject = Subject(account_id=account.id, display_name="Test subject")
        location = BodyLocation(account_id=account.id, code="arm", display_name="Arm")
        db.add_all([subject, location])
        db.flush()
        episode = EczemaEpisode(
            account_id=account.id,
            subject_id=subject.id,
            location_id=location.id,
            status="in_taper",
            current_phase_number=2,
            phase_started_at=datetime(2026, 1, 1, 8, tzinfo=timezone.utc),
            phase_due_end_at=datetime(2026, 1, 29, 8, tzinfo=timezone.utc),
            protocol_version="v1",
        )
        db.add(episode)
        db.flush()
        db.add(
            TreatmentApplication(
                episode_id=episode.id,
                applied_at=datetime(2026, 1, 3, 8, tzinfo=timezone.utc),
                treatment_type="other",
                phase_number_snapshot=2,
            )
        )
        db.commit()

        phase = db.get(TaperProtocolPhase, 2)
        phase.duration_days = 27
        db.commit()
        before_protocol = [
            (row.phase_number, row.duration_days, row.apply_every_n_days, row.applications_per_day)
            for row in db.execute(select(TaperProtocolPhase).order_by(TaperProtocolPhase.phase_number.asc())).scalars()
        ]
        before_treatment = [
            (row.id, row.episode_id, row.applied_at, row.treatment_type, row.phase_number_snapshot)
            for row in db.execute(select(TreatmentApplication).order_by(TreatmentApplication.id.asc())).scalars()
        ]
        before_state = (tuple(db.new), tuple(db.dirty), tuple(db.deleted))

        with pytest.raises(ProtocolMirrorMismatchError) as raised:
            validate_database_protocol_mirror(db)

        assert raised.value.diagnostic == {
            "phase_number": 2,
            "field": "duration_days",
            "expected": 28,
            "actual": 27,
        }
        after_protocol = [
            (row.phase_number, row.duration_days, row.apply_every_n_days, row.applications_per_day)
            for row in db.execute(select(TaperProtocolPhase).order_by(TaperProtocolPhase.phase_number.asc())).scalars()
        ]
        after_treatment = [
            (row.id, row.episode_id, row.applied_at, row.treatment_type, row.phase_number_snapshot)
            for row in db.execute(select(TreatmentApplication).order_by(TreatmentApplication.id.asc())).scalars()
        ]
        assert after_protocol == before_protocol
        assert after_treatment == before_treatment
        assert (tuple(db.new), tuple(db.dirty), tuple(db.deleted)) == before_state
    finally:
        db.close()


def test_matching_lifespan_validates_before_catch_up_and_scheduler(monkeypatch):
    events = []
    real_bootstrap = main_module.bootstrap_data
    real_validate = main_module.validate_protocol_mirror
    real_catch_up = main_module.catch_up_episode_phases

    def bootstrap(db):
        events.append("bootstrap")
        return real_bootstrap(db)

    def validate(db):
        events.append("validate")
        return real_validate(db)

    def catch_up(*args, **kwargs):
        events.append("catch_up")
        return real_catch_up(*args, **kwargs)

    def scheduler():
        events.append("scheduler")
        return None

    monkeypatch.setattr(main_module, "bootstrap_data", bootstrap)
    monkeypatch.setattr(main_module, "validate_protocol_mirror", validate)
    monkeypatch.setattr(main_module, "catch_up_episode_phases", catch_up)
    monkeypatch.setattr(main_module, "start_scheduler", scheduler)

    with TestClient(main_module.app) as test_client:
        response = test_client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert events == ["bootstrap", "validate", "catch_up", "scheduler"]


def test_mismatching_lifespan_fails_before_catch_up_and_scheduler_and_closes_session(monkeypatch):
    db = SessionLocal()
    try:
        phase = db.get(TaperProtocolPhase, 2)
        phase.duration_days = 27
        db.commit()
    finally:
        db.close()

    class TrackingSession:
        def __init__(self):
            self.session = SessionLocal()
            self.closed = False

        def close(self):
            self.closed = True
            self.session.close()

        def __getattr__(self, name):
            return getattr(self.session, name)

    tracking_db = TrackingSession()
    events = []
    real_bootstrap = main_module.bootstrap_data
    real_validate = main_module.validate_protocol_mirror

    def bootstrap(db):
        events.append("bootstrap")
        return real_bootstrap(db)

    def validate(db):
        events.append("validate")
        return real_validate(db)

    def unexpected_catch_up(*args, **kwargs):
        raise AssertionError("startup catch-up must not run after mirror mismatch")

    def unexpected_scheduler():
        raise AssertionError("scheduler must not start after mirror mismatch")

    monkeypatch.setattr(main_module, "SessionLocal", lambda: tracking_db)
    monkeypatch.setattr(main_module, "bootstrap_data", bootstrap)
    monkeypatch.setattr(main_module, "validate_protocol_mirror", validate)
    monkeypatch.setattr(main_module, "catch_up_episode_phases", unexpected_catch_up)
    monkeypatch.setattr(main_module, "start_scheduler", unexpected_scheduler)

    with pytest.raises(ProtocolMirrorMismatchError) as raised:
        with TestClient(main_module.app):
            pass

    assert raised.value.diagnostic == {
        "phase_number": 2,
        "field": "duration_days",
        "expected": 28,
        "actual": 27,
    }
    assert events == ["bootstrap", "validate"]
    assert tracking_db.closed is True


def test_post_start_health_mismatch_returns_503_without_repairing_the_mirror(client):
    db = SessionLocal()
    try:
        phase = db.get(TaperProtocolPhase, 2)
        phase.duration_days = 27
        db.commit()
    finally:
        db.close()

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {
        "status": "error",
        "protocol_mirror": {
            "phase_number": 2,
            "field": "duration_days",
            "expected": 28,
            "actual": 27,
        },
    }

    db = SessionLocal()
    try:
        assert db.get(TaperProtocolPhase, 2).duration_days == 27
    finally:
        db.close()


def test_health_becomes_healthy_again_after_the_mirror_is_restored(client):
    db = SessionLocal()
    try:
        phase = db.get(TaperProtocolPhase, 2)
        phase.duration_days = 27
        db.commit()
    finally:
        db.close()

    mismatch = client.get("/health")
    assert mismatch.status_code == 503

    db = SessionLocal()
    try:
        phase = db.get(TaperProtocolPhase, 2)
        phase.duration_days = 28
        db.commit()
    finally:
        db.close()

    healthy = client.get("/health")

    assert healthy.status_code == 200
    assert healthy.json()["status"] == "ok"

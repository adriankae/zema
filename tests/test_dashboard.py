from __future__ import annotations

from datetime import date, datetime, timezone
from html.parser import HTMLParser

from app.core.database import SessionLocal
from app.core.security import verify_password
from app.core.time import utc_now
from app.models import Account, AccountApiKey, BodyLocation, EczemaEpisode, EpisodeEvent, EpisodePhaseHistory, Subject, TreatmentApplication
from app.services import calculate_phase_due_end_at, create_episode, create_location, create_subject, heal_episode, log_application


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG_BYTES = b"\xff\xd8\xff" + b"\x00" * 16


class _CsrfParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tokens: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "input":
            return
        values = dict(attrs)
        if values.get("name") == "csrf_token" and values.get("value"):
            self.tokens.append(values["value"])


def _csrf_token(html: str) -> str:
    parser = _CsrfParser()
    parser.feed(html)
    assert parser.tokens
    return parser.tokens[0]


def _login(client):
    login_form = client.get("/dashboard/login")
    csrf = _csrf_token(login_form.text)
    response = client.post(
        "/dashboard/login",
        data={"username": "admin", "password": "admin", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return response


def _create_phase_one_episode(*, location_code: str = "left_elbow", location_name: str = "Left elbow") -> int:
    db = SessionLocal()
    try:
        from app.models import Account

        account = db.query(Account).filter(Account.username == "admin").one()
        subject = create_subject(db, account, f"Child {location_code}")
        location = create_location(db, account, location_code, location_name)
        episode = create_episode(
            db,
            account,
            subject.id,
            location.id,
            "v1",
            datetime(2026, 4, 6, 6, tzinfo=timezone.utc),
            "user",
            f"user:{account.id}",
        )
        return episode.id
    finally:
        db.close()


def _create_taper_episode(
    *,
    location_code: str = "neck",
    location_name: str = "Neck",
    healed_at: datetime = datetime(2026, 5, 25, 8, tzinfo=timezone.utc),
) -> int:
    db = SessionLocal()
    try:
        from app.models import Account

        account = db.query(Account).filter(Account.username == "admin").one()
        subject = create_subject(db, account, f"Child {location_code}")
        location = create_location(db, account, location_code, location_name)
        episode = create_episode(
            db,
            account,
            subject.id,
            location.id,
            "v1",
            datetime(2026, 5, 24, 8, tzinfo=timezone.utc),
            "user",
            f"user:{account.id}",
        )
        healed = heal_episode(db, account, episode.id, healed_at, "user", f"user:{account.id}")
        log_application(
            db,
            account,
            healed.id,
            datetime(2026, 5, 25, 9, tzinfo=timezone.utc),
            "steroid",
            "Hydrocortisone",
            "thin layer",
            None,
            "user",
            f"user:{account.id}",
        )
        return healed.id
    finally:
        db.close()


def test_dashboard_requires_login(client):
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/login"


def test_dashboard_login_returns_html(client):
    response = client.get("/dashboard/login")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<form" in response.text
    assert "Treatment Control Center" not in response.text


def test_bad_login_does_not_set_session_cookie(client):
    login_form = client.get("/dashboard/login")
    csrf = _csrf_token(login_form.text)
    response = client.post(
        "/dashboard/login",
        data={"username": "admin", "password": "wrong", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert "zema_session" not in response.cookies


def test_login_without_csrf_is_rejected(client):
    response = client.post(
        "/dashboard/login",
        data={"username": "admin", "password": "admin"},
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert "zema_session" not in response.cookies


def test_login_with_invalid_csrf_is_rejected(client):
    client.get("/dashboard/login")
    response = client.post(
        "/dashboard/login",
        data={"username": "admin", "password": "admin", "csrf_token": "invalid"},
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert "zema_session" not in response.cookies


def test_good_login_sets_secure_browser_session_cookie(client):
    response = _login(client)
    cookie = response.headers["set-cookie"]
    assert "zema_session=" in cookie
    assert "HttpOnly" in cookie
    assert "samesite=lax" in cookie.lower()
    assert "Path=/dashboard" in cookie


def test_authenticated_dashboard_renders_all_clear_overview(client, monkeypatch):
    import app.dashboard.read_model as read_model
    import app.services as services

    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 5, 26, 12, tzinfo=timezone.utc))
    monkeypatch.setattr(read_model, "utc_now", lambda: datetime(2026, 5, 26, 12, tzinfo=timezone.utc))
    _create_taper_episode(location_code="forearm", location_name="Forearm")
    _login(client)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Treatment Control Center" not in response.text
    assert "All clear" in response.text
    assert "All clear for now" not in response.text
    assert "No treatment is due at this moment" in response.text
    assert "Upcoming" in response.text
    assert "Forearm" in response.text


def test_dashboard_logout_without_csrf_is_rejected(client):
    _login(client)
    response = client.post("/dashboard/logout", follow_redirects=False)

    assert response.status_code == 403


def test_dashboard_logout_clears_cookie(client):
    _login(client)
    dashboard = client.get("/dashboard")
    csrf = _csrf_token(dashboard.text)
    response = client.post("/dashboard/logout", data={"csrf_token": csrf}, follow_redirects=False)

    assert response.status_code == 303
    cookie = response.headers["set-cookie"]
    assert "zema_session=" in cookie
    assert "Max-Age=0" in cookie


def test_treatment_post_without_csrf_rejected(client):
    episode_id = _create_phase_one_episode()
    _login(client)

    response = client.post(
        "/dashboard/treatments",
        data={"episode_id": str(episode_id), "treatment_type": "steroid"},
        follow_redirects=False,
    )

    assert response.status_code == 403


def test_treatment_post_with_invalid_csrf_rejected(client):
    episode_id = _create_phase_one_episode()
    _login(client)

    response = client.post(
        "/dashboard/treatments",
        data={"episode_id": str(episode_id), "treatment_type": "steroid", "csrf_token": "invalid"},
        follow_redirects=False,
    )

    assert response.status_code == 403


def test_treatment_post_with_valid_csrf_uses_domain_logging(client):
    episode_id = _create_phase_one_episode()
    _login(client)
    dashboard = client.get("/dashboard")
    token = _csrf_token(dashboard.text)

    response = client.post(
        "/dashboard/treatments",
        data={
            "episode_id": str(episode_id),
            "treatment_type": "steroid",
            "treatment_name": "Hydrocortisone 1%",
            "quantity_text": "thin layer",
            "notes": "dashboard log",
            "csrf_token": token,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    db = SessionLocal()
    try:
        application = db.query(TreatmentApplication).filter(TreatmentApplication.episode_id == episode_id).one()
        assert application.treatment_type == "steroid"
        assert application.treatment_name == "Hydrocortisone 1%"
        assert application.quantity_text == "thin layer"
        assert application.notes == "dashboard log"
    finally:
        db.close()


def test_log_all_due_locations_logs_current_due_items(client, monkeypatch):
    import app.dashboard.read_model as read_model
    import app.dashboard.routes as dashboard_routes
    import app.services as services

    now = datetime(2026, 5, 27, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(dashboard_routes, "utc_now", lambda: now)
    monkeypatch.setattr(services, "utc_now", lambda: now)
    monkeypatch.setattr(read_model, "utc_now", lambda: now)
    first_id = _create_taper_episode(location_code="bulk_one", location_name="Bulk one")
    second_id = _create_taper_episode(location_code="bulk_two", location_name="Bulk two")
    _login(client)
    dashboard = client.get("/dashboard")
    token = _csrf_token(dashboard.text)

    response = client.post("/dashboard/treatments/all-due", data={"csrf_token": token}, follow_redirects=False)

    assert response.status_code == 303
    db = SessionLocal()
    try:
        rows = (
            db.query(TreatmentApplication)
            .filter(TreatmentApplication.episode_id.in_([first_id, second_id]))
            .order_by(TreatmentApplication.episode_id.asc(), TreatmentApplication.applied_at.asc())
            .all()
        )
        logged_by_episode = {row.episode_id: [] for row in rows}
        for row in rows:
            logged_by_episode[row.episode_id].append(row)
        assert len(logged_by_episode[first_id]) == 2
        assert len(logged_by_episode[second_id]) == 2
        assert logged_by_episode[first_id][-1].applied_at.replace(tzinfo=timezone.utc) == now
        assert logged_by_episode[second_id][-1].applied_at.replace(tzinfo=timezone.utc) == now
    finally:
        db.close()


def test_undo_last_treatment_log_deletes_last_log_batch(client, monkeypatch):
    import app.dashboard.read_model as read_model
    import app.dashboard.routes as dashboard_routes
    import app.services as services

    now = datetime(2026, 5, 27, 12, tzinfo=timezone.utc)
    undo_now = datetime(2026, 5, 27, 12, 5, tzinfo=timezone.utc)
    current_now = now

    def fake_now():
        return current_now

    monkeypatch.setattr(dashboard_routes, "utc_now", fake_now)
    monkeypatch.setattr(services, "utc_now", fake_now)
    monkeypatch.setattr(read_model, "utc_now", fake_now)
    first_id = _create_taper_episode(location_code="undo_one", location_name="Undo one")
    second_id = _create_taper_episode(location_code="undo_two", location_name="Undo two")
    _login(client)
    dashboard = client.get("/dashboard")
    token = _csrf_token(dashboard.text)
    logged = client.post("/dashboard/treatments/all-due", data={"csrf_token": token}, follow_redirects=False)
    assert logged.status_code == 303

    current_now = undo_now
    undo_dashboard = client.get("/dashboard")
    undo_token = _csrf_token(undo_dashboard.text)
    undone = client.post("/dashboard/treatments/undo-last", data={"csrf_token": undo_token}, follow_redirects=False)

    assert undone.status_code == 303
    db = SessionLocal()
    try:
        rows = (
            db.query(TreatmentApplication)
            .filter(TreatmentApplication.episode_id.in_([first_id, second_id]))
            .order_by(TreatmentApplication.episode_id.asc(), TreatmentApplication.applied_at.asc())
            .all()
        )
        latest = [row for row in rows if row.applied_at.replace(tzinfo=timezone.utc) == now]
        assert len(latest) == 2
        assert all(row.is_deleted for row in latest)
        assert all(row.deleted_at.replace(tzinfo=timezone.utc) == undo_now for row in latest)
    finally:
        db.close()
    refreshed = client.get("/dashboard")
    assert "Undo one" in refreshed.text
    assert "Undo two" in refreshed.text


def test_undo_ignores_newer_agent_logs_and_reverts_last_dashboard_action(client, monkeypatch):
    import app.dashboard.read_model as read_model
    import app.dashboard.routes as dashboard_routes
    import app.services as services

    healed_at = datetime(2026, 5, 26, 12, tzinfo=timezone.utc)
    agent_log_at = datetime(2026, 5, 27, 8, tzinfo=timezone.utc)
    monkeypatch.setattr(dashboard_routes, "utc_now", lambda: healed_at)
    monkeypatch.setattr(services, "utc_now", lambda: healed_at)
    monkeypatch.setattr(read_model, "utc_now", lambda: healed_at)
    phase_one_id = _create_phase_one_episode(location_code="undo_phase_action", location_name="Undo phase action")
    agent_episode_id = _create_taper_episode(location_code="agent_log_latest", location_name="Agent log latest")
    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.username == "admin").one()
        agent_application = log_application(db, account, agent_episode_id, agent_log_at, "steroid", None, None, None, "agent", "api-key:2")
        agent_application_id = agent_application.id
    finally:
        db.close()
    _login(client)
    csrf = _csrf_token(client.get("/dashboard").text)
    healed = client.post(f"/dashboard/episodes/{phase_one_id}/heal", data={"csrf_token": csrf}, follow_redirects=False)
    assert healed.status_code == 303

    undo_token = _csrf_token(client.get("/dashboard").text)
    undo = client.post("/dashboard/treatments/undo-last", data={"csrf_token": undo_token}, follow_redirects=False)

    assert undo.status_code == 303
    db = SessionLocal()
    try:
        phase_one = db.get(EczemaEpisode, phase_one_id)
        agent_application = db.get(TreatmentApplication, agent_application_id)
        assert phase_one.status == "active_flare"
        assert phase_one.current_phase_number == 1
        assert agent_application.is_deleted is False
    finally:
        db.close()


def test_undo_can_revert_latest_telegram_bot_log(client, monkeypatch):
    import app.dashboard.read_model as read_model
    import app.dashboard.routes as dashboard_routes
    import app.services as services

    log_at = datetime(2026, 5, 27, 8, tzinfo=timezone.utc)
    undo_at = datetime(2026, 5, 27, 8, 5, tzinfo=timezone.utc)
    current_now = log_at

    def fake_now():
        return current_now

    monkeypatch.setattr(dashboard_routes, "utc_now", fake_now)
    monkeypatch.setattr(services, "utc_now", fake_now)
    monkeypatch.setattr(read_model, "utc_now", fake_now)
    episode_id = _create_taper_episode(location_code="telegram_undo", location_name="Telegram undo")
    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.username == "admin").one()
        api_key = AccountApiKey(account_id=account.id, name="telegram-dashboard-bot", key_hash="telegram-test-key", is_active=True)
        db.add(api_key)
        db.flush()
        application = log_application(db, account, episode_id, log_at, "steroid", None, None, None, "agent", f"api-key:{api_key.id}")
        application_id = application.id
    finally:
        db.close()
    _login(client)

    current_now = undo_at
    undo_token = _csrf_token(client.get("/dashboard").text)
    undo = client.post("/dashboard/treatments/undo-last", data={"csrf_token": undo_token}, follow_redirects=False)

    assert undo.status_code == 303
    db = SessionLocal()
    try:
        application = db.get(TreatmentApplication, application_id)
        assert application is not None
        assert application.is_deleted is True
        assert application.deleted_at.replace(tzinfo=timezone.utc) == undo_at
    finally:
        db.close()


def test_due_header_renders_primary_log_all_button(client, monkeypatch):
    import app.dashboard.read_model as read_model
    import app.services as services

    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 5, 27, 12, tzinfo=timezone.utc))
    monkeypatch.setattr(read_model, "utc_now", lambda: datetime(2026, 5, 27, 12, tzinfo=timezone.utc))
    _create_taper_episode(location_code="bulk_primary", location_name="Bulk primary")
    _login(client)

    response = client.get("/dashboard")

    assert response.status_code == 200
    due_block = response.text.split('id="due-title"', 1)[1].split("<article", 1)[0]
    assert 'action="/dashboard/treatments/all-due"' in due_block
    assert '<button type="submit">Log all locations</button>' in due_block
    assert "secondary-button" not in due_block


def test_upcoming_section_renders_active_and_tapering_non_due_episodes(client, monkeypatch):
    import app.dashboard.read_model as read_model
    import app.services as services

    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 5, 26, 9, tzinfo=timezone.utc))
    monkeypatch.setattr(read_model, "utc_now", lambda: datetime(2026, 5, 26, 9, tzinfo=timezone.utc))
    active_id = _create_phase_one_episode(location_code="active_clear", location_name="Active clear")
    taper_id = _create_taper_episode(location_code="taper_clear", location_name="Taper clear")
    db = SessionLocal()
    try:
        from app.models import Account

        account = db.query(Account).filter(Account.username == "admin").one()
        log_application(db, account, active_id, datetime(2026, 5, 26, 9, 30, tzinfo=timezone.utc), "steroid", None, None, None, "user", f"user:{account.id}")
        log_application(db, account, active_id, datetime(2026, 5, 26, 15, 30, tzinfo=timezone.utc), "steroid", None, None, None, "user", f"user:{account.id}")
        log_application(db, account, taper_id, datetime(2026, 5, 26, 9, 45, tzinfo=timezone.utc), "steroid", None, None, None, "user", f"user:{account.id}")
    finally:
        db.close()
    _login(client)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Active clear" in response.text
    assert "Taper clear" in response.text
    assert "Phase 1" in response.text
    assert "Phase 2" in response.text


def test_upcoming_rows_show_dates_without_times_in_single_user_overview(client, monkeypatch):
    import app.dashboard.read_model as read_model
    import app.services as services
    from app.core.config import settings

    monkeypatch.setattr(settings, "deployment_timezone", "Europe/Berlin")
    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 5, 26, 19, 55, tzinfo=timezone.utc))
    monkeypatch.setattr(read_model, "utc_now", lambda: datetime(2026, 5, 26, 19, 55, tzinfo=timezone.utc))
    _create_taper_episode(location_code="berlin_next", location_name="Berlin next")
    _login(client)

    response = client.get("/dashboard")

    assert response.status_code == 200
    upcoming_block = response.text.split('id="upcoming-title"', 1)[1].split('class="two-column"', 1)[0]
    assert "Berlin next" in upcoming_block
    assert "Child Berlin next" not in upcoming_block
    assert "Next May 27" in upcoming_block
    assert "Last May 25" in upcoming_block
    assert "Next May 27, 00:00" not in upcoming_block
    assert "00:00" not in upcoming_block


def test_phase_one_upcoming_rows_show_next_and_last_slots(client, monkeypatch):
    import app.dashboard.read_model as read_model
    import app.services as services
    from app.core.config import settings

    monkeypatch.setattr(settings, "deployment_timezone", "Europe/Berlin")
    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 5, 27, 10, tzinfo=timezone.utc))
    monkeypatch.setattr(read_model, "utc_now", lambda: datetime(2026, 5, 27, 10, tzinfo=timezone.utc))
    episode_id = _create_phase_one_episode(location_code="right_foot_slot", location_name="Right foot slot")
    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.username == "admin").one()
        log_application(db, account, episode_id, datetime(2026, 5, 27, 7, tzinfo=timezone.utc), "steroid", None, None, None, "user", f"user:{account.id}")
    finally:
        db.close()
    _login(client)

    response = client.get("/dashboard")

    assert response.status_code == 200
    upcoming_block = response.text.split('id="upcoming-title"', 1)[1].split('class="two-column"', 1)[0]
    assert "Right foot slot" in upcoming_block
    assert "Next May 27, evening" in upcoming_block
    assert "Last May 27, morning" in upcoming_block
    assert "14:00" not in upcoming_block


def test_location_info_buttons_render_phase_changes_and_location_adherence(client, monkeypatch):
    import app.dashboard.read_model as read_model
    import app.services as services

    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 5, 27, 12, tzinfo=timezone.utc))
    monkeypatch.setattr(read_model, "utc_now", lambda: datetime(2026, 5, 27, 12, tzinfo=timezone.utc))
    _create_taper_episode(
        location_code="info_location",
        location_name="Info location",
        healed_at=datetime(2026, 5, 25, 8, tzinfo=timezone.utc),
    )
    _login(client)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert 'aria-label="Show details for Info location"' in response.text
    assert 'id="location-info-dialog"' in response.text
    assert 'id="location-info-template-' in response.text
    assert "Last phase change" in response.text
    assert "Next phase change" in response.text
    assert "May 25" in response.text
    assert "Jun 22" in response.text
    assert "This location" in response.text
    assert "Expected" in response.text
    assert "Completed" in response.text
    assert "Missed days" in response.text
    assert "Partial days" not in response.text
    assert "location-info-chain" in response.text
    location_info_block = response.text
    assert 'data-location-range="week"' in location_info_block
    assert 'data-location-range="month"' in location_info_block
    assert 'data-location-range="year"' in location_info_block
    assert 'data-location-range="all"' in location_info_block
    assert 'data-location-range-panel="month"' in location_info_block
    assert 'data-adherence-detail-id="location-adherence-detail-' in location_info_block
    assert 'id="location-adherence-detail-' in location_info_block
    assert 'action="/dashboard/adherence/log-missing"' in location_info_block
    assert 'adherenceDetailDialog.addEventListener("submit"' in response.text
    assert 'await fetch(form.action' in response.text
    assert "new DOMParser().parseFromString(html" in response.text
    assert "refreshActiveLocationInfo(refreshedDocument);" in response.text
    assert "adherenceDetailDialog.close();" in response.text
    assert 'data-location-range="custom"' not in location_info_block
    assert 'data-status="partial"' not in response.text
    assert "location-info-trigger" in response.text
    css = client.get("/dashboard/assets/dashboard.css")
    assert css.status_code == 200
    assert ".location-range-panel[hidden]" in css.text
    assert "grid-template-columns: repeat(auto-fill, 13px)" in css.text


def test_location_info_backfill_refreshes_missing_state(client, monkeypatch):
    import app.adherence as adherence
    import app.dashboard.read_model as read_model
    import app.services as services

    now = datetime(2026, 5, 27, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(adherence, "utc_now", lambda: now)
    monkeypatch.setattr(read_model, "utc_now", lambda: now)
    monkeypatch.setattr(services, "utc_now", lambda: now)
    episode_id = _create_phase_one_episode(location_code="location_refresh", location_name="Location refresh")
    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.username == "admin").one()
        log_application(
            db,
            account,
            episode_id,
            datetime(2026, 5, 26, 7, tzinfo=timezone.utc),
            "steroid",
            None,
            None,
            None,
            "user",
            f"user:{account.id}",
        )
    finally:
        db.close()
    _login(client)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert f'id="location-adherence-detail-{episode_id}-month-2026-05-26"' in response.text
    assert 'name="applied_at" value="2026-05-26T18:00:00+00:00"' in response.text
    csrf = _csrf_token(response.text)

    posted = client.post(
        "/dashboard/adherence/log-missing",
        data={
            "csrf_token": csrf,
            "episode_id": str(episode_id),
            "phase_number": "1",
            "applied_at": "2026-05-26T18:00:00+00:00",
            "return_to": "/dashboard",
        },
        follow_redirects=False,
    )

    assert posted.status_code == 303
    refreshed = client.get("/dashboard")
    assert refreshed.status_code == 200
    assert f'id="location-adherence-detail-{episode_id}-month-2026-05-26"' in refreshed.text
    assert 'name="applied_at" value="2026-05-26T18:00:00+00:00"' not in refreshed.text
    assert 'data-date="2026-05-26"' in refreshed.text
    assert 'data-status="completed"' in refreshed.text


def test_location_info_backfill_restores_deleted_slot_instead_of_succeeding_noop(client, monkeypatch):
    import app.adherence as adherence
    import app.dashboard.read_model as read_model
    import app.services as services

    now = datetime(2026, 5, 27, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(adherence, "utc_now", lambda: now)
    monkeypatch.setattr(read_model, "utc_now", lambda: now)
    monkeypatch.setattr(services, "utc_now", lambda: now)
    episode_id = _create_phase_one_episode(location_code="restore_deleted_slot", location_name="Restore deleted slot")
    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.username == "admin").one()
        log_application(
            db,
            account,
            episode_id,
            datetime(2026, 5, 26, 7, tzinfo=timezone.utc),
            "steroid",
            None,
            None,
            None,
            "system",
            "test",
        )
        deleted = log_application(
            db,
            account,
            episode_id,
            datetime(2026, 5, 26, 18, tzinfo=timezone.utc),
            "steroid",
            None,
            None,
            None,
            "system",
            "test",
        )
        services.delete_application(db, account, deleted.id, datetime(2026, 5, 27, 9, tzinfo=timezone.utc), "user", f"user:{account.id}")
        deleted_id = deleted.id
    finally:
        db.close()
    _login(client)
    dashboard = client.get("/dashboard")
    csrf = _csrf_token(dashboard.text)

    posted = client.post(
        "/dashboard/adherence/log-missing",
        data={
            "csrf_token": csrf,
            "episode_id": str(episode_id),
            "phase_number": "1",
            "applied_at": "2026-05-26T18:00:00+00:00",
            "return_to": "/dashboard",
        },
        follow_redirects=False,
    )

    assert posted.status_code == 303
    db = SessionLocal()
    try:
        restored = db.get(TreatmentApplication, deleted_id)
        assert restored is not None
        assert restored.is_deleted is False
        assert restored.deleted_at is None
        assert restored.notes == "Backfilled from adherence detail"
        same_slot = (
            db.query(TreatmentApplication)
            .filter(TreatmentApplication.episode_id == episode_id, TreatmentApplication.applied_at == datetime(2026, 5, 26, 18, tzinfo=timezone.utc))
            .all()
        )
        assert [application.id for application in same_slot] == [deleted_id]
    finally:
        db.close()
    refreshed = client.get("/dashboard")
    assert 'name="applied_at" value="2026-05-26T18:00:00+00:00"' not in refreshed.text
    assert 'data-date="2026-05-26"' in refreshed.text
    assert 'data-status="completed"' in refreshed.text

    undo_token = _csrf_token(refreshed.text)
    undo = client.post("/dashboard/treatments/undo-last", data={"csrf_token": undo_token}, follow_redirects=False)

    assert undo.status_code == 303
    db = SessionLocal()
    try:
        assert db.get(TreatmentApplication, deleted_id).is_deleted is False
    finally:
        db.close()


def test_location_info_chain_uses_original_location_start_after_phase_advances(monkeypatch):
    import app.dashboard.read_model as read_model
    import app.services as services

    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 5, 28, 12, tzinfo=timezone.utc))
    monkeypatch.setattr(read_model, "utc_now", lambda: datetime(2026, 5, 28, 12, tzinfo=timezone.utc))
    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.username == "admin").one()
        subject = create_subject(db, account, "Child phase advanced")
        location = create_location(db, account, "phase_advanced_po", "Po")
        episode = create_episode(
            db,
            account,
            subject.id,
            location.id,
            "v1",
            datetime(2026, 4, 26, 17, 55, tzinfo=timezone.utc),
            "user",
            f"user:{account.id}",
        )
        healed = heal_episode(db, account, episode.id, datetime(2026, 4, 26, 17, 57, tzinfo=timezone.utc), "user", f"user:{account.id}")
        phase_two = (
            db.query(EpisodePhaseHistory)
            .filter(EpisodePhaseHistory.episode_id == healed.id, EpisodePhaseHistory.phase_number == 2)
            .one()
        )
        phase_three_start = datetime(2026, 5, 24, 17, 57, tzinfo=timezone.utc)
        phase_two.ended_at = phase_three_start
        healed.current_phase_number = 3
        healed.phase_started_at = phase_three_start
        healed.phase_due_end_at = calculate_phase_due_end_at(phase_three_start, 3)
        db.add(
            EpisodePhaseHistory(
                episode_id=healed.id,
                phase_number=3,
                started_at=phase_three_start,
                ended_at=None,
                reason="auto_advance",
            )
        )
        db.add(healed)
        db.commit()

        overview = read_model.build_dashboard_overview(db, account)
        row = next(item for item in overview.active_locations if item.location_name == "Po")
        month = next(item for item in row.location_adherence if item.range_key == "month")
        statuses_by_date = {day.date: day.status for day in month.habit_chain}

        assert statuses_by_date[date(2026, 5, 1)] != "pre_start"
        assert statuses_by_date[date(2026, 5, 23)] != "pre_start"
        assert any(day.status not in {"pre_start", "future"} for day in month.habit_chain)
    finally:
        db.close()


def test_dashboard_backup_tab_exposes_import_export_controls(client):
    _login(client)

    response = client.get("/dashboard?tab=settings&settings_tab=backup")

    assert response.status_code == 200
    assert "Import / Export" in response.text
    assert 'href="/dashboard/export"' in response.text
    assert 'action="/dashboard/import"' in response.text
    assert "Replace current tracking data" in response.text


def test_dashboard_telegram_tab_saves_token_and_chat_ids(client, monkeypatch):
    import app.telegram_settings as telegram_settings

    monkeypatch_bot = type("Bot", (), {"username": "zema_bot"})()
    monkeypatch.setattr(telegram_settings, "validate_bot_token", lambda _token: monkeypatch_bot)
    _login(client)
    dashboard = client.get("/dashboard?tab=settings&settings_tab=telegram")
    token = _csrf_token(dashboard.text)

    token_response = client.post(
        "/dashboard/telegram/token",
        data={"csrf_token": token, "bot_token": "123456:test-token"},
        follow_redirects=False,
    )

    assert token_response.status_code == 303
    dashboard = client.get("/dashboard?tab=settings&settings_tab=telegram")
    token = _csrf_token(dashboard.text)
    response = client.post(
        "/dashboard/telegram",
        data={
            "csrf_token": token,
            "allowed_chat_ids": "111, 222",
            "allowed_user_ids": "333",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    refreshed = client.get("/dashboard?tab=settings&settings_tab=telegram")
    assert "Telegram Bot" in refreshed.text
    assert "Ready to enable" in refreshed.text
    assert "@zema_bot" in refreshed.text
    assert "Chat: 111, 222" in refreshed.text
    assert "Save Telegram settings" not in refreshed.text
    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.username == "admin").one()
        from app.models import TelegramBotSettings

        row = db.query(TelegramBotSettings).filter(TelegramBotSettings.account_id == account.id).one()
        assert row.allow_writes is True
        assert row.allow_adherence_rebuild is False
        assert row.is_enabled is False
    finally:
        db.close()


def test_dashboard_telegram_wizard_discovers_chats(client, monkeypatch):
    import app.telegram_settings as telegram_settings
    from czm_cli.telegram.setup import DiscoveredChat

    monkeypatch.setattr(telegram_settings, "validate_bot_token", lambda _token: type("Bot", (), {"username": "zema_bot"})())
    monkeypatch.setattr(
        telegram_settings,
        "discover_chats",
        lambda _token: [DiscoveredChat(id=444, type="private", title="Adrian", user_id=555)],
    )
    _login(client)
    dashboard = client.get("/dashboard?tab=settings&settings_tab=telegram")
    token = _csrf_token(dashboard.text)
    client.post(
        "/dashboard/telegram/token",
        data={"csrf_token": token, "bot_token": "123456:test-token"},
        follow_redirects=False,
    )
    dashboard = client.get("/dashboard?tab=settings&settings_tab=telegram")
    assert "Open @zema_bot in Telegram" in dashboard.text
    assert 'href="https://t.me/zema_bot"' in dashboard.text
    token = _csrf_token(dashboard.text)

    response = client.post("/dashboard/telegram/discover", data={"csrf_token": token}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard?tab=settings&settings_tab=telegram&telegram_chat_linked=1"
    refreshed = client.get("/dashboard?tab=settings&settings_tab=telegram&telegram_chat_linked=1")
    assert "Chat linked." in refreshed.text
    assert "Chat: 444" in refreshed.text
    assert "Enable Bot" in refreshed.text
    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.username == "admin").one()
        from app.models import TelegramBotSettings

        row = db.query(TelegramBotSettings).filter(TelegramBotSettings.account_id == account.id).one()
        assert row.allowed_chat_ids == [444]
        assert row.allowed_user_ids == []
    finally:
        db.close()


def test_dashboard_telegram_discover_multiple_chats_requires_selection(client, monkeypatch):
    import app.telegram_settings as telegram_settings
    from czm_cli.telegram.setup import DiscoveredChat

    monkeypatch.setattr(telegram_settings, "validate_bot_token", lambda _token: type("Bot", (), {"username": "zema_bot"})())
    monkeypatch.setattr(
        telegram_settings,
        "discover_chats",
        lambda _token: [
            DiscoveredChat(id=444, type="private", title="Adrian", user_id=555),
            DiscoveredChat(id=777, type="group", title="Family", user_id=None),
        ],
    )
    _login(client)
    dashboard = client.get("/dashboard?tab=settings&settings_tab=telegram")
    token = _csrf_token(dashboard.text)
    client.post(
        "/dashboard/telegram/token",
        data={"csrf_token": token, "bot_token": "123456:test-token"},
        follow_redirects=False,
    )
    dashboard = client.get("/dashboard?tab=settings&settings_tab=telegram")
    token = _csrf_token(dashboard.text)

    response = client.post("/dashboard/telegram/discover", data={"csrf_token": token})

    assert response.status_code == 200
    assert "Adrian" in response.text
    assert 'value="444"' in response.text
    assert "Family" in response.text
    assert 'value="777"' in response.text
    assert "Use selected chat" in response.text
    assert "chat · 444" not in response.text


def test_dashboard_telegram_discovery_is_blocked_while_runtime_is_active(client, monkeypatch):
    import app.telegram_settings as telegram_settings

    called = False

    def discover(_token):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(telegram_settings, "validate_bot_token", lambda _token: type("Bot", (), {"username": "zema_bot"})())
    monkeypatch.setattr(telegram_settings, "discover_chats", discover)
    _login(client)
    dashboard = client.get("/dashboard?tab=settings&settings_tab=telegram")
    token = _csrf_token(dashboard.text)
    client.post(
        "/dashboard/telegram/token",
        data={"csrf_token": token, "bot_token": "123456:test-token"},
        follow_redirects=False,
    )
    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.username == "admin").one()
        from app.models import TelegramBotSettings

        row = db.query(TelegramBotSettings).filter(TelegramBotSettings.account_id == account.id).one()
        row.is_enabled = True
        row.runtime_status = "active"
        db.add(row)
        db.commit()
    finally:
        db.close()
    dashboard = client.get("/dashboard?tab=settings&settings_tab=telegram")
    token = _csrf_token(dashboard.text)

    response = client.post("/dashboard/telegram/discover", data={"csrf_token": token})

    assert response.status_code == 409
    assert "Turn the bot off and wait until it stops before finding chats." in response.text
    assert called is False


def test_dashboard_telegram_token_error_stays_in_wizard(client, monkeypatch):
    import app.telegram_settings as telegram_settings

    def fail_token(_token):
        raise RuntimeError("Telegram bot token validation failed")

    monkeypatch.setattr(telegram_settings, "validate_bot_token", fail_token)
    _login(client)
    dashboard = client.get("/dashboard?tab=settings&settings_tab=telegram")
    token = _csrf_token(dashboard.text)

    response = client.post(
        "/dashboard/telegram/token",
        data={"csrf_token": token, "bot_token": "bad-token"},
    )

    assert response.status_code == 422
    assert "Telegram bot token validation failed" in response.text
    assert "Telegram Bot" in response.text


def test_dashboard_telegram_enable_requires_chat_and_creates_api_key(client, monkeypatch):
    import app.telegram_settings as telegram_settings

    monkeypatch.setattr(telegram_settings, "validate_bot_token", lambda _token: type("Bot", (), {"username": "zema_bot"})())
    _login(client)
    dashboard = client.get("/dashboard?tab=settings&settings_tab=telegram")
    token = _csrf_token(dashboard.text)
    client.post(
        "/dashboard/telegram/token",
        data={"csrf_token": token, "bot_token": "123456:test-token"},
        follow_redirects=False,
    )
    dashboard = client.get("/dashboard?tab=settings&settings_tab=telegram")
    token = _csrf_token(dashboard.text)

    missing_chat = client.post("/dashboard/telegram/enable", data={"csrf_token": token}, follow_redirects=False)

    assert missing_chat.status_code == 422

    dashboard = client.get("/dashboard?tab=settings&settings_tab=telegram")
    token = _csrf_token(dashboard.text)
    client.post(
        "/dashboard/telegram",
        data={"csrf_token": token, "allowed_chat_ids": "111"},
        follow_redirects=False,
    )
    dashboard = client.get("/dashboard?tab=settings&settings_tab=telegram")
    token = _csrf_token(dashboard.text)
    enabled = client.post("/dashboard/telegram/enable", data={"csrf_token": token}, follow_redirects=False)

    assert enabled.status_code == 303
    assert enabled.headers["location"] == "/dashboard?tab=settings&settings_tab=telegram&telegram_enabled=1"
    refreshed = client.get("/dashboard?tab=settings&settings_tab=telegram&telegram_enabled=1")
    assert "Bot is starting." in refreshed.text
    assert "Starting bot" in refreshed.text
    assert "Starting bot. This can take a few seconds." in refreshed.text
    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.username == "admin").one()
        from app.models import AccountApiKey, TelegramBotSettings

        row = db.query(TelegramBotSettings).filter(TelegramBotSettings.account_id == account.id).one()
        assert row.is_enabled is True
        assert row.runtime_status == "starting"
        assert row.api_key_id is not None
        assert db.get(AccountApiKey, row.api_key_id) is not None
    finally:
        db.close()


def test_dashboard_telegram_active_status_hides_completed_wizard(client, monkeypatch):
    import app.telegram_settings as telegram_settings

    monkeypatch.setattr(telegram_settings, "validate_bot_token", lambda _token: type("Bot", (), {"username": "zema_bot"})())
    _login(client)
    dashboard = client.get("/dashboard?tab=settings&settings_tab=telegram")
    token = _csrf_token(dashboard.text)
    client.post(
        "/dashboard/telegram/token",
        data={"csrf_token": token, "bot_token": "123456:test-token"},
        follow_redirects=False,
    )
    dashboard = client.get("/dashboard?tab=settings&settings_tab=telegram")
    token = _csrf_token(dashboard.text)
    client.post(
        "/dashboard/telegram",
        data={"csrf_token": token, "allowed_chat_ids": "111"},
        follow_redirects=False,
    )
    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.username == "admin").one()
        from app.models import TelegramBotSettings

        row = db.query(TelegramBotSettings).filter(TelegramBotSettings.account_id == account.id).one()
        row.is_enabled = True
        row.runtime_status = "active"
        db.add(row)
        db.commit()
    finally:
        db.close()

    response = client.get("/dashboard?tab=settings&settings_tab=telegram")

    assert "Bot active" in response.text
    assert "@zema_bot" in response.text
    assert "Reset bot setup" in response.text
    assert "Connect bot" not in response.text
    assert "Open Telegram" not in response.text
    assert "Enable Bot" not in response.text
    assert "Advanced details" not in response.text
    assert "Open Telegram and send /menu to test the bot." not in response.text
    assert "Bot active. Open Telegram and send <code>/menu</code> to test it." not in response.text


def test_dashboard_telegram_reset_deletes_token_chat_ids_and_api_key(client, monkeypatch):
    import app.telegram_settings as telegram_settings

    monkeypatch.setattr(telegram_settings, "validate_bot_token", lambda _token: type("Bot", (), {"username": "zema_bot"})())
    _login(client)
    dashboard = client.get("/dashboard?tab=settings&settings_tab=telegram")
    token = _csrf_token(dashboard.text)
    client.post(
        "/dashboard/telegram/token",
        data={"csrf_token": token, "bot_token": "123456:test-token"},
        follow_redirects=False,
    )
    dashboard = client.get("/dashboard?tab=settings&settings_tab=telegram")
    token = _csrf_token(dashboard.text)
    client.post(
        "/dashboard/telegram",
        data={"csrf_token": token, "allowed_chat_ids": "111", "allowed_user_ids": "222"},
        follow_redirects=False,
    )
    dashboard = client.get("/dashboard?tab=settings&settings_tab=telegram")
    token = _csrf_token(dashboard.text)
    client.post("/dashboard/telegram/enable", data={"csrf_token": token}, follow_redirects=False)
    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.username == "admin").one()
        from app.models import AccountApiKey, TelegramBotSettings

        row = db.query(TelegramBotSettings).filter(TelegramBotSettings.account_id == account.id).one()
        api_key_id = row.api_key_id
        assert api_key_id is not None
        assert db.get(AccountApiKey, api_key_id) is not None
    finally:
        db.close()

    dashboard = client.get("/dashboard?tab=settings&settings_tab=telegram")
    assert "Remove this Telegram bot setup and start over?" in dashboard.text
    token = _csrf_token(dashboard.text)
    reset = client.post("/dashboard/telegram/reset", data={"csrf_token": token}, follow_redirects=False)

    assert reset.status_code == 303
    assert reset.headers["location"] == "/dashboard?tab=settings&settings_tab=telegram&telegram_reset=1"
    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.username == "admin").one()
        from app.models import AccountApiKey, TelegramBotSettings

        assert db.query(TelegramBotSettings).filter(TelegramBotSettings.account_id == account.id).one_or_none() is None
        assert db.get(AccountApiKey, api_key_id) is None
    finally:
        db.close()
    refreshed = client.get("/dashboard?tab=settings&settings_tab=telegram&telegram_reset=1")
    assert "Bot setup reset." in refreshed.text
    assert "Not connected" in refreshed.text
    assert "Check bot" in refreshed.text
    assert "Reset bot setup" not in refreshed.text


def test_dashboard_adherence_uses_rolling_taper_schedule_and_renders_habit_chain(client, monkeypatch):
    import app.adherence as adherence
    import app.dashboard.read_model as read_model
    import app.services as services

    monkeypatch.setattr(adherence, "utc_now", lambda: datetime(2026, 5, 26, 12, tzinfo=timezone.utc))
    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 5, 26, 12, tzinfo=timezone.utc))
    monkeypatch.setattr(read_model, "utc_now", lambda: datetime(2026, 5, 26, 12, tzinfo=timezone.utc))
    episode_id = _create_taper_episode(
        location_code="rolling_taper",
        location_name="Rolling taper",
        healed_at=datetime(2026, 5, 20, 8, tzinfo=timezone.utc),
    )
    db = SessionLocal()
    try:
        from app.models import Account

        account = db.query(Account).filter(Account.username == "admin").one()
        log_application(db, account, episode_id, datetime(2026, 5, 22, 9, tzinfo=timezone.utc), "steroid", None, None, None, "user", f"user:{account.id}")
        log_application(db, account, episode_id, datetime(2026, 5, 23, 9, tzinfo=timezone.utc), "steroid", None, None, None, "user", f"user:{account.id}")
    finally:
        db.close()
    _login(client)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "100%" in response.text
    assert 'class="habit-chain"' in response.text
    assert 'data-date="2026-05-26"' in response.text
    assert 'title="May 26' in response.text
    assert "not due" in response.text
    assert 'data-status="missed"' not in response.text


def test_dashboard_adherence_day_detail_can_backfill_missing_treatment(client, monkeypatch):
    import app.adherence as adherence
    import app.dashboard.read_model as read_model
    import app.services as services

    now = datetime(2026, 5, 27, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(adherence, "utc_now", lambda: now)
    monkeypatch.setattr(read_model, "utc_now", lambda: now)
    monkeypatch.setattr(services, "utc_now", lambda: now)
    episode_id = _create_phase_one_episode(location_code="adherence_detail", location_name="Adherence detail")
    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.username == "admin").one()
        log_application(
            db,
            account,
            episode_id,
            datetime(2026, 5, 26, 7, tzinfo=timezone.utc),
            "steroid",
            None,
            None,
            None,
            "user",
            f"user:{account.id}",
        )
    finally:
        db.close()
    _login(client)

    response = client.get("/dashboard?adherence_range=custom&from_date=2026-05-26&to_date=2026-05-26")

    assert response.status_code == 200
    assert 'data-adherence-detail-id="adherence-detail-2026-05-26"' in response.text
    assert 'id="adherence-detail-dialog"' in response.text
    assert 'id="adherence-detail-2026-05-26"' in response.text
    assert "Logged" in response.text
    assert "Missing" in response.text
    assert "Adherence detail" in response.text
    assert "Evening" in response.text
    assert 'action="/dashboard/adherence/log-missing"' in response.text
    assert 'name="applied_at" value="2026-05-26T18:00:00+00:00"' in response.text
    csrf = _csrf_token(response.text)

    posted = client.post(
        "/dashboard/adherence/log-missing",
        data={
            "csrf_token": csrf,
            "episode_id": str(episode_id),
            "phase_number": "1",
            "applied_at": "2026-05-26T18:00:00+00:00",
            "return_to": "/dashboard?adherence_range=custom&from_date=2026-05-26&to_date=2026-05-26#adherence",
        },
        follow_redirects=False,
    )

    assert posted.status_code == 303
    assert posted.headers["location"] == "/dashboard?adherence_range=custom&from_date=2026-05-26&to_date=2026-05-26#adherence"
    db = SessionLocal()
    try:
        applications = (
            db.query(TreatmentApplication)
            .filter(TreatmentApplication.episode_id == episode_id)
            .order_by(TreatmentApplication.applied_at.asc())
            .all()
        )
        assert len(applications) == 2
        assert applications[1].applied_at.replace(tzinfo=timezone.utc) == datetime(2026, 5, 26, 18, tzinfo=timezone.utc)
        assert applications[1].phase_number_snapshot == 1
        assert applications[1].notes == "Backfilled from adherence detail"
    finally:
        db.close()


def test_dashboard_adherence_anchor_tooltips_and_not_due_green(client, monkeypatch):
    import app.adherence as adherence
    import app.dashboard.read_model as read_model
    import app.services as services

    monkeypatch.setattr(adherence, "utc_now", lambda: datetime(2026, 5, 27, 12, tzinfo=timezone.utc))
    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 5, 27, 12, tzinfo=timezone.utc))
    monkeypatch.setattr(read_model, "utc_now", lambda: datetime(2026, 5, 27, 12, tzinfo=timezone.utc))
    _create_taper_episode(
        location_code="week_stable",
        location_name="Week stable",
        healed_at=datetime(2026, 5, 20, 8, tzinfo=timezone.utc),
    )
    _login(client)

    response = client.get("/dashboard?adherence_range=week")
    css = client.get("/dashboard/assets/dashboard.css")

    assert response.status_code == 200
    assert 'id="adherence"' in response.text
    assert 'action="/dashboard#adherence"' in response.text
    assert 'data-date="2026-05-26"' in response.text
    assert 'data-status="not_due"' in response.text
    assert 'title="May 26' in response.text
    assert "not due" in response.text
    assert 'data-date="2026-05-27"' in response.text
    assert 'data-status="due"' in response.text
    assert 'title="May 27' in response.text
    assert "due" in response.text
    assert ".habit-not_due" in css.text
    assert ".habit-due" in css.text
    assert "#34c759" in css.text


def test_dashboard_defaults_to_dark_theme_and_exposes_theme_toggle(client):
    _create_taper_episode(location_code="theme_default", location_name="Theme default")
    _login(client)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert '<body class="theme-dark">' in response.text
    assert 'aria-label="Switch to light mode"' in response.text
    assert '☀️' in response.text


def test_dashboard_theme_toggle_sets_cookie(client):
    _login(client)
    dashboard = client.get("/dashboard")
    csrf = _csrf_token(dashboard.text)

    response = client.post("/dashboard/theme", data={"theme": "light", "csrf_token": csrf}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"
    cookie = response.headers["set-cookie"]
    assert "zema_theme=light" in cookie
    assert "Path=/dashboard" in cookie


def test_dashboard_privacy_toggle_sets_cookie_and_masks_location_names(client):
    _create_taper_episode(location_code="privacy_location", location_name="Sensitive Po")
    _login(client)
    current_view = "/dashboard?tab=settings&settings_tab=edit-locations"
    dashboard = client.get(current_view)
    csrf = _csrf_token(dashboard.text)

    response = client.post(
        "/dashboard/privacy",
        data={"privacy_mode": "on", "return_to": current_view, "csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == current_view
    cookie = response.headers["set-cookie"]
    assert "zema_privacy=on" in cookie
    assert "Path=/dashboard" in cookie

    masked = client.get(current_view)

    assert masked.status_code == 200
    assert "Sensitive Po" not in masked.text
    assert "S***********" in masked.text
    assert 'name="display_name" value="S***********" disabled' in masked.text
    assert "Disable privacy mode to edit this name." in masked.text
    assert 'class="secondary-button compact-button" type="submit" disabled' in masked.text
    assert 'aria-label="Disable privacy mode"' in masked.text
    assert 'class="icon-button privacy-toggle active"' in masked.text


def test_dashboard_topbar_is_fixed_and_contains_session_actions(client):
    _login(client)

    response = client.get("/dashboard")
    css = client.get("/dashboard/assets/dashboard.css")

    assert response.status_code == 200
    assert 'class="topbar-shell"' in response.text
    assert 'class="topbar-actions"' in response.text
    assert 'href="/dashboard?tab=settings"' in response.text
    assert 'aria-label="Undo last action"' in response.text
    assert 'class="button-icon"' in response.text
    assert 'd="M9 14 4 9l5-5"' in response.text
    assert "↻" not in response.text
    assert 'aria-label="Switch to light mode"' in response.text
    assert 'aria-label="Enable privacy mode"' in response.text
    assert "Log out" in response.text
    assert "position: sticky" in css.text
    assert ".button-icon" in css.text
    assert ".privacy-toggle.active" in css.text
    assert "z-index" in css.text


def test_adherence_card_height_is_stable_across_timeframes(client):
    _login(client)

    css = client.get("/dashboard/assets/dashboard.css")

    assert css.status_code == 200
    assert "align-items: start" in css.text
    assert "height: 190px" in css.text
    assert "overflow-y: auto" in css.text
    assert "padding: 2px 2px 10px" in css.text


def test_adherence_range_controls_render_and_select_last_month_by_default(client):
    _create_taper_episode(location_code="range_default", location_name="Range default")
    _login(client)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert 'name="adherence_range"' in response.text
    assert 'value="week"' in response.text
    assert 'value="month" onchange="this.form.submit()"' in response.text
    assert 'value="month" onchange="this.form.submit()" checked' in response.text
    assert 'value="year"' in response.text
    assert 'value="all"' in response.text
    assert 'name="from_date"' in response.text
    assert 'name="to_date"' in response.text
    assert "Last month" in response.text
    assert "System Trust" not in response.text
    assert "Generated " not in response.text


def test_preset_adherence_ranges_auto_apply_and_custom_apply_is_grouped(client):
    _login(client)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert 'value="week" onchange="this.form.submit()"' in response.text
    assert 'value="month" onchange="this.form.submit()"' in response.text
    assert 'value="year" onchange="this.form.submit()"' in response.text
    assert 'value="all" onchange="this.form.submit()"' in response.text
    assert 'class="custom-range-controls"' in response.text
    custom_block = response.text.split('class="custom-range-controls"', 1)[1].split("</div>", 1)[0]
    assert 'name="from_date"' in custom_block
    assert 'name="to_date"' in custom_block
    assert ">Apply<" in custom_block


def test_year_adherence_range_renders_reference_points(client, monkeypatch):
    import app.adherence as adherence
    import app.dashboard.read_model as read_model
    import app.services as services

    monkeypatch.setattr(adherence, "utc_now", lambda: datetime(2026, 5, 26, 12, tzinfo=timezone.utc))
    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 5, 26, 12, tzinfo=timezone.utc))
    monkeypatch.setattr(read_model, "utc_now", lambda: datetime(2026, 5, 26, 12, tzinfo=timezone.utc))
    _create_taper_episode(location_code="year_reference", location_name="Year reference")
    _login(client)

    response = client.get("/dashboard?adherence_range=year")

    assert response.status_code == 200
    assert 'class="habit-reference"' in response.text
    assert "May 27" in response.text
    assert "Today" in response.text
    assert 'data-range-days="365"' in response.text
    assert 'data-date="2025-05-27"' in response.text
    assert 'data-status="pre_start"' in response.text


def test_custom_adherence_range_uses_requested_dates(client, monkeypatch):
    import app.adherence as adherence
    import app.dashboard.read_model as read_model
    import app.services as services

    monkeypatch.setattr(adherence, "utc_now", lambda: datetime(2026, 5, 26, 12, tzinfo=timezone.utc))
    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 5, 26, 12, tzinfo=timezone.utc))
    monkeypatch.setattr(read_model, "utc_now", lambda: datetime(2026, 5, 26, 12, tzinfo=timezone.utc))
    _create_taper_episode(location_code="custom_range", location_name="Custom range")
    _login(client)

    response = client.get("/dashboard?adherence_range=custom&from_date=2026-05-20&to_date=2026-05-26")

    assert response.status_code == 200
    assert 'value="custom" checked' in response.text
    assert 'value="2026-05-20"' in response.text
    assert 'value="2026-05-26"' in response.text
    assert "Custom range" in response.text
    assert 'data-date="2026-05-20"' in response.text
    assert 'data-date="2026-05-26"' in response.text


def test_dashboard_subject_rename_updates_display_name(client):
    episode_id = _create_taper_episode(location_code="subject_rename", location_name="Subject rename location")
    db = SessionLocal()
    try:
        episode = db.get(TreatmentApplication, -1)  # keeps import names available for linters
        del episode
        from app.models import EczemaEpisode

        created_episode = db.get(EczemaEpisode, episode_id)
        subject_id = created_episode.subject_id
    finally:
        db.close()
    _login(client)
    dashboard = client.get("/dashboard")
    csrf = _csrf_token(dashboard.text)

    response = client.post(
        f"/dashboard/subjects/{subject_id}",
        data={"display_name": "Updated subject", "csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    db = SessionLocal()
    try:
        assert db.get(Subject, subject_id).display_name == "Updated subject"
    finally:
        db.close()
    updated = client.get("/dashboard?tab=settings&settings_tab=subject")
    assert "Updated subject" in updated.text


def test_dashboard_location_rename_updates_display_name(client):
    episode_id = _create_taper_episode(location_code="location_rename", location_name="Old location")
    db = SessionLocal()
    try:
        from app.models import EczemaEpisode

        location_id = db.get(EczemaEpisode, episode_id).location_id
    finally:
        db.close()
    _login(client)
    dashboard = client.get("/dashboard")
    csrf = _csrf_token(dashboard.text)

    response = client.post(
        f"/dashboard/locations/{location_id}",
        data={"display_name": "New location", "csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    db = SessionLocal()
    try:
        assert db.get(BodyLocation, location_id).display_name == "New location"
    finally:
        db.close()
    updated = client.get("/dashboard")
    assert "New location" in updated.text
    assert "Old location" not in updated.text


def test_dashboard_edit_locations_can_delete_location_and_tracking_history(client):
    episode_id = _create_taper_episode(location_code="delete_location", location_name="Delete location")
    db = SessionLocal()
    try:
        episode = db.get(EczemaEpisode, episode_id)
        location_id = episode.location_id
        assert db.query(TreatmentApplication).filter(TreatmentApplication.episode_id == episode_id).count() > 0
    finally:
        db.close()
    _login(client)
    edit_page = client.get("/dashboard?tab=settings&settings_tab=edit-locations")
    csrf = _csrf_token(edit_page.text)

    assert f'action="/dashboard/locations/{location_id}/delete"' in edit_page.text
    assert "danger-button compact-button" in edit_page.text

    response = client.post(
        f"/dashboard/locations/{location_id}/delete",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard?tab=settings&settings_tab=edit-locations"
    db = SessionLocal()
    try:
        assert db.get(BodyLocation, location_id) is None
        assert db.get(EczemaEpisode, episode_id) is None
        assert db.query(TreatmentApplication).filter(TreatmentApplication.episode_id == episode_id).count() == 0
        assert db.query(EpisodePhaseHistory).filter(EpisodePhaseHistory.episode_id == episode_id).count() == 0
        assert db.query(EpisodeEvent).filter(EpisodeEvent.episode_id == episode_id).count() == 0
    finally:
        db.close()
    updated = client.get("/dashboard")
    assert "Delete location" not in updated.text


def test_edit_location_delete_button_is_aligned_with_row_actions(client):
    _create_taper_episode(location_code="aligned_delete", location_name="Aligned delete")
    _login(client)

    response = client.get("/dashboard?tab=settings&settings_tab=edit-locations")
    css = client.get("/dashboard/assets/dashboard.css")

    assert response.status_code == 200
    assert 'class="delete-location-form settings-form"' in response.text
    assert ".location-edit-row .settings-form" in css.text
    assert "align-self: end" in css.text
    assert "display: flex" in css.text


def test_missing_location_image_file_uses_initial_fallback(client, monkeypatch):
    import app.dashboard.read_model as read_model
    import app.services as services

    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 5, 26, 12, tzinfo=timezone.utc))
    monkeypatch.setattr(read_model, "utc_now", lambda: datetime(2026, 5, 26, 12, tzinfo=timezone.utc))
    episode_id = _create_taper_episode(location_code="missing_image", location_name="Missing image")
    db = SessionLocal()
    try:
        from app.models import EczemaEpisode

        location = db.get(BodyLocation, db.get(EczemaEpisode, episode_id).location_id)
        location.image_storage_key = "definitely-missing-image-file.jpg"
        location.image_mime_type = "image/jpeg"
        db.add(location)
        db.commit()
    finally:
        db.close()
    _login(client)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Missing image" in response.text
    assert "definitely-missing-image-file.jpg" not in response.text
    assert 'aria-label="Open image for Missing image"' not in response.text


def test_upcoming_rows_show_location_image_thumbnail(client, monkeypatch):
    import app.dashboard.read_model as read_model
    import app.services as services

    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 5, 26, 12, tzinfo=timezone.utc))
    monkeypatch.setattr(read_model, "utc_now", lambda: datetime(2026, 5, 26, 12, tzinfo=timezone.utc))
    episode_id = _create_taper_episode(location_code="ear_left", location_name="Gehörgang links")
    db = SessionLocal()
    try:
        from app.models import EczemaEpisode

        location_id = db.get(EczemaEpisode, episode_id).location_id
    finally:
        db.close()
    _login(client)
    csrf = _csrf_token(client.get("/dashboard").text)
    upload = client.post(
        f"/dashboard/locations/{location_id}/image",
        data={"csrf_token": csrf},
        files={"image": ("ear.png", PNG_BYTES, "image/png")},
        follow_redirects=False,
    )
    assert upload.status_code == 303

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Gehörgang links" in response.text
    assert f'data-image-url="/dashboard/locations/{location_id}/image"' in response.text
    assert f'src="/dashboard/locations/{location_id}/image"' in response.text
    assert 'class="location-image thumbnail-image"' in response.text
    assert 'id="image-dialog"' in response.text


def test_due_cards_show_only_location_thumbnail_phase_and_phase_one_slot(client, monkeypatch):
    import app.dashboard.read_model as read_model
    import app.services as services

    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 5, 26, 16, tzinfo=timezone.utc))
    monkeypatch.setattr(read_model, "utc_now", lambda: datetime(2026, 5, 26, 16, tzinfo=timezone.utc))
    episode_id = _create_phase_one_episode(location_code="due_minimal", location_name="Due minimal")
    db = SessionLocal()
    try:
        from app.models import EczemaEpisode

        location_id = db.get(EczemaEpisode, episode_id).location_id
    finally:
        db.close()
    _login(client)
    csrf = _csrf_token(client.get("/dashboard?tab=settings").text)
    upload = client.post(
        f"/dashboard/locations/{location_id}/image",
        data={"csrf_token": csrf},
        files={"image": ("due.png", PNG_BYTES, "image/png")},
        follow_redirects=False,
    )
    assert upload.status_code == 303

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Due minimal" in response.text
    assert "Phase 1 · evening slot" in response.text
    assert "Child due_minimal" not in response.text
    assert 'name="treatment_name"' not in response.text
    assert 'name="quantity_text"' not in response.text
    assert ">Log<" in response.text
    assert "Log treatment" not in response.text
    assert "Healed" in response.text
    assert "success-button" in response.text
    assert f'data-image-url="/dashboard/locations/{location_id}/image"' in response.text


def test_non_phase_one_due_card_hides_slot(client, monkeypatch):
    import app.dashboard.read_model as read_model
    import app.services as services

    monkeypatch.setattr(services, "utc_now", lambda: datetime(2026, 5, 27, 12, tzinfo=timezone.utc))
    monkeypatch.setattr(read_model, "utc_now", lambda: datetime(2026, 5, 27, 12, tzinfo=timezone.utc))
    _create_taper_episode(location_code="phase_two_due", location_name="Phase two due")
    _login(client)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert "Phase two due" in response.text
    assert "Phase 2 ·" not in response.text
    assert "Relapsed" in response.text
    assert "Relapses" not in response.text


def test_dashboard_due_card_healed_button_moves_phase_one_to_taper(client, monkeypatch):
    import app.dashboard.read_model as read_model
    import app.dashboard.routes as dashboard_routes
    import app.services as services

    now = datetime(2026, 5, 26, 16, tzinfo=timezone.utc)
    monkeypatch.setattr(dashboard_routes, "utc_now", lambda: now)
    monkeypatch.setattr(services, "utc_now", lambda: now)
    monkeypatch.setattr(read_model, "utc_now", lambda: now)
    episode_id = _create_phase_one_episode(location_code="dashboard_healed", location_name="Dashboard healed")
    _login(client)
    csrf = _csrf_token(client.get("/dashboard").text)

    response = client.post(f"/dashboard/episodes/{episode_id}/heal", data={"csrf_token": csrf}, follow_redirects=False)

    assert response.status_code == 303
    db = SessionLocal()
    try:
        episode = db.get(EczemaEpisode, episode_id)
        assert episode.status == "in_taper"
        assert episode.current_phase_number == 2
    finally:
        db.close()
    undo_token = _csrf_token(client.get("/dashboard").text)
    undo = client.post("/dashboard/treatments/undo-last", data={"csrf_token": undo_token}, follow_redirects=False)
    assert undo.status_code == 303
    db = SessionLocal()
    try:
        episode = db.get(EczemaEpisode, episode_id)
        assert episode.status == "active_flare"
        assert episode.current_phase_number == 1
        assert episode.healed_at is None
    finally:
        db.close()


def test_dashboard_due_card_relapse_button_moves_taper_to_phase_one(client, monkeypatch):
    import app.dashboard.read_model as read_model
    import app.dashboard.routes as dashboard_routes
    import app.services as services

    now = datetime(2026, 5, 27, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(dashboard_routes, "utc_now", lambda: now)
    monkeypatch.setattr(services, "utc_now", lambda: now)
    monkeypatch.setattr(read_model, "utc_now", lambda: now)
    episode_id = _create_taper_episode(location_code="dashboard_relapse", location_name="Dashboard relapse")
    _login(client)
    csrf = _csrf_token(client.get("/dashboard").text)

    response = client.post(f"/dashboard/episodes/{episode_id}/relapse", data={"csrf_token": csrf}, follow_redirects=False)

    assert response.status_code == 303
    db = SessionLocal()
    try:
        episode = db.get(EczemaEpisode, episode_id)
        assert episode.status == "active_flare"
        assert episode.current_phase_number == 1
        assert episode.healed_at is None
    finally:
        db.close()
    undo_token = _csrf_token(client.get("/dashboard").text)
    undo = client.post("/dashboard/treatments/undo-last", data={"csrf_token": undo_token}, follow_redirects=False)
    assert undo.status_code == 303
    db = SessionLocal()
    try:
        episode = db.get(EczemaEpisode, episode_id)
        assert episode.status == "in_taper"
        assert episode.current_phase_number == 2
        assert episode.healed_at is not None
    finally:
        db.close()


def test_dashboard_account_settings_update_username_and_password(client):
    _login(client)
    dashboard = client.get("/dashboard")
    csrf = _csrf_token(dashboard.text)

    response = client.post(
        "/dashboard/account",
        data={
            "username": "new-admin",
            "current_password": "admin",
            "new_password": "new-password",
            "confirm_password": "new-password",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.username == "new-admin").one()
        assert verify_password("new-password", account.password_hash)
    finally:
        db.close()

    old_login_form = client.get("/dashboard/login")
    old_csrf = _csrf_token(old_login_form.text)
    old_login = client.post(
        "/dashboard/login",
        data={"username": "admin", "password": "admin", "csrf_token": old_csrf},
        follow_redirects=False,
    )
    assert old_login.status_code == 401

    new_login_form = client.get("/dashboard/login")
    new_csrf = _csrf_token(new_login_form.text)
    new_login = client.post(
        "/dashboard/login",
        data={"username": "new-admin", "password": "new-password", "csrf_token": new_csrf},
        follow_redirects=False,
    )
    assert new_login.status_code == 303


def test_dashboard_account_settings_reject_bad_current_password_and_mismatch(client):
    _login(client)
    csrf = _csrf_token(client.get("/dashboard").text)

    bad_current = client.post(
        "/dashboard/account",
        data={
            "username": "admin2",
            "current_password": "wrong",
            "new_password": "new-password",
            "confirm_password": "new-password",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert bad_current.status_code == 403

    mismatch = client.post(
        "/dashboard/account",
        data={
            "username": "admin",
            "current_password": "admin",
            "new_password": "new-password",
            "confirm_password": "different",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert mismatch.status_code == 422


def test_dashboard_management_forms_are_grouped_as_settings_panels(client):
    _create_taper_episode(location_code="settings_tabs", location_name="Settings tabs")
    _login(client)

    overview = client.get("/dashboard")
    response = client.get("/dashboard?tab=settings")
    subject = client.get("/dashboard?tab=settings&settings_tab=subject")
    add_location = client.get("/dashboard?tab=settings&settings_tab=add-location")
    edit_locations = client.get("/dashboard?tab=settings&settings_tab=edit-locations")

    assert overview.status_code == 200
    assert 'id="settings"' not in overview.text
    assert response.status_code == 200
    assert 'id="settings"' in response.text
    assert 'class="settings-tabs"' in response.text
    assert 'href="/dashboard?tab=settings&settings_tab=account" class="active"' in response.text
    assert 'href="/dashboard?tab=settings&settings_tab=subject"' in response.text
    assert "Update account" in response.text
    assert "Display name" not in response.text
    assert "Create location" not in response.text
    assert "Upload image" not in response.text
    assert "Display name" in subject.text
    assert "Add location" in add_location.text
    assert 'name="code"' not in add_location.text
    assert "Upload image" in edit_locations.text or "Replace image" in edit_locations.text
    assert "settings_tabs" not in edit_locations.text


def test_dashboard_add_location_with_image(client):
    _login(client)
    dashboard = client.get("/dashboard")
    csrf = _csrf_token(dashboard.text)

    response = client.post(
        "/dashboard/locations",
        data={"display_name": "Gehörgang Neu", "csrf_token": csrf},
        files={"image": ("cheek.png", PNG_BYTES, "image/png")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard?tab=settings&settings_tab=add-location&created_location=1"
    db = SessionLocal()
    try:
        location = db.query(BodyLocation).filter(BodyLocation.code == "gehoergang_neu").one()
        assert location.display_name == "Gehörgang Neu"
        assert location.image_mime_type == "image/png"
        assert location.image_storage_key is not None
        episode = db.query(EczemaEpisode).filter(EczemaEpisode.location_id == location.id).one()
        assert episode.status == "active_flare"
        assert episode.current_phase_number == 1
    finally:
        db.close()
    success = client.get(response.headers["location"])
    assert "Location created and tracking started." in success.text


def test_dashboard_replace_location_image(client):
    episode_id = _create_taper_episode(location_code="replace_image", location_name="Replace image")
    db = SessionLocal()
    try:
        from app.models import EczemaEpisode

        location_id = db.get(EczemaEpisode, episode_id).location_id
    finally:
        db.close()
    _login(client)
    csrf = _csrf_token(client.get("/dashboard").text)
    first = client.post(
        f"/dashboard/locations/{location_id}/image",
        data={"csrf_token": csrf},
        files={"image": ("first.png", PNG_BYTES, "image/png")},
        follow_redirects=False,
    )
    assert first.status_code == 303
    db = SessionLocal()
    try:
        old_storage_key = db.get(BodyLocation, location_id).image_storage_key
    finally:
        db.close()

    csrf = _csrf_token(client.get("/dashboard").text)
    second = client.post(
        f"/dashboard/locations/{location_id}/image",
        data={"csrf_token": csrf},
        files={"image": ("second.jpg", JPEG_BYTES, "image/jpeg")},
        follow_redirects=False,
    )

    assert second.status_code == 303
    db = SessionLocal()
    try:
        location = db.get(BodyLocation, location_id)
        assert location.image_mime_type == "image/jpeg"
        assert location.image_storage_key != old_storage_key
    finally:
        db.close()


def test_dashboard_html_does_not_expose_tokens_or_browser_token_storage(client):
    _login(client)
    response = client.get("/dashboard")
    html = response.text.lower()

    assert "api key" not in html
    assert "bot token" not in html
    assert "bearer " not in html
    assert "localstorage" not in html
    assert "sessionstorage" not in html

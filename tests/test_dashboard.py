from __future__ import annotations

from datetime import date, datetime, timezone
from html.parser import HTMLParser

from app.core.database import SessionLocal
from app.core.time import utc_now
from app.models import BodyLocation, Subject, TreatmentApplication
from app.services import create_episode, create_location, create_subject, heal_episode, log_application


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


def test_authenticated_dashboard_renders_all_clear_overview(client):
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


def test_upcoming_times_are_rendered_in_deployment_timezone(client, monkeypatch):
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
    assert "Next May 27, 00:00" in response.text
    assert "Next May 26, 22:00" not in response.text


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
    assert 'data-status="missed"' not in response.text


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


def test_adherence_range_controls_render_and_select_last_month_by_default(client):
    _create_taper_episode(location_code="range_default", location_name="Range default")
    _login(client)

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert 'name="adherence_range"' in response.text
    assert 'value="week"' in response.text
    assert 'value="month" checked' in response.text
    assert 'value="year"' in response.text
    assert 'value="all"' in response.text
    assert 'name="from_date"' in response.text
    assert 'name="to_date"' in response.text
    assert "Last month" in response.text


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
    updated = client.get("/dashboard")
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


def test_dashboard_add_location_with_image(client):
    _login(client)
    dashboard = client.get("/dashboard")
    csrf = _csrf_token(dashboard.text)

    response = client.post(
        "/dashboard/locations",
        data={"code": "new_cheek", "display_name": "New cheek", "csrf_token": csrf},
        files={"image": ("cheek.png", PNG_BYTES, "image/png")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    db = SessionLocal()
    try:
        location = db.query(BodyLocation).filter(BodyLocation.code == "new_cheek").one()
        assert location.display_name == "New cheek"
        assert location.image_mime_type == "image/png"
        assert location.image_storage_key is not None
    finally:
        db.close()


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

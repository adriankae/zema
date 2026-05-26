from __future__ import annotations

from datetime import date, datetime, timezone
from html.parser import HTMLParser

from app.core.database import SessionLocal
from app.core.time import utc_now
from app.models import TreatmentApplication
from app.services import create_episode, create_location, create_subject, heal_episode, log_application


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
    assert "All clear for now" in response.text
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


def test_dashboard_html_does_not_expose_tokens_or_browser_token_storage(client):
    _login(client)
    response = client.get("/dashboard")
    html = response.text.lower()

    assert "api key" not in html
    assert "bot token" not in html
    assert "bearer " not in html
    assert "localstorage" not in html
    assert "sessionstorage" not in html

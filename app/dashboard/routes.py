from __future__ import annotations

import json
from datetime import date, datetime, timezone
from importlib import metadata
from pathlib import Path
import re
import tomllib
import unicodedata

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.config import settings
from app.core.time import to_local, utc_now
from app.dashboard.auth import (
    LOGIN_CSRF_COOKIE,
    SESSION_COOKIE,
    SESSION_PATH,
    dashboard_cookie_secure,
    issue_csrf_token,
    issue_login_csrf_token,
    load_dashboard_account,
    require_valid_csrf,
    require_valid_login_csrf,
)
from app.dashboard.read_model import build_dashboard_overview
from app.import_export import export_account_data, import_account_data
from app.location_images import get_location_image_file, store_location_image
from app.models import Account, AccountApiKey, BodyLocation, EczemaEpisode, EpisodeEvent, EpisodePhaseHistory, Subject, TreatmentApplication
from app.services import authenticate_user, calculate_phase_due_end_at, catch_up_episode_phases, create_episode, create_event, create_location, create_subject, delete_application, delete_location, due_items, get_location, get_subject, heal_episode, issue_login_token, log_application, relapse_episode, update_account_credentials
from app.telegram_settings import (
    discover_telegram_chats,
    parse_int_list,
    reset_telegram_settings,
    save_telegram_bot_token,
    save_telegram_settings,
    send_telegram_setup_success,
    set_telegram_enabled,
    telegram_settings_view,
)


router = APIRouter(prefix="/dashboard", tags=["dashboard"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
ASSET_DIR = Path(__file__).parent / "static"
THEME_COOKIE = "zema_theme"
THEME_PATH = SESSION_PATH
PRIVACY_COOKIE = "zema_privacy"
OVERVIEW_TAB = "overview"
SETTINGS_TAB = "settings"
SETTINGS_TABS = {"account", "subject", "add-location", "edit-locations", "backup", "network", "telegram"}
APP_VERSION = "unknown"


def _app_version() -> str:
    global APP_VERSION
    if APP_VERSION != "unknown":
        return APP_VERSION
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    try:
        with pyproject_path.open("rb") as handle:
            APP_VERSION = tomllib.load(handle)["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        try:
            APP_VERSION = metadata.version("zema")
        except metadata.PackageNotFoundError:
            APP_VERSION = "unknown"
    return APP_VERSION



def _format_datetime(value) -> str:
    if value is None:
        return "Not yet"
    return to_local(value).strftime("%b %-d, %H:%M")


def _format_date(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = to_local(value).date()
    return value.strftime("%b %-d")


templates.env.filters["zdt"] = _format_datetime
templates.env.filters["zdate"] = _format_date


def _mask_display_name(value: str | None, privacy_mode: bool = False) -> str:
    if value is None:
        return ""
    text = str(value)
    if not privacy_mode or text == "":
        return text
    return text[:1] + ("*" * max(len(text) - 1, 0))


templates.env.filters["privacy_name"] = _mask_display_name


def _dashboard_theme(request: Request) -> str:
    return "light" if request.cookies.get(THEME_COOKIE) == "light" else "dark"


def _privacy_mode(request: Request) -> bool:
    return request.cookies.get(PRIVACY_COOKIE) == "on"


def _network_status(request: Request) -> dict[str, str | bool | int]:
    host_bind = settings.zema_host_bind.strip() or "127.0.0.1"
    port = settings.zema_port or 28173
    is_local_only = host_bind in {"127.0.0.1", "localhost", "::1"}
    browser_host = "localhost" if is_local_only else request.url.hostname or host_bind
    dashboard_url = f"http://{browser_host}:{port}/dashboard"
    health_url = f"http://{browser_host}:{port}/health"
    return {
        "host_bind": host_bind,
        "port": port,
        "dashboard_url": dashboard_url,
        "health_url": health_url,
        "access_label": "Local only" if is_local_only else "Network exposed",
        "is_local_only": is_local_only,
    }


def _dashboard_return_target(value: str | None, default: str = "/dashboard") -> str:
    if value in {"/dashboard", "/dashboard/"}:
        return value
    if value and (value.startswith("/dashboard?") or value.startswith("/dashboard#") or value.startswith("/dashboard/")):
        return value
    return default


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _overview_from_request(db: Session, account: Account, request: Request):
    query = request.query_params
    return build_dashboard_overview(
        db,
        account,
        adherence_range=query.get("adherence_range") or "month",
        from_date=_parse_date(query.get("from_date")),
        to_date=_parse_date(query.get("to_date")),
    )


def _active_tab(request: Request) -> str:
    return SETTINGS_TAB if request.query_params.get("tab") == SETTINGS_TAB else OVERVIEW_TAB


def _active_settings_tab(request: Request) -> str:
    selected = request.query_params.get("settings_tab") or "account"
    return selected if selected in SETTINGS_TABS else "account"


def _success_message(request: Request) -> str | None:
    if request.query_params.get("created_location") == "1":
        return "Location created and tracking started."
    if request.query_params.get("imported") == "1":
        return "Import complete. Tracking data was replaced from the backup file."
    if request.query_params.get("telegram_saved") == "1":
        return "Bot connected."
    if request.query_params.get("telegram_enabled") == "1":
        return "Bot is starting."
    if request.query_params.get("telegram_disabled") == "1":
        return "Bot is stopping."
    if request.query_params.get("telegram_reset") == "1":
        return "Bot setup reset."
    if request.query_params.get("telegram_chat_linked") == "1":
        return "Chat linked."
    return None


def _redirect_to_login() -> RedirectResponse:
    return RedirectResponse("/dashboard/login", status_code=status.HTTP_303_SEE_OTHER)


def _require_account(request: Request, db: Session) -> Account:
    account = load_dashboard_account(request, db)
    if account is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="dashboard login required")
    return account


def _html(request: Request, template: str, context: dict, status_code: int = 200) -> HTMLResponse:
    context.setdefault("app_version", _app_version())
    return templates.TemplateResponse(request, template, context, status_code=status_code)


def _telegram_settings_html(
    request: Request,
    db: Session,
    account: Account,
    *,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return _html(
        request,
        "dashboard.html",
        {
            "account": account,
            "overview": _overview_from_request(db, account, request),
            "csrf_token": issue_csrf_token(account),
            "theme": _dashboard_theme(request),
            "privacy_mode": _privacy_mode(request),
            "active_tab": SETTINGS_TAB,
            "active_settings_tab": "telegram",
            "error": error,
            "telegram": telegram_settings_view(db, account),
            "network": _network_status(request),
        },
        status_code=status_code,
    )


@router.get("", response_class=HTMLResponse)
def dashboard_home(request: Request, db: Session = Depends(get_db)):
    account = load_dashboard_account(request, db)
    if account is None:
        return _redirect_to_login()
    overview = _overview_from_request(db, account, request)
    return _html(
        request,
        "dashboard.html",
        {
            "account": account,
            "overview": overview,
            "csrf_token": issue_csrf_token(account),
            "theme": _dashboard_theme(request),
            "privacy_mode": _privacy_mode(request),
            "active_tab": _active_tab(request),
            "active_settings_tab": _active_settings_tab(request),
            "success_message": _success_message(request),
            "telegram": telegram_settings_view(db, account),
            "network": _network_status(request),
        },
    )


@router.get("/", response_class=HTMLResponse)
def dashboard_home_slash(request: Request, db: Session = Depends(get_db)):
    return dashboard_home(request, db)


@router.get("/login", response_class=HTMLResponse)
def dashboard_login_form(request: Request):
    csrf_token = issue_login_csrf_token()
    response = _html(request, "login.html", {"error": None, "csrf_token": csrf_token, "theme": _dashboard_theme(request)})
    response.set_cookie(
        LOGIN_CSRF_COOKIE,
        csrf_token,
        httponly=True,
        secure=dashboard_cookie_secure(),
        samesite="lax",
        path=SESSION_PATH,
    )
    return response


@router.get("/assets/{filename}")
def dashboard_asset(filename: str):
    asset_types = {
        "dashboard.css": "text/css",
        "favicon.svg": "image/svg+xml",
    }
    media_type = asset_types.get(filename)
    if media_type is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")
    return FileResponse(ASSET_DIR / filename, media_type=media_type)


@router.post("/login", response_class=HTMLResponse)
def dashboard_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    try:
        require_valid_login_csrf(csrf_token, request.cookies.get(LOGIN_CSRF_COOKIE))
    except HTTPException:
        fresh_token = issue_login_csrf_token()
        response = _html(
            request,
            "login.html",
            {"error": "This form expired. Reload and try again.", "csrf_token": fresh_token, "theme": _dashboard_theme(request)},
            status_code=status.HTTP_403_FORBIDDEN,
        )
        response.set_cookie(
            LOGIN_CSRF_COOKIE,
            fresh_token,
            httponly=True,
            secure=dashboard_cookie_secure(),
            samesite="lax",
            path=SESSION_PATH,
        )
        return response
    try:
        account = authenticate_user(db, username, password)
    except HTTPException:
        return _html(request, "login.html", {"error": "Invalid username or password.", "csrf_token": csrf_token, "theme": _dashboard_theme(request)}, status_code=status.HTTP_401_UNAUTHORIZED)
    response = RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE,
        issue_login_token(account),
        httponly=True,
        secure=dashboard_cookie_secure(),
        samesite="lax",
        path=SESSION_PATH,
    )
    response.delete_cookie(LOGIN_CSRF_COOKIE, path=SESSION_PATH, samesite="lax", secure=dashboard_cookie_secure(), httponly=True)
    return response



@router.post("/account")
def dashboard_update_account(
    request: Request,
    username: str = Form(...),
    current_password: str = Form(...),
    new_password: str | None = Form(default=None),
    confirm_password: str | None = Form(default=None),
    csrf_token: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    account = load_dashboard_account(request, db)
    if account is None:
        return _redirect_to_login()
    require_valid_csrf(csrf_token, account)
    cleaned_new_password = _clean(new_password)
    if cleaned_new_password is not None and cleaned_new_password != (confirm_password or ""):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="password confirmation does not match")
    updated = update_account_credentials(db, account, username, current_password, cleaned_new_password)
    response = RedirectResponse("/dashboard?tab=settings&settings_tab=account", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE,
        issue_login_token(updated),
        httponly=True,
        secure=dashboard_cookie_secure(),
        samesite="lax",
        path=SESSION_PATH,
    )
    return response


@router.get("/export")
def dashboard_export_data(request: Request, db: Session = Depends(get_db)):
    account = _require_account(request, db)
    payload = export_account_data(db, account)
    filename = f"zema-export-{date.today().isoformat()}.json"
    return Response(
        json.dumps(payload, indent=2, sort_keys=True),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import")
async def dashboard_import_data(
    request: Request,
    csrf_token: str | None = Form(default=None),
    backup_file: UploadFile = File(...),
    confirm_replace: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    account = load_dashboard_account(request, db)
    if account is None:
        return _redirect_to_login()
    require_valid_csrf(csrf_token, account)
    if confirm_replace != "yes":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="confirm replace is required")
    try:
        payload = json.loads((await backup_file.read()).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid import JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid import JSON")
    import_account_data(db, account, payload)
    return RedirectResponse("/dashboard?tab=settings&settings_tab=backup&imported=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/telegram")
def dashboard_update_telegram(
    request: Request,
    bot_token: str | None = Form(default=None),
    allowed_chat_ids: str | None = Form(default=None),
    allowed_user_ids: str | None = Form(default=None),
    csrf_token: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    account = load_dashboard_account(request, db)
    if account is None:
        return _redirect_to_login()
    require_valid_csrf(csrf_token, account)
    try:
        save_telegram_settings(
            db,
            account,
            bot_token=_clean(bot_token),
            allowed_chat_ids=parse_int_list(allowed_chat_ids, label="Allowed chat IDs"),
            allowed_user_ids=parse_int_list(allowed_user_ids, label="Allowed user IDs"),
            allow_writes=True,
            allow_adherence_rebuild=False,
            is_enabled=None,
        )
        set_telegram_enabled(db, account, True)
        send_telegram_setup_success(db, account)
    except HTTPException as exc:
        return _telegram_settings_html(request, db, account, error=str(exc.detail), status_code=exc.status_code)
    return RedirectResponse("/dashboard?tab=settings&settings_tab=telegram&telegram_enabled=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/telegram/token")
def dashboard_save_telegram_token(
    request: Request,
    bot_token: str = Form(...),
    csrf_token: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    account = load_dashboard_account(request, db)
    if account is None:
        return _redirect_to_login()
    require_valid_csrf(csrf_token, account)
    try:
        save_telegram_bot_token(db, account, bot_token)
    except HTTPException as exc:
        return _telegram_settings_html(request, db, account, error=str(exc.detail), status_code=exc.status_code)
    return RedirectResponse("/dashboard?tab=settings&settings_tab=telegram&telegram_saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/telegram/discover")
def dashboard_discover_telegram_chats(
    request: Request,
    csrf_token: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    account = load_dashboard_account(request, db)
    if account is None:
        return _redirect_to_login()
    require_valid_csrf(csrf_token, account)
    try:
        chats = discover_telegram_chats(db, account)
    except HTTPException as exc:
        return _telegram_settings_html(request, db, account, error=str(exc.detail), status_code=exc.status_code)
    if len(chats) == 1:
        try:
            save_telegram_settings(
                db,
                account,
                bot_token=None,
                allowed_chat_ids=[chats[0].id],
                allowed_user_ids=[],
                allow_writes=True,
                allow_adherence_rebuild=False,
                is_enabled=None,
            )
            set_telegram_enabled(db, account, True)
            send_telegram_setup_success(db, account)
        except HTTPException as exc:
            return _telegram_settings_html(request, db, account, error=str(exc.detail), status_code=exc.status_code)
        return RedirectResponse("/dashboard?tab=settings&settings_tab=telegram&telegram_enabled=1", status_code=status.HTTP_303_SEE_OTHER)
    overview = _overview_from_request(db, account, request)
    telegram = telegram_settings_view(db, account)
    telegram.discovered_chats.extend(chats)
    if not chats:
        telegram.notice = "No Telegram chats found yet. Send /start to the bot, then try again."
    return _html(
        request,
        "dashboard.html",
        {
            "account": account,
            "overview": overview,
            "csrf_token": issue_csrf_token(account),
            "theme": _dashboard_theme(request),
            "privacy_mode": _privacy_mode(request),
            "active_tab": SETTINGS_TAB,
            "active_settings_tab": "telegram",
            "telegram": telegram,
            "network": _network_status(request),
        },
    )


@router.post("/telegram/enable")
def dashboard_enable_telegram(
    request: Request,
    csrf_token: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    account = load_dashboard_account(request, db)
    if account is None:
        return _redirect_to_login()
    require_valid_csrf(csrf_token, account)
    try:
        set_telegram_enabled(db, account, True)
    except HTTPException as exc:
        return _telegram_settings_html(request, db, account, error=str(exc.detail), status_code=exc.status_code)
    return RedirectResponse("/dashboard?tab=settings&settings_tab=telegram&telegram_enabled=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/telegram/disable")
def dashboard_disable_telegram(
    request: Request,
    csrf_token: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    account = load_dashboard_account(request, db)
    if account is None:
        return _redirect_to_login()
    require_valid_csrf(csrf_token, account)
    try:
        set_telegram_enabled(db, account, False)
    except HTTPException as exc:
        return _telegram_settings_html(request, db, account, error=str(exc.detail), status_code=exc.status_code)
    return RedirectResponse("/dashboard?tab=settings&settings_tab=telegram&telegram_disabled=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/telegram/reset")
def dashboard_reset_telegram(
    request: Request,
    csrf_token: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    account = load_dashboard_account(request, db)
    if account is None:
        return _redirect_to_login()
    require_valid_csrf(csrf_token, account)
    reset_telegram_settings(db, account)
    return RedirectResponse("/dashboard?tab=settings&settings_tab=telegram&telegram_reset=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/theme")
def dashboard_theme(
    request: Request,
    theme: str = Form(...),
    csrf_token: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    account = load_dashboard_account(request, db)
    if account is None:
        return _redirect_to_login()
    require_valid_csrf(csrf_token, account)
    selected = "light" if theme == "light" else "dark"
    response = RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        THEME_COOKIE,
        selected,
        httponly=True,
        secure=dashboard_cookie_secure(),
        samesite="lax",
        path=THEME_PATH,
    )
    return response


@router.post("/privacy")
def dashboard_privacy(
    request: Request,
    privacy_mode: str = Form(...),
    return_to: str = Form(default="/dashboard"),
    csrf_token: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    account = load_dashboard_account(request, db)
    if account is None:
        return _redirect_to_login()
    require_valid_csrf(csrf_token, account)
    selected = "on" if privacy_mode == "on" else "off"
    response = RedirectResponse(_dashboard_return_target(return_to), status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        PRIVACY_COOKIE,
        selected,
        httponly=True,
        secure=dashboard_cookie_secure(),
        samesite="lax",
        path=SESSION_PATH,
    )
    return response


@router.post("/logout")
def dashboard_logout(
    request: Request,
    csrf_token: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    account = load_dashboard_account(request, db)
    if account is None:
        return _redirect_to_login()
    require_valid_csrf(csrf_token, account)
    response = RedirectResponse("/dashboard/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE, path=SESSION_PATH, samesite="lax", secure=dashboard_cookie_secure(), httponly=True)
    return response


@router.post("/treatments")
def dashboard_log_treatment(
    request: Request,
    episode_id: int = Form(...),
    treatment_type: str | None = Form(default=None),
    treatment_name: str | None = Form(default=None),
    quantity_text: str | None = Form(default=None),
    notes: str | None = Form(default=None),
    csrf_token: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    account = load_dashboard_account(request, db)
    if account is None:
        return _redirect_to_login()
    try:
        require_valid_csrf(csrf_token, account)
    except HTTPException:
        overview = _overview_from_request(db, account, request)
        return _html(
            request,
            "dashboard.html",
            {
                "account": account,
                "overview": overview,
                "csrf_token": issue_csrf_token(account),
                "theme": _dashboard_theme(request),
                "privacy_mode": _privacy_mode(request),
                "active_tab": _active_tab(request),
                "active_settings_tab": _active_settings_tab(request),
                "error": "This form expired. Reload and try again.",
                "network": _network_status(request),
            },
            status_code=status.HTTP_403_FORBIDDEN,
        )
    log_application(
        db,
        account,
        episode_id,
        utc_now(),
        _clean(treatment_type),
        _clean(treatment_name),
        _clean(quantity_text),
        _clean(notes),
        "user",
        f"user:{account.id}",
    )
    return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/episodes/{episode_id}/heal")
def dashboard_mark_episode_healed(
    episode_id: int,
    request: Request,
    csrf_token: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    account = load_dashboard_account(request, db)
    if account is None:
        return _redirect_to_login()
    require_valid_csrf(csrf_token, account)
    heal_episode(db, account, episode_id, utc_now(), "user", f"user:{account.id}")
    return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/episodes/{episode_id}/relapse")
def dashboard_mark_episode_relapse(
    episode_id: int,
    request: Request,
    csrf_token: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    account = load_dashboard_account(request, db)
    if account is None:
        return _redirect_to_login()
    require_valid_csrf(csrf_token, account)
    relapse_episode(db, account, episode_id, utc_now(), "dashboard", "user", f"user:{account.id}")
    return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/treatments/all-due")
def dashboard_log_all_due_treatments(
    request: Request,
    csrf_token: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    account = load_dashboard_account(request, db)
    if account is None:
        return _redirect_to_login()
    try:
        require_valid_csrf(csrf_token, account)
    except HTTPException:
        overview = _overview_from_request(db, account, request)
        return _html(
            request,
            "dashboard.html",
            {
                "account": account,
                "overview": overview,
                "csrf_token": issue_csrf_token(account),
                "theme": _dashboard_theme(request),
                "privacy_mode": _privacy_mode(request),
                "active_tab": _active_tab(request),
                "active_settings_tab": _active_settings_tab(request),
                "error": "This form expired. Reload and try again.",
                "network": _network_status(request),
            },
            status_code=status.HTTP_403_FORBIDDEN,
        )
    catch_up_episode_phases(db, reason="dashboard-log-all", account=account)
    now = utc_now()
    for item in due_items(db, account):
        log_application(
            db,
            account,
            int(item["episode_id"]),
            now,
            "steroid",
            None,
            None,
            None,
            "user",
            f"user:{account.id}",
        )
    return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/adherence/log-missing")
def dashboard_log_missing_adherence(
    request: Request,
    episode_id: int = Form(...),
    applied_at: str = Form(...),
    phase_number: int = Form(...),
    return_to: str = Form(default="/dashboard#adherence"),
    csrf_token: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    account = load_dashboard_account(request, db)
    if account is None:
        return _redirect_to_login()
    require_valid_csrf(csrf_token, account)
    try:
        parsed_applied_at = datetime.fromisoformat(applied_at)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid applied_at")
    if parsed_applied_at.tzinfo is None:
        parsed_applied_at = parsed_applied_at.replace(tzinfo=timezone.utc)
    try:
        _log_or_restore_missing_adherence(
            db,
            account,
            episode_id,
            parsed_applied_at.astimezone(timezone.utc),
            phase_number,
        )
    except IntegrityError:
        db.rollback()
    target = _dashboard_return_target(return_to, "/dashboard#adherence")
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


def _log_or_restore_missing_adherence(
    db: Session,
    account: Account,
    episode_id: int,
    applied_at: datetime,
    phase_number: int,
) -> TreatmentApplication:
    existing = db.execute(
        select(TreatmentApplication)
        .join(EczemaEpisode, EczemaEpisode.id == TreatmentApplication.episode_id)
        .where(
            EczemaEpisode.account_id == account.id,
            TreatmentApplication.episode_id == episode_id,
            TreatmentApplication.applied_at == applied_at,
        )
        .limit(1)
    ).scalar_one_or_none()
    if existing is None:
        return log_application(
            db,
            account,
            episode_id,
            applied_at,
            "steroid",
            None,
            None,
            "Backfilled from adherence detail",
            "system",
            f"adherence-backfill:user:{account.id}",
            phase_number_snapshot=phase_number,
        )
    if not existing.is_deleted and not existing.is_voided:
        return existing
    existing.is_deleted = False
    existing.deleted_at = None
    existing.is_voided = False
    existing.voided_at = None
    existing.treatment_type = "steroid"
    existing.treatment_name = None
    existing.quantity_text = None
    existing.notes = "Backfilled from adherence detail"
    existing.phase_number_snapshot = phase_number
    existing.updated_at = utc_now()
    db.add(existing)
    create_event(
        db,
        episode_id=existing.episode_id,
        event_type="application_logged",
        actor_type="system",
        actor_id=f"adherence-backfill:user:{account.id}",
        occurred_at=applied_at,
        payload={
            "application_id": existing.id,
            "applied_at": applied_at.isoformat(),
            "treatment_type": existing.treatment_type,
            "phase_number_snapshot": existing.phase_number_snapshot,
            "treatment_name": existing.treatment_name,
            "quantity_text": existing.quantity_text,
            "notes": existing.notes,
            "restored": True,
        },
    )
    db.commit()
    db.refresh(existing)
    return existing


@router.post("/treatments/undo-last")
def dashboard_undo_last_treatment_log(
    request: Request,
    csrf_token: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    account = load_dashboard_account(request, db)
    if account is None:
        return _redirect_to_login()
    require_valid_csrf(csrf_token, account)
    event = _latest_undoable_dashboard_event(db, account)
    if event is None:
        return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    if event.event_type == "application_logged":
        _undo_application_logged_batch(db, account, event)
    else:
        _undo_phase_action(db, account, event)
    return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)


def _latest_undoable_dashboard_event(db: Session, account: Account) -> EpisodeEvent | None:
    events = list(db.execute(
        select(EpisodeEvent)
        .join(EczemaEpisode, EczemaEpisode.id == EpisodeEvent.episode_id)
        .where(
            EczemaEpisode.account_id == account.id,
            EpisodeEvent.event_type.in_(("application_logged", "healed_marked", "relapse_marked")),
        )
        .order_by(EpisodeEvent.occurred_at.desc(), EpisodeEvent.id.desc())
        .limit(100)
    ).scalars())
    for event in events:
        if not _event_was_created_from_dashboard_or_telegram(db, account, event):
            continue
        if event.event_type == "application_logged":
            application = _application_from_logged_event(db, account, event)
            if application is not None and not application.is_deleted and not application.is_voided:
                return event
        elif _phase_action_can_be_undone(db, account, event):
            return event
    return None


def _event_was_created_from_dashboard_or_telegram(db: Session, account: Account, event: EpisodeEvent) -> bool:
    if event.actor_type == "user" and event.actor_id == f"user:{account.id}":
        return True
    if event.actor_type != "agent" or not event.actor_id.startswith("api-key:"):
        return False
    try:
        api_key_id = int(event.actor_id.removeprefix("api-key:"))
    except ValueError:
        return False
    api_key = db.get(AccountApiKey, api_key_id)
    return bool(api_key and api_key.account_id == account.id and api_key.name == "telegram-dashboard-bot")


def _application_from_logged_event(db: Session, account: Account, event: EpisodeEvent) -> TreatmentApplication | None:
    application_id = event.payload.get("application_id") if isinstance(event.payload, dict) else None
    if not isinstance(application_id, int):
        return None
    application = db.get(TreatmentApplication, application_id)
    if application is None:
        return None
    episode = db.get(EczemaEpisode, application.episode_id)
    if episode is None or episode.account_id != account.id:
        return None
    return application


def _phase_action_can_be_undone(db: Session, account: Account, event: EpisodeEvent) -> bool:
    episode = db.get(EczemaEpisode, event.episode_id)
    if episode is None or episode.account_id != account.id:
        return False
    current_history = _phase_history_for_event(db, event)
    if current_history is None:
        return False
    return (
        db.execute(
            select(EpisodePhaseHistory.id)
            .where(EpisodePhaseHistory.episode_id == episode.id, EpisodePhaseHistory.id < current_history.id)
            .order_by(EpisodePhaseHistory.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )


def _phase_history_for_event(db: Session, event: EpisodeEvent) -> EpisodePhaseHistory | None:
    return db.execute(
        select(EpisodePhaseHistory)
        .where(EpisodePhaseHistory.episode_id == event.episode_id, EpisodePhaseHistory.started_at == event.occurred_at)
        .order_by(EpisodePhaseHistory.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _undo_application_logged_batch(db: Session, account: Account, event: EpisodeEvent) -> None:
    events = list(
        db.execute(
            select(EpisodeEvent)
            .join(EczemaEpisode, EczemaEpisode.id == EpisodeEvent.episode_id)
            .where(
                EczemaEpisode.account_id == account.id,
                EpisodeEvent.actor_type == event.actor_type,
                EpisodeEvent.actor_id == event.actor_id,
                EpisodeEvent.event_type == "application_logged",
                EpisodeEvent.occurred_at == event.occurred_at,
            )
            .order_by(EpisodeEvent.id.asc())
        ).scalars()
    )
    deleted_at = utc_now()
    for logged_event in events:
        application = _application_from_logged_event(db, account, logged_event)
        if application is not None and not application.is_deleted and not application.is_voided:
            delete_application(db, account, application.id, deleted_at, "user", f"user:{account.id}")


def _undo_phase_action(db: Session, account: Account, event: EpisodeEvent) -> None:
    episode = db.get(EczemaEpisode, event.episode_id)
    if episode is None or episode.account_id != account.id:
        return
    current_history = _phase_history_for_event(db, event)
    if current_history is None:
        return
    previous_history = db.execute(
        select(EpisodePhaseHistory)
        .where(EpisodePhaseHistory.episode_id == episode.id, EpisodePhaseHistory.id < current_history.id)
        .order_by(EpisodePhaseHistory.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if previous_history is None:
        return

    previous_history.ended_at = None
    episode.current_phase_number = previous_history.phase_number
    episode.phase_started_at = previous_history.started_at
    episode.phase_due_end_at = calculate_phase_due_end_at(previous_history.started_at, previous_history.phase_number)
    episode.updated_at = utc_now()
    if event.event_type == "healed_marked":
        episode.status = "active_flare"
        episode.healed_at = None
    else:
        episode.status = "in_taper"
        phase_two = db.execute(
            select(EpisodePhaseHistory)
            .where(EpisodePhaseHistory.episode_id == episode.id, EpisodePhaseHistory.phase_number == 2, EpisodePhaseHistory.id != current_history.id)
            .order_by(EpisodePhaseHistory.started_at.asc(), EpisodePhaseHistory.id.asc())
            .limit(1)
        ).scalar_one_or_none()
        episode.healed_at = phase_two.started_at if phase_two is not None else previous_history.started_at
    db.add(previous_history)
    db.add(episode)
    db.delete(current_history)
    for related in db.execute(
        select(EpisodeEvent).where(
            EpisodeEvent.episode_id == episode.id,
            EpisodeEvent.occurred_at == event.occurred_at,
            EpisodeEvent.event_type.in_((event.event_type, "phase_entered")),
        )
    ).scalars():
        db.delete(related)
    db.commit()



@router.post("/subjects/{subject_id}")
def dashboard_update_subject(
    subject_id: int,
    request: Request,
    display_name: str = Form(...),
    csrf_token: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    account = load_dashboard_account(request, db)
    if account is None:
        return _redirect_to_login()
    require_valid_csrf(csrf_token, account)
    subject = get_subject(db, account, subject_id)
    cleaned = _clean(display_name)
    if cleaned is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="display name required")
    subject.display_name = cleaned
    db.add(subject)
    db.commit()
    return RedirectResponse("/dashboard?tab=settings&settings_tab=subject", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/locations")
async def dashboard_create_location(
    request: Request,
    display_name: str = Form(...),
    csrf_token: str | None = Form(default=None),
    image: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
):
    account = load_dashboard_account(request, db)
    if account is None:
        return _redirect_to_login()
    require_valid_csrf(csrf_token, account)
    cleaned_name = _clean(display_name)
    if cleaned_name is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="display name required")
    cleaned_code = _unique_location_code(db, account, cleaned_name)
    location = create_location(db, account, cleaned_code, cleaned_name)
    subject = _default_subject(db, account)
    create_episode(db, account, subject.id, location.id, "v1", utc_now(), "user", f"user:{account.id}")
    if image is not None and image.filename:
        await store_location_image(db, account, location.id, image)
    return RedirectResponse("/dashboard?tab=settings&settings_tab=add-location&created_location=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/locations/{location_id}")
def dashboard_update_location(
    location_id: int,
    request: Request,
    display_name: str = Form(...),
    csrf_token: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    account = load_dashboard_account(request, db)
    if account is None:
        return _redirect_to_login()
    require_valid_csrf(csrf_token, account)
    location = get_location(db, account, location_id)
    cleaned = _clean(display_name)
    if cleaned is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="display name required")
    location.display_name = cleaned
    db.add(location)
    db.commit()
    return RedirectResponse("/dashboard?tab=settings&settings_tab=edit-locations", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/locations/{location_id}/image")
async def dashboard_update_location_image(
    location_id: int,
    request: Request,
    csrf_token: str | None = Form(default=None),
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    account = load_dashboard_account(request, db)
    if account is None:
        return _redirect_to_login()
    require_valid_csrf(csrf_token, account)
    await store_location_image(db, account, location_id, image)
    return RedirectResponse("/dashboard?tab=settings&settings_tab=edit-locations", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/locations/{location_id}/delete")
def dashboard_delete_location(
    location_id: int,
    request: Request,
    csrf_token: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    account = load_dashboard_account(request, db)
    if account is None:
        return _redirect_to_login()
    require_valid_csrf(csrf_token, account)
    delete_location(db, account, location_id)
    return RedirectResponse("/dashboard?tab=settings&settings_tab=edit-locations", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/locations/{location_id}/image")
def dashboard_location_image(location_id: int, request: Request, db: Session = Depends(get_db)):
    account = _require_account(request, db)
    stored = get_location_image_file(db, account, location_id)
    return FileResponse(stored.path, media_type=stored.mime_type)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _default_subject(db: Session, account: Account) -> Subject:
    subject = db.execute(select(Subject).where(Subject.account_id == account.id).order_by(Subject.id.asc()).limit(1)).scalar_one_or_none()
    if subject is not None:
        return subject
    return create_subject(db, account, "Default subject")


def _slugify_location_code(display_name: str) -> str:
    replacements = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "ae", "Ö": "oe", "Ü": "ue"})
    value = display_name.translate(replacements)
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return value or "location"


def _unique_location_code(db: Session, account: Account, display_name: str) -> str:
    base = _slugify_location_code(display_name)
    existing = set(
        db.execute(select(BodyLocation.code).where(BodyLocation.account_id == account.id, BodyLocation.code.like(f"{base}%"))).scalars()
    )
    if base not in existing:
        return base
    suffix = 2
    while f"{base}_{suffix}" in existing:
        suffix += 1
    return f"{base}_{suffix}"

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.time import utc_now
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
from app.location_images import get_location_image_file
from app.models import Account
from app.services import authenticate_user, issue_login_token, log_application


router = APIRouter(prefix="/dashboard", tags=["dashboard"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
ASSET_DIR = Path(__file__).parent / "static"


def _format_datetime(value) -> str:
    if value is None:
        return "Not yet"
    return value.strftime("%b %-d, %H:%M")


def _format_date(value) -> str:
    if value is None:
        return ""
    return value.strftime("%b %-d")


templates.env.filters["zdt"] = _format_datetime
templates.env.filters["zdate"] = _format_date


def _redirect_to_login() -> RedirectResponse:
    return RedirectResponse("/dashboard/login", status_code=status.HTTP_303_SEE_OTHER)


def _require_account(request: Request, db: Session) -> Account:
    account = load_dashboard_account(request, db)
    if account is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="dashboard login required")
    return account


def _html(request: Request, template: str, context: dict, status_code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(request, template, context, status_code=status_code)


@router.get("", response_class=HTMLResponse)
def dashboard_home(request: Request, db: Session = Depends(get_db)):
    account = load_dashboard_account(request, db)
    if account is None:
        return _redirect_to_login()
    overview = build_dashboard_overview(db, account)
    return _html(
        request,
        "dashboard.html",
        {
            "account": account,
            "overview": overview,
            "csrf_token": issue_csrf_token(account),
        },
    )


@router.get("/", response_class=HTMLResponse)
def dashboard_home_slash(request: Request, db: Session = Depends(get_db)):
    return dashboard_home(request, db)


@router.get("/login", response_class=HTMLResponse)
def dashboard_login_form(request: Request):
    csrf_token = issue_login_csrf_token()
    response = _html(request, "login.html", {"error": None, "csrf_token": csrf_token})
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
    if filename != "dashboard.css":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")
    return FileResponse(ASSET_DIR / filename, media_type="text/css")


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
            {"error": "This form expired. Reload and try again.", "csrf_token": fresh_token},
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
        return _html(request, "login.html", {"error": "Invalid username or password.", "csrf_token": csrf_token}, status_code=status.HTTP_401_UNAUTHORIZED)
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
        overview = build_dashboard_overview(db, account)
        return _html(
            request,
            "dashboard.html",
            {
                "account": account,
                "overview": overview,
                "csrf_token": issue_csrf_token(account),
                "error": "This form expired. Reload and try again.",
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

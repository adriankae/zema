from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.database import get_db
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
from app.location_images import get_location_image_file, store_location_image
from app.models import Account
from app.services import authenticate_user, create_location, get_location, get_subject, issue_login_token, log_application


router = APIRouter(prefix="/dashboard", tags=["dashboard"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
ASSET_DIR = Path(__file__).parent / "static"
THEME_COOKIE = "zema_theme"
THEME_PATH = SESSION_PATH



def _format_datetime(value) -> str:
    if value is None:
        return "Not yet"
    return to_local(value).strftime("%b %-d, %H:%M")


def _format_date(value) -> str:
    if value is None:
        return ""
    return value.strftime("%b %-d")


templates.env.filters["zdt"] = _format_datetime
templates.env.filters["zdate"] = _format_date



def _dashboard_theme(request: Request) -> str:
    return "light" if request.cookies.get(THEME_COOKIE) == "light" else "dark"


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
    overview = _overview_from_request(db, account, request)
    return _html(
        request,
        "dashboard.html",
        {
            "account": account,
            "overview": overview,
            "csrf_token": issue_csrf_token(account),
            "theme": _dashboard_theme(request),
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
    return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/locations")
async def dashboard_create_location(
    request: Request,
    code: str = Form(...),
    display_name: str = Form(...),
    csrf_token: str | None = Form(default=None),
    image: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
):
    account = load_dashboard_account(request, db)
    if account is None:
        return _redirect_to_login()
    require_valid_csrf(csrf_token, account)
    cleaned_code = _clean(code)
    cleaned_name = _clean(display_name)
    if cleaned_code is None or cleaned_name is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="location code and display name required")
    location = create_location(db, account, cleaned_code, cleaned_name)
    if image is not None and image.filename:
        await store_location_image(db, account, location.id, image)
    return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)


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
    return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)


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

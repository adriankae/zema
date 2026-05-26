from __future__ import annotations

from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe

from fastapi import HTTPException, Request, status
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import decode_access_token
from app.models import Account


SESSION_COOKIE = "zema_session"
LOGIN_CSRF_COOKIE = "zema_login_csrf"
SESSION_PATH = "/dashboard"
CSRF_TTL_MINUTES = 60 * 8


def dashboard_cookie_secure() -> bool:
    return settings.app_env not in {"local", "dev", "test"}


def load_dashboard_account(request: Request, db: Session) -> Account | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        payload = decode_access_token(token)
    except JWTError:
        return None
    account_id = payload.get("account_id")
    if not isinstance(account_id, int):
        return None
    account = db.get(Account, account_id)
    if account is None or not account.is_active:
        return None
    return account


def issue_csrf_token(account: Account) -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=CSRF_TTL_MINUTES)
    payload = {"purpose": "dashboard-csrf", "account_id": account.id, "exp": expires}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def issue_login_csrf_token() -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=CSRF_TTL_MINUTES)
    payload = {"purpose": "dashboard-login-csrf", "nonce": token_urlsafe(24), "exp": expires}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def require_valid_login_csrf(form_token: str | None, cookie_token: str | None) -> None:
    if not form_token or not cookie_token or form_token != cookie_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid CSRF token")
    try:
        payload = jwt.decode(form_token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid CSRF token") from exc
    if payload.get("purpose") != "dashboard-login-csrf":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid CSRF token")


def require_valid_csrf(token: str | None, account: Account) -> None:
    if not token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid CSRF token")
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid CSRF token") from exc
    if payload.get("purpose") != "dashboard-csrf" or payload.get("account_id") != account.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid CSRF token")

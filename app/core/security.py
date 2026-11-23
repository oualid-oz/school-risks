from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from pwdlib import PasswordHash
from sqlmodel import Session, select

from app import db
from app.core.config import settings
from app.db import get_session
from app.models.security import AuditLog, User, UserCollegeLink, UserRole

logger = logging.getLogger(__name__)
password_hash = PasswordHash.recommended()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_prefix}/auth/token")
_DUMMY_PASSWORD_HASH = password_hash.hash("not-a-real-account-password")


def validate_security_settings() -> None:
    if len(settings.jwt_secret_key.encode("utf-8")) < 32:
        raise RuntimeError("SRT_JWT_SECRET_KEY must contain at least 32 bytes.")
    if (settings.bootstrap_admin_username is None) != (
        settings.bootstrap_admin_password is None
    ):
        raise RuntimeError(
            "Set both SRT_BOOTSTRAP_ADMIN_USERNAME and SRT_BOOTSTRAP_ADMIN_PASSWORD."
        )
    if settings.bootstrap_admin_password and len(settings.bootstrap_admin_password) < 12:
        raise RuntimeError("The bootstrap admin password must contain at least 12 characters.")


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return password_hash.verify(password, hashed_password)


def _password_fingerprint(user: User) -> str:
    # Changes whenever the password hash changes, so a password reset revokes
    # every token issued before it without needing a token table.
    return hashlib.sha256(user.hashed_password.encode("utf-8")).hexdigest()[:16]


def create_access_token(user: User) -> str:
    expires_at = datetime.now(UTC) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    return jwt.encode(
        {"sub": str(user.id), "exp": expires_at, "pwd": _password_fingerprint(user)},
        settings.jwt_secret_key,
        algorithm="HS256",
    )


def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: Session = Depends(get_session),
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Session invalide ou expirée",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=["HS256"],
            options={"require": ["exp", "sub", "pwd"]},
        )
        user_id = int(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
        raise unauthorized from None

    user = session.get(User, user_id)
    if (
        user is None
        or not user.active
        or payload["pwd"] != _password_fingerprint(user)
    ):
        raise unauthorized
    return user


def require_roles(*roles: UserRole):
    def check_role(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Droits insuffisants pour cette action")
        return user

    return check_role


def assigned_schools(session: Session, user: User) -> set[str]:
    return set(
        session.exec(
            select(UserCollegeLink.uai).where(UserCollegeLink.user_id == user.id)
        ).all()
    )


def can_manage_school(session: Session, user: User, uai: str) -> bool:
    return user.role == UserRole.admin or (
        user.role == UserRole.referent and uai in assigned_schools(session, user)
    )


def require_school_access(session: Session, user: User, uai: str) -> None:
    if not can_manage_school(session, user, uai):
        raise HTTPException(status_code=403, detail="Pas d’accès aux actions de ce collège")


def record_audit(
    session: Session,
    request: Request,
    actor: User | None,
    event: str,
    resource_type: str,
    resource_id: str | int,
    details: dict[str, Any] | None = None,
    actor_username: str | None = None,
) -> None:
    session.add(
        AuditLog(
            actor_user_id=actor.id if actor else None,
            actor_username=actor.username if actor else (actor_username or "-"),
            event=event,
            resource_type=resource_type,
            resource_id=str(resource_id),
            details=json.dumps(details, ensure_ascii=False, sort_keys=True)
            if details is not None
            else None,
            remote_addr=request.client.host if request.client else None,
        )
    )


def bootstrap_admin() -> None:
    username = settings.bootstrap_admin_username
    password = settings.bootstrap_admin_password
    if username is None or password is None:
        logger.warning(
            "No bootstrap admin configured; set SRT_BOOTSTRAP_ADMIN_USERNAME and "
            "SRT_BOOTSTRAP_ADMIN_PASSWORD to create the first administrator."
        )
        return

    # db.engine, looked up now: tests and tools swap the engine after import.
    with Session(db.engine) as session:
        existing = session.exec(select(User).where(User.username == username)).first()
        if existing:
            return
        session.add(
            User(
                username=username,
                hashed_password=hash_password(password),
                role=UserRole.admin,
            )
        )
        session.commit()
        logger.info("Created the configured bootstrap administrator.")


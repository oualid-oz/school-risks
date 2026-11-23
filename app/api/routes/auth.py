from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.config import settings
from app.core.security import (
    _DUMMY_PASSWORD_HASH,
    assigned_schools,
    create_access_token,
    get_current_user,
    hash_password,
    password_hash,
    record_audit,
    require_roles,
)
from app.data.store import DataStore, get_store
from app.db import get_session
from app.models.security import (
    AuditLog,
    AuditLogRead,
    TokenRead,
    User,
    UserCollegeLink,
    UserCreate,
    UserRead,
    UserRole,
    UserUpdate,
)

router = APIRouter(prefix="/auth", tags=["auth"])
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.rate_limit_default],
    storage_uri=settings.rate_limit_storage_uri,
)


def _user_read(session: Session, user: User) -> UserRead:
    return UserRead(
        id=user.id,
        username=user.username,
        role=user.role,
        active=user.active,
        school_uais=sorted(assigned_schools(session, user)),
        created_at=user.created_at,
    )


def _validate_school_assignments(
    store: DataStore, role: UserRole, school_uais: list[str]
) -> None:
    if role != UserRole.referent and school_uais:
        raise HTTPException(
            status_code=422, detail="Only referents can be assigned to schools"
        )
    unknown = sorted(uai for uai in set(school_uais) if not store.exists(uai))
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown college UAI(s): {unknown}")


@router.post("/token", response_model=TokenRead)
@limiter.limit(settings.rate_limit_login)
def issue_token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_session),
):
    user = session.exec(select(User).where(User.username == form_data.username)).first()
    stored_hash = user.hashed_password if user else _DUMMY_PASSWORD_HASH
    valid_password = password_hash.verify(form_data.password, stored_hash)
    if user is None or not user.active or not valid_password:
        record_audit(
            session,
            request,
            user,
            "auth.login_failed",
            "user",
            user.id if user else "-",
            {"username": form_data.username[:100]},
            actor_username=form_data.username[:100],
        )
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    record_audit(session, request, user, "auth.login_succeeded", "user", user.id)
    session.commit()
    return TokenRead(
        access_token=create_access_token(user),
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.get("/me", response_model=UserRead)
@limiter.limit(settings.rate_limit_default)
def read_current_user(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return _user_read(session, user)


@router.get("/users", response_model=list[UserRead])
@limiter.limit(settings.rate_limit_default)
def list_users(
    request: Request,
    session: Session = Depends(get_session),
    _admin: User = Depends(require_roles(UserRole.admin)),
):
    users = session.exec(select(User).order_by(User.username)).all()
    return [_user_read(session, user) for user in users]


@router.post("/users", response_model=UserRead, status_code=201)
@limiter.limit(settings.rate_limit_default)
def create_user(
    payload: UserCreate,
    request: Request,
    session: Session = Depends(get_session),
    store: DataStore = Depends(get_store),
    admin: User = Depends(require_roles(UserRole.admin)),
):
    if session.exec(select(User).where(User.username == payload.username)).first():
        raise HTTPException(status_code=409, detail="Username already exists")
    _validate_school_assignments(store, payload.role, payload.school_uais)
    user = User(
        username=payload.username,
        hashed_password=hash_password(payload.password),
        role=payload.role,
    )
    session.add(user)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Username already exists") from None
    for uai in sorted(set(payload.school_uais)):
        session.add(UserCollegeLink(user_id=user.id, uai=uai))
    record_audit(
        session,
        request,
        admin,
        "user.created",
        "user",
        user.id,
        {"role": user.role.value, "school_uais": sorted(set(payload.school_uais))},
    )
    session.commit()
    session.refresh(user)
    return _user_read(session, user)


@router.patch("/users/{user_id}", response_model=UserRead)
@limiter.limit(settings.rate_limit_default)
def update_user(
    user_id: int,
    payload: UserUpdate,
    request: Request,
    session: Session = Depends(get_session),
    store: DataStore = Depends(get_store),
    admin: User = Depends(require_roles(UserRole.admin)),
):
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    changes = payload.model_dump(exclude_unset=True)
    new_role = changes.get("role", user.role)
    new_active = changes.get("active", user.active)
    if user.id == admin.id and (new_role != UserRole.admin or not new_active):
        raise HTTPException(status_code=422, detail="You cannot disable or demote yourself")
    if user.role == UserRole.admin and user.active and (
        new_role != UserRole.admin or not new_active
    ):
        admins = session.exec(
            select(User).where(User.role == UserRole.admin, User.active.is_(True))
        ).all()
        if len(admins) <= 1:
            raise HTTPException(status_code=422, detail="Cannot disable the last administrator")

    if "school_uais" in changes:
        school_uais = changes["school_uais"] or []
        _validate_school_assignments(store, new_role, school_uais)
        for link in session.exec(
            select(UserCollegeLink).where(UserCollegeLink.user_id == user.id)
        ).all():
            session.delete(link)
        for uai in sorted(set(school_uais)):
            session.add(UserCollegeLink(user_id=user.id, uai=uai))
    elif "role" in changes and new_role != UserRole.referent:
        for link in session.exec(
            select(UserCollegeLink).where(UserCollegeLink.user_id == user.id)
        ).all():
            session.delete(link)
    if "password" in changes and changes["password"]:
        user.hashed_password = hash_password(changes["password"])
    if "role" in changes:
        user.role = new_role
    if "active" in changes:
        user.active = new_active
    record_audit(
        session,
        request,
        admin,
        "user.updated",
        "user",
        user.id,
        {"updated_fields": sorted(changes)},
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return _user_read(session, user)


@router.get("/audit", response_model=list[AuditLogRead])
@limiter.limit(settings.rate_limit_default)
def list_audit_log(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    session: Session = Depends(get_session),
    _admin: User = Depends(require_roles(UserRole.admin)),
):
    return session.exec(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)).all()

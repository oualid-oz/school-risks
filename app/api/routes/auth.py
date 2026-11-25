from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, or_, select

from app.core.config import parse_departements, settings
from app.core.scope import Scope, can_administer, get_scope, user_area, user_reach
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
    MyAccountUpdate,
    PasswordChange,
    TokenRead,
    User,
    UserCollegeLink,
    UserCreate,
    UserDepartementLink,
    UserRead,
    UserRole,
    UserUpdate,
)

router = APIRouter(prefix="/auth", tags=["auth"])
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.rate_limit_default],
    storage_uri=settings.rate_limit_storage_uri,
    # If Redis is unreachable, count in memory instead of failing every request (without
    # this, a Redis outage answers 500 to everything); back to Redis when it returns.
    in_memory_fallback_enabled=True,
)


def _user_read(session: Session, user: User) -> UserRead:
    return UserRead(
        id=user.id,
        username=user.username,
        role=user.role,
        active=user.active,
        school_uais=sorted(assigned_schools(session, user)),
        departements=sorted(user_area(session, user)),
        email=user.email,
        alertes_email=user.alertes_email,
        created_at=user.created_at,
    )


def _check_alert_settings(role: UserRole, email: str | None, alertes_email: bool) -> None:
    if not alertes_email:
        return
    if role == UserRole.lecteur:
        raise HTTPException(
            status_code=422,
            detail="Les alertes e-mail sont réservées aux administrateurs et aux référents",
        )
    if not email:
        raise HTTPException(status_code=422, detail="Renseignez une adresse e-mail pour recevoir les alertes")


def _validate_school_assignments(
    store: DataStore, role: UserRole, school_uais: list[str]
) -> None:
    if role != UserRole.referent and school_uais:
        raise HTTPException(
            status_code=422, detail="Seuls les référents peuvent être rattachés à des collèges"
        )
    unknown = sorted(uai for uai in set(school_uais) if not store.exists(uai))
    if unknown:
        raise HTTPException(status_code=422, detail=f"UAI de collège inconnu(s) : {', '.join(unknown)}")


def _validate_area(store: DataStore, role: UserRole, departements: list[str]) -> list[str]:
    """Department codes of an admin's or reader's area (empty: all of France)."""
    codes = sorted(set(parse_departements(departements)))
    if role == UserRole.referent and codes:
        raise HTTPException(
            status_code=422,
            detail="Un référent n’a pas de périmètre : son périmètre est celui de ses collèges",
        )
    known = {t["code"] for t in store.territories()}
    unknown = [c for c in codes if c not in known]
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"Département(s) sans collège suivi : {', '.join(unknown)}"
        )
    return codes


def _set_area(session: Session, user: User, codes: list[str]) -> None:
    for link in session.exec(select(UserDepartementLink).where(UserDepartementLink.user_id == user.id)).all():
        session.delete(link)
    for code in codes:
        session.add(UserDepartementLink(user_id=user.id, departement=code))


def _reach_of(
    store: DataStore, role: UserRole, school_uais: list[str], area: list[str]
) -> frozenset[str] | None:
    """The departments a user with these settings would reach (None: all of France)."""
    if role == UserRole.referent:
        colleges = (store.get_college(u) for u in school_uais)
        return frozenset(c["departement"] for c in colleges if c and c.get("departement"))
    return frozenset(area) if area else None


def _require_administrable(scope: Scope, reach: frozenset[str] | None) -> None:
    if not can_administer(scope, reach):
        raise HTTPException(
            status_code=403,
            detail="Hors de votre périmètre : seul un administrateur national peut gérer ce compte",
        )


def _national_admins(session: Session) -> list[User]:
    admins = session.exec(select(User).where(User.role == UserRole.admin, User.active.is_(True))).all()
    return [a for a in admins if not user_area(session, a)]


@router.post("/token", response_model=TokenRead)
@limiter.limit(settings.rate_limit_login)
def issue_token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_session),
):
    user = session.exec(select(User).where(User.username == form_data.username)).first()
    stored_hash = user.hashed_password if user else _DUMMY_PASSWORD_HASH
    session.close()  # the password check is slow on purpose: not while holding a connection
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
            detail="Identifiant ou mot de passe incorrect",
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
    account = _user_read(session, user)
    session.close()  # free the connection before the response is serialised and sent
    return account


@router.post("/me/password", response_model=TokenRead)
@limiter.limit(settings.rate_limit_login)  # it checks a password: same limit as the login
def change_own_password(
    payload: PasswordChange,
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Change your own password. Every token issued before is revoked (the password hash is
    part of the token), so a new one is returned to stay signed in on this device."""
    if not password_hash.verify(payload.current_password, user.hashed_password):
        record_audit(session, request, user, "user.password_change_failed", "user", user.id)
        session.commit()
        raise HTTPException(status_code=400, detail="Mot de passe actuel incorrect")
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=422, detail="Le nouveau mot de passe doit être différent de l’actuel")
    user.hashed_password = hash_password(payload.new_password)
    record_audit(session, request, user, "user.password_changed", "user", user.id)
    session.add(user)
    session.commit()
    session.refresh(user)
    return TokenRead(
        access_token=create_access_token(user),
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.patch("/me", response_model=UserRead)
@limiter.limit(settings.rate_limit_default)
def update_current_user(
    payload: MyAccountUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Users manage their own email address and weekly-digest opt-in."""
    changes = payload.model_dump(exclude_unset=True)
    email = changes.get("email", user.email)
    requested = changes.get("alertes_email")
    alertes = user.alertes_email if requested is None else requested
    if email is None and requested is None:
        alertes = False  # address removed: alerts stop (asking for alerts without one is refused)
    _check_alert_settings(user.role, email, alertes)
    user.email, user.alertes_email = email, alertes
    record_audit(
        session, request, user, "user.self_updated", "user", user.id,
        {"updated_fields": sorted(changes), "alertes_email": alertes},
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return _user_read(session, user)


@router.get("/users", response_model=list[UserRead])
@limiter.limit(settings.rate_limit_default)
def list_users(
    request: Request,
    limit: int | None = Query(None, ge=1, le=1000, description="Page size (default: everything)"),
    offset: int = Query(0, ge=0),
    response: Response = None,
    session: Session = Depends(get_session),
    store: DataStore = Depends(get_store),
    _admin: User = Depends(require_roles(UserRole.admin)),
    scope: Scope = Depends(get_scope),
):
    """The accounts this admin manages (area admins: those inside their area, and themselves).
    The total before paging is in the `X-Total-Count` header."""
    users = [
        u for u in session.exec(select(User).order_by(User.username)).all()
        if u.id == scope.user.id or can_administer(scope, user_reach(session, store, u))
    ]
    response.headers["X-Total-Count"] = str(len(users))
    page = users[offset:offset + limit] if limit else users[offset:]
    return [_user_read(session, user) for user in page]


@router.post("/users", response_model=UserRead, status_code=201)
@limiter.limit(settings.rate_limit_default)
def create_user(
    payload: UserCreate,
    request: Request,
    session: Session = Depends(get_session),
    store: DataStore = Depends(get_store),
    admin: User = Depends(require_roles(UserRole.admin)),
    scope: Scope = Depends(get_scope),
):
    if session.exec(select(User).where(User.username == payload.username)).first():
        raise HTTPException(status_code=409, detail="Cet identifiant existe déjà")
    _validate_school_assignments(store, payload.role, payload.school_uais)
    area = _validate_area(store, payload.role, payload.departements)
    _require_administrable(scope, _reach_of(store, payload.role, payload.school_uais, area))
    user = User(
        username=payload.username,
        hashed_password=hash_password(payload.password),
        role=payload.role,
        email=payload.email,
        alertes_email=payload.alertes_email,
    )
    _check_alert_settings(user.role, user.email, user.alertes_email)
    session.add(user)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Cet identifiant existe déjà") from None
    for uai in sorted(set(payload.school_uais)):
        session.add(UserCollegeLink(user_id=user.id, uai=uai))
    _set_area(session, user, area)
    record_audit(
        session,
        request,
        admin,
        "user.created",
        "user",
        user.id,
        {"role": user.role.value, "school_uais": sorted(set(payload.school_uais)), "departements": area},
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
    scope: Scope = Depends(get_scope),
):
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")
    if user.id != admin.id:
        _require_administrable(scope, user_reach(session, store, user))

    changes = payload.model_dump(exclude_unset=True)
    new_role = changes.get("role", user.role)
    new_active = changes.get("active", user.active)
    if user.id == admin.id and (new_role != UserRole.admin or not new_active):
        raise HTTPException(
            status_code=422,
            detail="Vous ne pouvez pas désactiver votre propre compte ni retirer votre rôle d’administrateur",
        )
    current_area = sorted(user_area(session, user))
    new_area = current_area
    if "departements" in changes or new_role == UserRole.referent:
        new_area = _validate_area(store, new_role, changes.get("departements") or [])
    if user.id == admin.id and new_area != current_area:
        raise HTTPException(status_code=422, detail="Vous ne pouvez pas modifier votre propre périmètre")
    if "school_uais" in changes:
        new_schools = changes["school_uais"] or []
    else:
        new_schools = sorted(assigned_schools(session, user))
    if user.id != admin.id:
        _require_administrable(scope, _reach_of(store, new_role, new_schools, new_area))
    was_national_admin = user.role == UserRole.admin and user.active and not current_area
    stays_national_admin = new_role == UserRole.admin and new_active and not new_area
    if was_national_admin and not stays_national_admin and len(_national_admins(session)) <= 1:
        raise HTTPException(
            status_code=422,
            detail="Impossible de désactiver, de rétrograder ou de limiter "
            "le dernier administrateur national actif",
        )

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
    if new_area != current_area:
        _set_area(session, user, new_area)
    if "password" in changes and changes["password"]:
        user.hashed_password = hash_password(changes["password"])
    if "role" in changes:
        user.role = new_role
    if "active" in changes:
        user.active = new_active
    if "email" in changes:
        user.email = changes["email"]
    if "alertes_email" in changes and changes["alertes_email"] is not None:
        user.alertes_email = changes["alertes_email"]
    if user.role == UserRole.lecteur:
        user.alertes_email = False  # readers get no digest
    _check_alert_settings(user.role, user.email, user.alertes_email)
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
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
    store: DataStore = Depends(get_store),
    _admin: User = Depends(require_roles(UserRole.admin)),
    scope: Scope = Depends(get_scope),
):
    """Newest first. Area admins see the events about their area's schools, and those by or
    about the accounts they manage."""
    stmt = select(AuditLog)
    if not scope.national:
        managed = [
            u.id for u in session.exec(select(User)).all()
            if u.id == scope.user.id or can_administer(scope, user_reach(session, store, u))
        ]
        stmt = stmt.where(or_(
            AuditLog.departement.in_(scope.departements),
            AuditLog.actor_user_id.in_(managed),
            (AuditLog.resource_type == "user") & AuditLog.resource_id.in_([str(i) for i in managed]),
        ))
    return session.exec(stmt.order_by(AuditLog.id.desc()).offset(offset).limit(limit)).all()

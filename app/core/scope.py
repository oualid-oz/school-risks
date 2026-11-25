"""What a user may see and manage (Sprint 21).

- National administrator (no area): everything.
- Department or academy administrator (area = some departments): the schools of the area,
  their actions and PPMS, and the users whose own reach is inside the area.
- Referent: sees the departments of their schools, manages those schools.
- Reader: sees their area (or all of France), manages nothing.

Every route that returns or changes school data goes through a `Scope`: lists are filtered,
and a school outside it answers 404 (reading) or 403 (changing).
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException
from sqlmodel import Session, select

from app.core.security import assigned_schools, get_current_user
from app.data.store import DataStore, get_store
from app.db import get_session
from app.models.security import User, UserDepartementLink, UserRole


def user_area(session: Session, user: User) -> set[str]:
    """Departments of an administrator's or reader's area (empty: all of France)."""
    return set(session.exec(
        select(UserDepartementLink.departement).where(UserDepartementLink.user_id == user.id)
    ).all())


@dataclass(frozen=True)
class Scope:
    user: User
    departements: frozenset[str] | None  # visible departments; None: all of France
    managed: frozenset[str] | None  # schools whose actions/PPMS can change; None: all visible

    @property
    def national(self) -> bool:
        return self.departements is None

    def sees_departement(self, departement: str | None) -> bool:
        return self.departements is None or departement in self.departements

    def sees(self, store: DataStore, uai: str) -> bool:
        college = store.get_college(uai)
        return college is not None and self.sees_departement(college.get("departement"))

    def manages(self, store: DataStore, uai: str) -> bool:
        if self.user.role == UserRole.lecteur or not self.sees(store, uai):
            return False
        return self.managed is None or uai in self.managed

    def visible_uais(self, store: DataStore) -> list[str]:
        return [u for u in store.uais() if self.sees(store, u)]

    def require_visible(self, store: DataStore, uai: str) -> None:
        if not self.sees(store, uai):
            raise HTTPException(status_code=404, detail=f"Collège {uai} introuvable")

    def require_managed(self, store: DataStore, uai: str) -> None:
        if not self.manages(store, uai):
            raise HTTPException(status_code=403, detail="Pas d’accès aux actions de ce collège")

    def check_departement(self, departement: str | None) -> None:
        """A `departement` filter outside the area: 403 rather than an empty answer."""
        if departement is not None and not self.sees_departement(departement):
            raise HTTPException(status_code=403, detail=f"Département {departement} hors de votre périmètre")


def scope_for(session: Session, store: DataStore, user: User) -> Scope:
    if user.role == UserRole.referent:
        schools = frozenset(assigned_schools(session, user))
        departements = frozenset(
            c["departement"] for c in (store.get_college(u) for u in schools) if c and c.get("departement")
        )
        return Scope(user, departements, schools)
    area = user_area(session, user)
    return Scope(user, frozenset(area) if area else None, None)


def get_scope(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    store: DataStore = Depends(get_store),
) -> Scope:
    scope = scope_for(session, store, user)
    session.close()  # as in get_current_user: no connection held while waiting for a thread
    return scope


def user_reach(session: Session, store: DataStore, user: User) -> frozenset[str] | None:
    """The departments a user's rights extend over (None: all of France): used to decide
    which administrators may manage them."""
    return scope_for(session, store, user).departements


def can_administer(admin_scope: Scope, reach: frozenset[str] | None) -> bool:
    """A national admin manages everyone; an area admin, users whose reach is inside it
    (never national users, nor a referent without schools, whose reach is empty)."""
    if admin_scope.national:
        return True
    return reach is not None and bool(reach) and reach <= admin_scope.departements

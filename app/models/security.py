from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import Enum

import sqlalchemy as sa
from pydantic import field_validator
from sqlmodel import Field, SQLModel


class UserRole(str, Enum):
    admin = "admin"
    referent = "referent"
    lecteur = "lecteur"


class User(SQLModel, table=True):
    __tablename__ = "app_user"

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True, min_length=3, max_length=100)
    hashed_password: str
    role: UserRole = Field(default=UserRole.lecteur, index=True)
    active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # Weekly digest (Sprint 10): opt-in, for admins and referents.
    email: str | None = Field(default=None, max_length=254)
    alertes_email: bool = Field(default=False, sa_column_kwargs={"server_default": sa.false()})


class UserCollegeLink(SQLModel, table=True):
    __tablename__ = "user_college"

    user_id: int = Field(foreign_key="app_user.id", primary_key=True)
    uai: str = Field(primary_key=True, max_length=8, index=True)


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_log"

    id: int | None = Field(default=None, primary_key=True)
    actor_user_id: int | None = Field(default=None, foreign_key="app_user.id", index=True)
    actor_username: str = Field(max_length=100, index=True)
    event: str = Field(max_length=100, index=True)
    resource_type: str = Field(max_length=100)
    resource_id: str = Field(max_length=100)
    details: str | None = None
    remote_addr: str | None = Field(default=None, max_length=64)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)


# Deliberately simple: one "@", a dot in the domain, no spaces. The real check is the
# email reaching the inbox; this only catches typos.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _check_email(value: str | None) -> str | None:
    if value is None or value.strip() == "":
        return None
    value = value.strip()
    if len(value) > 254 or not _EMAIL.match(value):
        raise ValueError("adresse e-mail invalide")
    return value


class UserCreate(SQLModel):
    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=12, max_length=256)
    role: UserRole = UserRole.lecteur
    school_uais: list[str] = Field(default_factory=list)
    email: str | None = None
    alertes_email: bool = False

    _email = field_validator("email")(_check_email)


class UserUpdate(SQLModel):
    password: str | None = Field(default=None, min_length=12, max_length=256)
    role: UserRole | None = None
    active: bool | None = None
    school_uais: list[str] | None = None
    email: str | None = None
    alertes_email: bool | None = None

    _email = field_validator("email")(_check_email)


class MyAccountUpdate(SQLModel):
    """What users may change on their own account."""

    email: str | None = None
    alertes_email: bool | None = None

    _email = field_validator("email")(_check_email)


class UserRead(SQLModel):
    id: int
    username: str
    role: UserRole
    active: bool
    school_uais: list[str]
    email: str | None
    alertes_email: bool
    created_at: datetime


class TokenRead(SQLModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class AuditLogRead(SQLModel):
    id: int
    actor_user_id: int | None
    actor_username: str
    event: str
    resource_type: str
    resource_id: str
    details: str | None
    remote_addr: str | None
    created_at: datetime

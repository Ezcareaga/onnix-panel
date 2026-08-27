"""Pydantic schemas for user validation."""
from typing import Optional

from pydantic import BaseModel, field_validator

VALID_ROLES = {"admin", "user"}


class UserCreateForm(BaseModel):
    """Validates data for creating a new user."""

    email: str
    name: str
    password: str
    role: str = "user"

    @field_validator("email")
    @classmethod
    def email_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v or "@" not in v:
            raise ValueError("A valid email is required")
        return v

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name cannot be empty")
        return v

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v

    @field_validator("role")
    @classmethod
    def role_must_be_valid(cls, v: str) -> str:
        if v not in VALID_ROLES:
            raise ValueError(f"Invalid role '{v}'. Must be one of: {sorted(VALID_ROLES)}")
        return v


class UserUpdateForm(BaseModel):
    """Validates data for updating a user."""

    name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("role")
    @classmethod
    def role_must_be_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in VALID_ROLES:
            raise ValueError(f"Invalid role '{v}'. Must be one of: {sorted(VALID_ROLES)}")
        return v

"""Pydantic schemas for contact validation."""
from typing import Optional

from pydantic import BaseModel, field_validator

from app.constants import VALID_STATUSES_WITH_DELETED as VALID_STATUSES


class ContactCreateForm(BaseModel):
    """Validates data for creating a new contact."""

    name: str
    phone: str
    email: Optional[str] = None
    source: str = "manual"
    status: str = "new"
    preferences: Optional[dict] = None

    @field_validator("status")
    @classmethod
    def status_must_be_valid(cls, v: str) -> str:
        if v not in VALID_STATUSES:
            raise ValueError(f"Invalid status '{v}'. Must be one of: {sorted(VALID_STATUSES)}")
        return v

    @field_validator("phone")
    @classmethod
    def phone_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Phone number cannot be empty")
        return v

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name cannot be empty")
        return v


class ContactUpdateForm(BaseModel):
    """Validates data for updating a contact."""

    name: Optional[str] = None
    phone: Optional[str] = None
    phone_normalized: Optional[str] = None
    email: Optional[str] = None
    preferences: Optional[dict] = None


class ContactStatusForm(BaseModel):
    """Validates a status change request."""

    status: str

    @field_validator("status")
    @classmethod
    def status_must_be_valid(cls, v: str) -> str:
        if v not in VALID_STATUSES:
            raise ValueError(f"Invalid status '{v}'. Must be one of: {sorted(VALID_STATUSES)}")
        return v

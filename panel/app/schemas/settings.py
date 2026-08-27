"""Pydantic schemas for settings validation."""
from pydantic import BaseModel, field_validator


class SettingUpdateForm(BaseModel):
    """Validates a bot setting key-value update."""

    key: str
    value: str

    @field_validator("key")
    @classmethod
    def key_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Setting key cannot be empty")
        return v

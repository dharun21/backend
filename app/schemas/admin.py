from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SpecialistAccessUpdate(BaseModel):
    enabled: bool
    expires_at: datetime | None = None
    monthly_usage_limit: int | None = Field(default=None, ge=0)


class SpecialistAccessView(BaseModel):
    enabled: bool
    expires_at: datetime | None = None
    monthly_usage_limit: int | None = None


class AdminUserView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    email: str
    created_at: datetime
    is_admin: bool
    access: dict[str, SpecialistAccessView]

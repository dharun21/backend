from datetime import datetime, timezone

from fastapi import HTTPException, status

from app.models.specialist_access import SpecialistAccess

SPECIALISTS = {
    "finance_analyst",
    "personal_assistant",
    "hr_executive",
    "sales_analyst",
}


def grant_is_active(grant: SpecialistAccess | None) -> bool:
    if grant is None or not grant.enabled:
        return False
    if grant.expires_at is None:
        return True
    expires_at = grant.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at > datetime.now(timezone.utc)


def active_specialists(db, user_id: str) -> list[str]:
    grants = db.query(SpecialistAccess).filter(SpecialistAccess.user_id == user_id).all()
    return sorted(grant.specialist_key for grant in grants if grant_is_active(grant))


def require_specialist_access(db, user_id: str, specialist_key: str) -> None:
    grant = db.get(SpecialistAccess, (user_id, specialist_key))
    if not grant_is_active(grant):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This AI specialist is not enabled for your account.",
        )

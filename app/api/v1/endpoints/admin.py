from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.dependencies import CurrentAdmin, Database
from app.models.admin_account import AdminAccount
from app.models.specialist_access import SpecialistAccess
from app.models.user import User
from app.schemas.admin import AdminUserView, SpecialistAccessUpdate, SpecialistAccessView
from app.services.entitlements import SPECIALISTS
from app.services.notifications import create_notification

router = APIRouter(prefix="/admin", tags=["admin"])


def user_view(db: Database, user: User) -> AdminUserView:
    grants = db.scalars(
        select(SpecialistAccess).where(SpecialistAccess.user_id == user.id)
    )
    access = {
        key: SpecialistAccessView(
            enabled=bool(grant.enabled),
            expires_at=grant.expires_at,
            monthly_usage_limit=grant.monthly_usage_limit,
        )
        for grant in grants
        for key in [grant.specialist_key]
    }
    for key in SPECIALISTS:
        access.setdefault(key, SpecialistAccessView(enabled=False))
    is_admin = db.get(AdminAccount, user.id) is not None
    return AdminUserView(
        id=user.id,
        name=user.name,
        email=user.email,
        created_at=user.created_at,
        is_admin=is_admin,
        access=access,
    )


@router.get("/users", response_model=list[AdminUserView])
def list_users(_: CurrentAdmin, db: Database) -> list[AdminUserView]:
    users = db.scalars(select(User).order_by(User.created_at.desc())).all()
    return [user_view(db, user) for user in users]


@router.put("/users/{user_id}/access/{specialist_key}", response_model=AdminUserView)
def update_user_access(
    user_id: str,
    specialist_key: str,
    payload: SpecialistAccessUpdate,
    _: CurrentAdmin,
    db: Database,
) -> AdminUserView:
    if specialist_key not in SPECIALISTS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown AI specialist.")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    grant = db.get(SpecialistAccess, (user_id, specialist_key))
    if grant is None:
        grant = SpecialistAccess(user_id=user_id, specialist_key=specialist_key)
        db.add(grant)
    grant.enabled = payload.enabled
    grant.expires_at = payload.expires_at
    grant.monthly_usage_limit = payload.monthly_usage_limit
    grant.source = "manual"
    action = "enabled" if payload.enabled else "disabled"
    create_notification(
        db,
        user.id,
        "AI access updated",
        f"{specialist_key.replace('_', ' ').title()} access was {action} for your account.",
        "/workspace",
    )
    db.commit()
    db.refresh(user)
    return user_view(db, user)

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from sqlalchemy import select, update

from app.api.dependencies import CurrentUser, Database
from app.models.user import User
from app.models.admin_account import AdminAccount
from app.models.password_reset import PasswordReset
from app.services.email import send_password_reset_email, send_welcome_email
from app.services.notifications import create_notification
from app.services.entitlements import active_specialists
from app.schemas.auth import AuthResponse, ForgotPasswordRequest, LoginRequest, ProfileUpdate, RegisterRequest, ResetPasswordRequest, UserResponse
from app.services.security import create_access_token, get_jwt_secret, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["authentication"])
RESET_LIFETIME = timedelta(minutes=20)
RESET_COOLDOWN = timedelta(minutes=1)
MAX_RESET_CODE_ATTEMPTS = 5


def _code_hash(code: str) -> str:
    return hmac.new(get_jwt_secret().encode(), code.encode(), hashlib.sha256).hexdigest()


def _invalid_reset() -> HTTPException:
    return HTTPException(status_code=400, detail="This reset link or code is invalid or expired. Request a new one.")


def user_response(db: Database, user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        specialists=active_specialists(db, user.id),
        is_admin=db.get(AdminAccount, user.id) is not None,
    )

@router.patch("/me", response_model=UserResponse)
def update_current_user(payload: ProfileUpdate, user: CurrentUser, db: Database) -> UserResponse:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Name cannot be blank.")
    user.name = name
    db.commit()
    db.refresh(user)
    return user_response(db, user)


@router.get("/me", response_model=UserResponse)
def current_user(user: CurrentUser, db: Database) -> UserResponse:
    return user_response(db, user)


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, background_tasks: BackgroundTasks, db: Database) -> AuthResponse:
    email = str(payload.email).strip().lower()
    existing = db.scalar(select(User).where(User.email == email))
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists.")

    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Name cannot be blank.")
    user = User(name=name, email=email, password_hash=hash_password(payload.password))
    db.add(user)
    db.flush()
    create_notification(db, user.id, "Welcome to ZengenAI", "Your account is ready. Set up your workspace to get started.", "/workspace")
    db.commit()
    db.refresh(user)
    background_tasks.add_task(send_welcome_email, user.name, user.email)
    return AuthResponse(access_token=create_access_token(user.id), user=user_response(db, user))


@router.post("/login", response_model=AuthResponse)
def login(payload: LoginRequest, db: Database) -> AuthResponse:
    email = str(payload.email).strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email or password is incorrect.", headers={"WWW-Authenticate": "Bearer"})
    return AuthResponse(access_token=create_access_token(user.id), user=user_response(db, user))


@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, background_tasks: BackgroundTasks, db: Database) -> dict[str, str]:
    generic_response = {"message": "If an account exists for that email, password reset instructions will be sent shortly."}
    email = str(payload.email).strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    now = datetime.now(timezone.utc)
    if user is None:
        return generic_response

    recent_request = db.scalar(
        select(PasswordReset.id)
        .where(PasswordReset.user_id == user.id, PasswordReset.created_at >= now - RESET_COOLDOWN)
        .limit(1)
    )
    if recent_request:
        return generic_response

    db.execute(
        update(PasswordReset)
        .where(PasswordReset.user_id == user.id, PasswordReset.used_at.is_(None))
        .values(used_at=now)
    )
    token = secrets.token_urlsafe(32)
    code = f"{secrets.randbelow(1_000_000):06d}"
    reset = PasswordReset(
        user_id=user.id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        code_hash=_code_hash(code),
        expires_at=now + RESET_LIFETIME,
    )
    db.add(reset)
    db.commit()
    background_tasks.add_task(send_password_reset_email, user.name, user.email, token, code)
    return generic_response


@router.post("/reset-password")
def reset_password(payload: ResetPasswordRequest, db: Database) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    if payload.token:
        reset = db.scalar(
            select(PasswordReset).where(
                PasswordReset.token_hash == hashlib.sha256(payload.token.encode()).hexdigest()
            )
        )
        user = db.get(User, reset.user_id) if reset else None
    elif payload.email and payload.code:
        email = str(payload.email).strip().lower()
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            raise _invalid_reset()
        reset = db.scalar(
            select(PasswordReset)
            .where(PasswordReset.user_id == user.id)
            .order_by(PasswordReset.created_at.desc())
            .limit(1)
        )
        if reset and not hmac.compare_digest(reset.code_hash, _code_hash(payload.code)):
            if reset.used_at is None and reset.attempts < MAX_RESET_CODE_ATTEMPTS:
                reset.attempts += 1
                db.commit()
            raise _invalid_reset()
    else:
        raise _invalid_reset()

    expires_at = reset.expires_at if reset else now
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if (
        user is None
        or reset is None
        or reset.used_at is not None
        or reset.attempts >= MAX_RESET_CODE_ATTEMPTS
        or expires_at <= now
    ):
        raise _invalid_reset()

    user.password_hash = hash_password(payload.new_password)
    reset.used_at = now
    db.commit()
    return {"message": "Your password has been reset. You can now sign in with your new password."}


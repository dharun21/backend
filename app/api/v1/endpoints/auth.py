from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from sqlalchemy import select

from app.api.dependencies import CurrentUser, Database
from app.models.user import User
from app.services.email import send_welcome_email
from app.services.notifications import create_notification
from app.schemas.auth import AuthResponse, LoginRequest, ProfileUpdate, RegisterRequest, UserResponse
from app.services.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.patch("/me", response_model=UserResponse)
def update_current_user(payload: ProfileUpdate, user: CurrentUser, db: Database) -> User:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Name cannot be blank.")
    user.name = name
    db.commit()
    db.refresh(user)
    return user


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
    return AuthResponse(access_token=create_access_token(user.id), user=UserResponse.model_validate(user))


@router.post("/login", response_model=AuthResponse)
def login(payload: LoginRequest, db: Database) -> AuthResponse:
    email = str(payload.email).strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email or password is incorrect.", headers={"WWW-Authenticate": "Bearer"})
    return AuthResponse(access_token=create_access_token(user.id), user=UserResponse.model_validate(user))


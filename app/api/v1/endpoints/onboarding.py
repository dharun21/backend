from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.dependencies import CurrentUser, Database
from app.models.onboarding import OnboardingSession
from app.models.user import User
from app.schemas.onboarding import (
    BusinessProfileResponse,
    BusinessProfileUpdate,
    OnboardingAnswer,
    OnboardingSessionResponse,
    OnboardingStartResponse,
    OnboardingSubmitResponse,
)
from app.services.entitlements import require_specialist_access

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

ONBOARDING_QUESTIONS = [
    {
        "id": "business_type",
        "text": "What type of business do you run?",
        "type": "select",
        "options": ["Retail", "Corporate", "Services", "E-commerce", "Manufacturing", "Consulting", "Something else"],
        "allow_custom": True,
    },
    {
        "id": "industry",
        "text": "Which industry are you in?",
        "type": "select",
        "options": ["Technology", "Healthcare", "Finance", "Education", "Food & Beverage", "Other", "Something else"],
        "allow_custom": True,
    },
    {
        "id": "country",
        "text": "Which country is your business based in?",
        "type": "select",
        "options": ["United States", "United Kingdom", "Canada", "Australia", "India", "Other", "Something else"],
        "allow_custom": True,
    },
    {
        "id": "currency",
        "text": "What currency do you use?",
        "type": "select",
        "options": ["USD", "EUR", "GBP", "INR", "CAD", "AUD", "Something else"],
        "allow_custom": True,
    },
    {
        "id": "financial_year",
        "text": "What is your financial year?",
        "type": "select",
        "options": ["January - December", "April - March", "July - June", "October - September", "Something else"],
        "allow_custom": True,
    },
    {
        "id": "what_they_sell",
        "text": "What products or services do you sell?",
        "type": "text",
        "options": [],
        "allow_custom": True,
    },
    {
        "id": "revenue_model",
        "text": "How do you earn revenue?",
        "type": "select",
        "options": [
            "One-time sales",
            "Subscriptions",
            "Service fees",
            "Commission",
            "Advertising",
            "Something else",
        ],
        "allow_custom": True,
    },
    {
        "id": "business_stage",
        "text": "What stage is your business at?",
        "type": "select",
        "options": ["Starting", "Growing", "Established", "Something else"],
        "allow_custom": True,
    },
    {
        "id": "main_goal",
        "text": "What is your main goal?",
        "type": "select",
        "options": [
            "Improve profit",
            "Manage cash flow",
            "Grow sales",
            "Reduce costs",
            "Something else",
        ],
        "allow_custom": True,
    },
    {
        "id": "biggest_challenge",
        "text": "What is your biggest current challenge?",
        "type": "text",
        "options": [],
        "allow_custom": True,
    },
    {
        "id": "targets",
        "text": "Do you have any targets (e.g. monthly revenue or profit)?",
        "type": "text",
        "options": [],
        "allow_custom": True,
    },
]


def _get_or_create_session(db: Database, user: User) -> OnboardingSession:
    require_specialist_access(db, user.id, "finance_analyst")
    session = db.scalar(select(OnboardingSession).where(OnboardingSession.user_id == user.id))
    if session is None:
        session = OnboardingSession(user_id=user.id, current_step=0, completed=False, answers=[])
        db.add(session)
        db.commit()
        db.refresh(session)
    return session


@router.get("/start", response_model=OnboardingStartResponse)
def start_onboarding(user: CurrentUser, db: Database) -> OnboardingStartResponse:
    session = _get_or_create_session(db, user)
    if session.completed:
        return OnboardingStartResponse(
            session_id=session.id,
            current_step=session.current_step,
            question={},
            completed=True,
        )
    question = ONBOARDING_QUESTIONS[session.current_step] if session.current_step < len(ONBOARDING_QUESTIONS) else {}
    return OnboardingStartResponse(
        session_id=session.id,
        current_step=session.current_step,
        question=question,
        completed=False,
    )


@router.post("/submit", response_model=OnboardingSubmitResponse)
def submit_answer(
    answer: OnboardingAnswer,
    user: CurrentUser,
    db: Database,
) -> OnboardingSubmitResponse:
    session = _get_or_create_session(db, user)

    answer_entry = {
        "question_id": answer.question_id,
        "answer": answer.answer,
        "custom_answer": answer.custom_answer,
    }
    # SQLAlchemy does not track in-place mutations of JSON columns, so the list
    # has to be rebuilt and reassigned for the change to be persisted.
    answers = list(session.answers or [])
    answers.append(answer_entry)
    session.answers = answers

    session.current_step += 1
    if session.current_step >= len(ONBOARDING_QUESTIONS):
        session.completed = True
        _save_profile(db, user, session.answers)
        db.commit()
        return OnboardingSubmitResponse(
            session_id=session.id,
            current_step=session.current_step,
            completed=True,
            next_question=None,
        )

    db.commit()
    db.refresh(session)
    next_question = ONBOARDING_QUESTIONS[session.current_step]
    return OnboardingSubmitResponse(
        session_id=session.id,
        current_step=session.current_step,
        completed=False,
        next_question=next_question,
    )


@router.get("/session", response_model=OnboardingSessionResponse)
def get_session(user: CurrentUser, db: Database) -> OnboardingSessionResponse:
    session = _get_or_create_session(db, user)
    return OnboardingSessionResponse.model_validate(session)


@router.get("/profile", response_model=BusinessProfileResponse)
def get_profile(user: CurrentUser, db: Database) -> BusinessProfileResponse:
    from app.models.onboarding import BusinessProfile

    require_specialist_access(db, user.id, "finance_analyst")
    profile = db.scalar(
        select(BusinessProfile).where(BusinessProfile.user_id == user.id)
    )
    if profile is None:
        return BusinessProfileResponse()
    return BusinessProfileResponse.model_validate(profile)


@router.put("/profile", response_model=BusinessProfileResponse)
def update_profile(
    payload: BusinessProfileUpdate,
    user: CurrentUser,
    db: Database,
) -> BusinessProfileResponse:
    from app.models.onboarding import BusinessProfile
    from app.models.finance import FinanceWorkspace

    require_specialist_access(db, user.id, "finance_analyst")
    profile = db.scalar(
        select(BusinessProfile).where(BusinessProfile.user_id == user.id)
    )
    if profile is None:
        profile = BusinessProfile(user_id=user.id)
        db.add(profile)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)

    if "business_type" in payload.model_fields_set:
        workspace = db.scalar(select(FinanceWorkspace).where(FinanceWorkspace.owner_id == user.id))
        if workspace is not None:
            workspace.business_type = profile.business_type if profile.business_type in {"Retail", "Corporate"} else None

    db.commit()
    db.refresh(profile)
    return BusinessProfileResponse.model_validate(profile)


def _save_profile(db: Database, user: User, answers: list[dict]) -> None:
    from app.models.onboarding import BusinessProfile
    from app.models.finance import FinanceWorkspace

    answer_map = {a["question_id"]: a for a in answers}
    profile = db.scalar(
        select(BusinessProfile).where(BusinessProfile.user_id == user.id)
    )
    if profile is None:
        profile = BusinessProfile(user_id=user.id)
        db.add(profile)

    def get_answer(qid: str) -> str | None:
        entry = answer_map.get(qid, {})
        return entry.get("custom_answer") or entry.get("answer")

    profile.business_type = get_answer("business_type")
    workspace = db.scalar(select(FinanceWorkspace).where(FinanceWorkspace.owner_id == user.id))
    if workspace is not None:
        workspace.business_type = profile.business_type if profile.business_type in {"Retail", "Corporate"} else None
    profile.industry = get_answer("industry")
    profile.country = get_answer("country")
    profile.currency = get_answer("currency")
    profile.financial_year = get_answer("financial_year")
    profile.what_they_sell = get_answer("what_they_sell")
    profile.revenue_model = get_answer("revenue_model")
    profile.business_stage = get_answer("business_stage")
    profile.main_goal = get_answer("main_goal")
    profile.biggest_challenge = get_answer("biggest_challenge")

    targets_entry = answer_map.get("targets", {})
    targets_raw = targets_entry.get("custom_answer") or targets_entry.get("answer") or ""
    profile.targets = [{"text": targets_raw}] if targets_raw.strip() else []

    db.commit()


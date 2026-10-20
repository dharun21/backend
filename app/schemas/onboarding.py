from pydantic import BaseModel, ConfigDict, Field


class OnboardingAnswer(BaseModel):
    question_id: str
    answer: str | None = None
    custom_answer: str | None = None  # for "Something else"


class OnboardingSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    current_step: int
    completed: bool
    answers: list[dict]


class OnboardingStartResponse(BaseModel):
    session_id: str
    current_step: int
    question: dict  # {id, text, type, options, allow_custom}
    completed: bool


class OnboardingSubmitResponse(BaseModel):
    session_id: str
    current_step: int
    completed: bool
    next_question: dict | None = None


class BusinessProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    business_type: str | None
    industry: str | None
    country: str | None
    currency: str | None
    financial_year: str | None
    what_they_sell: str | None
    revenue_model: str | None
    business_stage: str | None
    main_goal: str | None
    biggest_challenge: str | None
    targets: list[dict]


class BusinessProfileUpdate(BaseModel):
    business_type: str | None = None
    industry: str | None = None
    country: str | None = None
    currency: str | None = None
    financial_year: str | None = None
    what_they_sell: str | None = None
    revenue_model: str | None = None
    business_stage: str | None = None
    main_goal: str | None = None
    biggest_challenge: str | None = None
    targets: list[dict] | None = None
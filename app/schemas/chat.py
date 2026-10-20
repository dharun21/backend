from datetime import datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class ChatMessageBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    role: str
    content: str
    message_type: str = "text"
    # The ORM column is `message_metadata`; SQLAlchemy models also expose a
    # `metadata` attribute (the table MetaData object), so the column name must
    # be tried first when validating from ORM instances.
    metadata: dict | None = Field(
        default=None,
        validation_alias=AliasChoices("message_metadata", "metadata"),
    )


class ChatMessageResponse(ChatMessageBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    created_at: datetime


class ChatConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    created_at: datetime
    updated_at: datetime


class ChatConversationListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    updated_at: datetime


class CreateConversationRequest(BaseModel):
    title: str = Field(default="New conversation", min_length=1, max_length=200)


class SendMessageRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(min_length=1, max_length=4000)
    attach_document_id: str | None = None


class SendMessageResponse(BaseModel):
    conversation_id: str
    message_id: str
    reply: str
    message_type: str
    metadata: dict | None = None
    suggested_questions: list[str] = Field(default_factory=list)
    needs_more_info: bool = False
    follow_up_questions: list[dict] = Field(default_factory=list)  # {id, text, options, allow_custom}


class DeleteConversationResponse(BaseModel):
    detail: str
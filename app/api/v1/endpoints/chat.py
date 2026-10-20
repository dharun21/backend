from fastapi import APIRouter, HTTPException, status

from app.api.dependencies import CurrentUser, Database
from app.models.chat import ChatConversation, ChatMessage
from app.schemas.chat import (
    ChatConversationListResponse,
    ChatConversationResponse,
    ChatMessageResponse,
    CreateConversationRequest,
    DeleteConversationResponse,
    SendMessageRequest,
    SendMessageResponse,
)
from app.services.ai_service import AIService
from app.services.entitlements import require_specialist_access
from sqlalchemy import select

router = APIRouter(prefix="/chat", tags=["chat"])


def _get_ai_service(db: Database, user: CurrentUser) -> AIService:
    require_specialist_access(db, user.id, "finance_analyst")
    return AIService(db, user)


@router.get("/conversations", response_model=list[ChatConversationListResponse])
def list_conversations(user: CurrentUser, db: Database) -> list[ChatConversation]:
    require_specialist_access(db, user.id, "finance_analyst")
    rows = db.scalars(
        select(ChatConversation)
        .where(ChatConversation.user_id == user.id)
        .order_by(ChatConversation.updated_at.desc())
    ).all()
    return rows


@router.post("/conversations", response_model=ChatConversationResponse)
def create_conversation(
    payload: CreateConversationRequest,
    user: CurrentUser,
    db: Database,
) -> ChatConversation:
    require_specialist_access(db, user.id, "finance_analyst")
    conv = ChatConversation(user_id=user.id, title=payload.title)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


@router.get("/conversations/{conversation_id}/messages", response_model=list[ChatMessageResponse])
def get_messages(conversation_id: str, user: CurrentUser, db: Database) -> list[ChatMessage]:
    require_specialist_access(db, user.id, "finance_analyst")
    owned = db.scalar(
        select(ChatConversation.id).where(
            ChatConversation.id == conversation_id,
            ChatConversation.user_id == user.id,
        )
    )
    if not owned:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    rows = db.scalars(
        select(ChatMessage)
        .where(
            ChatMessage.conversation_id == conversation_id,
            ChatMessage.conversation.has(user_id=user.id),
        )
        .order_by(ChatMessage.created_at.asc())
    ).all()
    return rows


@router.post("/send", response_model=SendMessageResponse)
def send_message(
    payload: SendMessageRequest,
    user: CurrentUser,
    db: Database,
) -> SendMessageResponse:
    ai = _get_ai_service(db, user)
    try:
        result = ai.generate_reply(
            conversation_id=payload.conversation_id,
            user_message=payload.message,
            attach_document_id=payload.attach_document_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return SendMessageResponse(
        conversation_id=result["conversation_id"],
        message_id=result["message_id"],
        reply=result["reply"],
        message_type=result["message_type"],
        metadata=result.get("metadata"),
        suggested_questions=result.get("suggested_questions", []),
        needs_more_info=result.get("needs_more_info", False),
        follow_up_questions=result.get("follow_up_questions", []),
    )


@router.delete("/conversations/{conversation_id}", response_model=DeleteConversationResponse)
def delete_conversation(conversation_id: str, user: CurrentUser, db: Database) -> DeleteConversationResponse:
    ai = _get_ai_service(db, user)
    if not ai.delete_conversation(conversation_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    return DeleteConversationResponse(detail="Conversation deleted.")

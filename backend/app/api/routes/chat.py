from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas import ChatRequest, ChatResponse
from app.services.app_service import get_app
from app.services.chat_service import chat_once, chat_stream, list_messages

router = APIRouter(tags=["chat"])


@router.post("/apps/{app_id}/chat", response_model=ChatResponse | None)
async def chat(app_id: str, payload: ChatRequest, db: Session = Depends(get_db)):
    app = get_app(db, app_id)
    if not app:
        raise HTTPException(status_code=404, detail="App not found")
    if payload.stream:
        return StreamingResponse(
            chat_stream(db, app, payload.query, payload.conversation_id),
            media_type="text/event-stream",
        )
    return await chat_once(db, app, payload.query, payload.conversation_id)


@router.get("/conversations/{conversation_id}/messages")
def messages(conversation_id: str, db: Session = Depends(get_db)):
    return list_messages(db, conversation_id)

import logging

from fastapi import APIRouter, Request

from app.services import telegram_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telegram", tags=["Telegram"])


@router.post("/webhook")
async def handle_telegram_update(request: Request):
    data = await request.json()

    message = data.get("message")
    if not message:
        return {"status": "ignored"}

    chat_id: int = message["chat"]["id"]
    username: str = message["from"].get("username", "Unknown")
    text: str = message.get("text", "")

    if text == "/start":
        logger.info("📩New user registered: %s (chat_id=%s)", username, chat_id)
        # await telegram_service.send_welcome_message(chat_id)  # Tam tat gui Telegram

    return {"status": "ok"}

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_TELEGRAM_API = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}"

telegram_client = httpx.AsyncClient(
    base_url=f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}",
    timeout=httpx.Timeout(15.0),
)


async def send_welcome_message(chat_id: int) -> None:
    payload = {
        "chat_id": chat_id,
        "text": "Chào mừng bạn đến với Taekwondo Văn Quán! 🥋",
    }
    try:
        # Sử dụng client dùng chung, không dùng 'async with' ở đây nữa
        response = await telegram_client.post("/sendMessage", json=payload)
        response.raise_for_status()
    except httpx.RequestError as exc:
        logger.error(f"Lỗi mạng: {exc}")

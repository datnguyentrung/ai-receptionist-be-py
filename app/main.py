import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import api_router
from app.core.config import settings
from app.db.session import engine
from app.exceptions.app_exception import AppException

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- 1. KIỂM TRA KẾT NỐI DATABASE ---
    try:
        print("🔄 Đang kiểm tra kết nối Database...")
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        print("✅ Kết nối Database Supabase thành công!")
    except Exception as e:
        print(f"❌ LỖI KẾT NỐI DATABASE: {e}")
        # Nếu muốn server dừng luôn không chạy nữa nếu lỗi DB, bạn có thể raise lỗi ở đây

    # Tam tat Telegram: khong tu dong dang ky webhook khi server khoi dong.
    # if not settings.SERVER_PUBLIC_URL:
    #     print("⚠️  SERVER_PUBLIC_URL chưa được cấu hình — bỏ qua đăng ký Webhook.")
    # else:
    #     webhook_url = f"{settings.SERVER_PUBLIC_URL}/telegram/webhook"
    #     set_url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/setWebhook?url={webhook_url}"
    #
    #     max_retries = 3
    #     retry_delay = 5  # giây
    #
    #     async with httpx.AsyncClient() as client:
    #         for attempt in range(1, max_retries + 1):
    #             try:
    #                 print(f"🔄 Đang đăng ký Webhook (Lần {attempt}/{max_retries})...")
    #                 resp = await client.get(set_url, timeout=10)
    #                 resp.raise_for_status()
    #                 data = resp.json()
    #
    #                 if data.get("ok"):
    #                     print(f"✅ Webhook đã đăng ký thành công: {webhook_url}")
    #                     break  # Thoát vòng lặp khi thành công
    #                 else:
    #                     print(f"❌ Telegram từ chối: {data}")
    #                     # Nếu Telegram từ chối (ví dụ sai URL), thường retry cũng không ích gì nên có thể break luôn hoặc đợi retry
    #                     break
    #
    #             except (httpx.RequestError, httpx.HTTPStatusError) as exc:
    #                 print(
    #                     f"❌ Lỗi đăng ký Webhook (Lần {attempt}): {type(exc).__name__}"
    #                 )
    #
    #                 if attempt < max_retries:
    #                     print(f"   → Thử lại sau {retry_delay} giây...")
    #                     await asyncio.sleep(retry_delay)
    #                 else:
    #                     print(
    #                         f"‼️ Đã thử {max_retries} lần nhưng thất bại. Vui lòng kiểm tra NGROK hoặc Internet."
    #                     )

    yield  # Server bắt đầu chạy tại đây

    # --- PHẦN ĐÓNG (SHUTDOWN) ---
    # Tam tat Telegram: khong khoi tao/dong Telegram client trong lifecycle.
    # from app.services.telegram_service import telegram_client
    #
    # await telegram_client.aclose()
    # print("🛑 Đã đóng kết nối Telegram Client.")


app = FastAPI(
    title=settings.PROJECT_NAME,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    """
    Tự động bắt mọi lỗi AppException văng ra trong code
    và trả về định dạng JSON chuẩn.
    """
    error_content = {
        "status_code": exc.error_code.status_code,
        "message": exc.error_code.message,
    }

    if exc.detail_message:
        error_content["detail"] = exc.detail_message

    return JSONResponse(
        status_code=exc.error_code.status_code,
        content=error_content,
    )


@app.get("/", tags=["Root"])
async def root_check():
    """
    Route này sinh ra chỉ để 'dỗ' hệ thống giám sát của Hugging Face.
    Trả về 200 OK để nó biết container vẫn đang sống nhăn răng.
    """
    return {"message": "Hugging Face Space is running smoothly!"}


@app.get("/health", tags=["Health"])
async def health_check():
    """
    Tương đương với Actuator Health bên Java Spring Boot.
    Kiểm tra xem Database có thực sự kết nối được không.
    """
    try:
        # Thực hiện một truy vấn siêu nhẹ để check DB
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        return {
            "status": "UP",
            "database": "CONNECTED",
            "version": settings.PROJECT_NAME,
        }
    except Exception as e:
        # Trả về lỗi 503 nếu DB có vấn đề để hệ thống giám sát biết mà restart app
        return JSONResponse(
            status_code=503,
            content={"status": "DOWN", "database": "ERROR", "detail": str(e)},
        )

from fastapi import APIRouter

from app.api.face_embedding_api import face_embedding_router
from app.api.telegram_api import router as telegram_router

api_router = APIRouter()
api_router.include_router(face_embedding_router)
api_router.include_router(telegram_router)

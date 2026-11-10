from fastapi import APIRouter

from app.api.face_check_in_scan_api import router as face_check_in_scan_router
from app.api.face_embedding_api import face_embedding_router
from app.api.telegram_api import router as telegram_router
from app.api.device_provisioning_api import router as device_provisioning_router

api_router = APIRouter()
api_router.include_router(face_check_in_scan_router)
api_router.include_router(face_embedding_router)
api_router.include_router(telegram_router)
api_router.include_router(device_provisioning_router)

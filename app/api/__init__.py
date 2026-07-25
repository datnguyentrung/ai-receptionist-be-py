from fastapi import APIRouter

from app.api.person_api import person_router

api_router = APIRouter()
api_router.include_router(person_router)

from fastapi import APIRouter

from app.api.v1.endpoints import admin, auth, finance, notifications, system

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(finance.router)
api_router.include_router(notifications.router)

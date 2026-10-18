from fastapi import APIRouter

from app.api.v1.endpoints import auth, finance, system

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(auth.router)
api_router.include_router(finance.router)

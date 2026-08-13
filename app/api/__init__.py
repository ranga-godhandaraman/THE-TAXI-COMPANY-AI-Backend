"""API routers."""

from fastapi import APIRouter

from app.api import agents, auth, chat, chat_sessions, health, profile, rag, taxi

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(profile.router)
api_router.include_router(taxi.router)
api_router.include_router(rag.router)
api_router.include_router(agents.router)
api_router.include_router(chat.router)
api_router.include_router(chat_sessions.router)

"""Versioned API routes."""

from fastapi import APIRouter, Depends

from app.core.server_access import document_server_token

from .feed import router as feed_router
from .mcp_updates import router as mcp_updates_router
from .rankings import router as rankings_router
from .saved import router as saved_router
from .sources import router as sources_router
from .translation_preferences import router as translation_preferences_router
from .translations import router as translations_router

api_router = APIRouter(prefix="/v1", dependencies=[Depends(document_server_token)])


api_router.include_router(sources_router)
api_router.include_router(feed_router)
api_router.include_router(rankings_router)
api_router.include_router(saved_router)
api_router.include_router(translation_preferences_router)
api_router.include_router(translations_router)
api_router.include_router(mcp_updates_router)

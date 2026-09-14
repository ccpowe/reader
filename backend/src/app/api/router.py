"""Versioned API routes."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.auth import AuthenticatedUser, require_current_user
from app.core.server_access import document_server_token

from .auth import router as auth_router
from .feed import router as feed_router
from .profile import router as profile_router
from .rankings import router as rankings_router
from .saved import router as saved_router
from .sources import router as sources_router
from .translation_preferences import router as translation_preferences_router
from .translations import router as translations_router

api_router = APIRouter(prefix="/v1", dependencies=[Depends(document_server_token)])


class CurrentUserResponse(BaseModel):
    id: str
    email: str | None


@api_router.get("/me", response_model=CurrentUserResponse, tags=["auth"])
async def get_current_user(
    current_user: AuthenticatedUser = Depends(require_current_user),
) -> CurrentUserResponse:
    """Return the current Reader user (legacy /me alias)."""
    return CurrentUserResponse(id=str(current_user.id), email=current_user.email)


api_router.include_router(auth_router)
api_router.include_router(sources_router)
api_router.include_router(feed_router)
api_router.include_router(profile_router)
api_router.include_router(rankings_router)
api_router.include_router(saved_router)
api_router.include_router(translation_preferences_router)
api_router.include_router(translations_router)

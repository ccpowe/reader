from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api import profile
from app.core.auth import AuthenticatedUser
from app.storage.models import Profile


def _user(user_id):
    return AuthenticatedUser(id=user_id, email="reader@example.com", claims={})


@pytest.mark.asyncio
async def test_get_profile_returns_backend_owned_fields_and_session_email() -> None:
    user_id = uuid4()
    stored = Profile(id=user_id, display_name="Reader", avatar_url="https://cdn.example/avatar.png")
    session = AsyncMock()
    session.get.return_value = stored

    response = await profile.get_profile(request=None, current_user=_user(user_id), session=session)

    assert response.model_dump() == {
        "id": str(user_id),
        "email": "reader@example.com",
        "display_name": "Reader",
        "avatar_url": "https://cdn.example/avatar.png",
    }
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_profile_only_changes_fields_present_in_patch() -> None:
    user_id = uuid4()
    stored = Profile(id=user_id, display_name="Before", avatar_url="https://cdn.example/before.png")
    session = AsyncMock()
    session.get.return_value = stored

    response = await profile.update_profile(
        profile.UpdateProfileRequest(display_name=" After "),
        request=None,
        current_user=_user(user_id),
        session=session,
    )

    assert stored.display_name == "After"
    assert stored.avatar_url == "https://cdn.example/before.png"
    assert response.display_name == "After"
    session.commit.assert_awaited_once()


def test_profile_payload_rejects_unsafe_avatar_url_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        profile.UpdateProfileRequest(avatar_url="javascript:alert(1)")
    with pytest.raises(ValidationError):
        profile.UpdateProfileRequest(avatar_url="https://user:secret@example.test/avatar.png")
    with pytest.raises(ValidationError):
        profile.UpdateProfileRequest(display_name="Reader", unexpected="value")


@pytest.mark.asyncio
async def test_get_profile_commits_legacy_backfill():
    user_id = uuid4()
    session = AsyncMock()
    session.get.side_effect = [None, Profile(id=user_id)]
    response = await profile.get_profile(request=None, current_user=_user(user_id), session=session)
    assert response.id == str(user_id)
    session.commit.assert_awaited_once()

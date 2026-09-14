"""Public opaque avatar URLs contain no account or deployment credentials."""

import warnings
from io import BytesIO
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.storage.auth_models import ProfileAvatar
from app.storage.database import get_session

router = APIRouter(tags=["profile"])
AVATAR_MAX_BYTES = 5 * 1024 * 1024
AVATAR_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


def validate_avatar(data: bytes, declared_type: str) -> str:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                content_type = AVATAR_TYPES.get(image.format or "")
                if content_type is None or content_type != declared_type:
                    raise ValueError("type mismatch")
                # Bound decompression in addition to the encoded-byte limit.
                if image.width * image.height > 16_000_000 or getattr(image, "n_frames", 1) != 1:
                    raise ValueError("dimensions or animation")
                image.verify()
            # verify() checks structure; load() detects truncated pixel payloads.
            with Image.open(BytesIO(data)) as image:
                image.load()
        return content_type
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise HTTPException(
            status_code=422, detail="Invalid or unsupported avatar image."
        ) from None


@router.get(
    "/avatars/{user_id}/{version}",
    responses={
        200: {
            "content": {
                "image/jpeg": {},
                "image/png": {},
                "image/webp": {},
            }
        }
    },
)
async def get_avatar(
    user_id: UUID,
    version: UUID,
    session: AsyncSession = Depends(get_session),
) -> Response:
    avatar = (
        await session.execute(
            select(ProfileAvatar).where(
                ProfileAvatar.user_id == user_id,
                ProfileAvatar.version == version,
            )
        )
    ).scalar_one_or_none()
    if avatar is None:
        raise HTTPException(status_code=404, detail="Avatar not found.")
    return Response(
        content=avatar.data,
        media_type=avatar.content_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "public, max-age=86400, immutable",
        },
    )

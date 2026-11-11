"""Uploaded avatar checks: real JPEG / PNG / WebP, bounded size and pixels."""

from __future__ import annotations

import warnings
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from reader.core.errors import AppError

AVATAR_MAX_BYTES = 5 * 1024 * 1024
AVATAR_MAX_PIXELS = 16_000_000
AVATAR_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


def validate_avatar(data: bytes, declared_type: str) -> None:
    """Raise unless `data` is a single-frame image of the declared type."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if AVATAR_TYPES.get(image.format or "") != declared_type:
                    raise ValueError("type mismatch")
                if image.width * image.height > AVATAR_MAX_PIXELS:
                    raise ValueError("too many pixels")
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError("animated")
                image.verify()
            # verify() checks structure; load() catches truncated pixel data.
            with Image.open(BytesIO(data)) as image:
                image.load()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,  # Pillow's verify() reports a broken PNG checksum this way
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise AppError(422, "invalid_avatar", "The image is invalid or unsupported.") from None

"""Read public YouTube caption tracks without translating or fetching supplied URLs."""

import asyncio
import re
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

import requests
from youtube_transcript_api import (
    AgeRestricted,
    IpBlocked,
    NoTranscriptFound,
    PoTokenRequired,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    VideoUnplayable,
    YouTubeRequestFailed,
    YouTubeTranscriptApi,
    YouTubeTranscriptApiException,
)

_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
_CURSOR = re.compile(r"([A-Za-z0-9_-]{22}):([1-9][0-9]*)\Z")
_MAX_SEGMENTS_PER_PAGE = 60
_MAX_TEXT_PER_PAGE = 12_000
_MAX_SEGMENT_TEXT = 2_000
_CACHE_SIZE = 16
_CACHE_SECONDS = 30 * 60
_MAX_PROVIDER_BYTES = 4 * 1024 * 1024
_MAX_CAPTION_BYTES = 2 * 1024 * 1024
_MAX_CACHE_BYTES = 8 * 1024 * 1024
_MAX_CUES = 10_000
_MAX_PENDING = 2
_FETCH_SECONDS = 30


class TranscriptError(Exception):
    """Safe error with a stable code for MCP clients (no upstream URLs or credentials)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def parse_youtube_video_id(video_url: str) -> str:
    """Only recognize explicit YouTube video URLs; never request the input URL."""
    if not isinstance(video_url, str) or len(video_url) > 2048 or not video_url:
        raise TranscriptError("invalid_url", "A YouTube video URL is required.")
    if any(ord(char) <= 32 or ord(char) == 127 for char in video_url):
        raise TranscriptError("invalid_url", "Invalid YouTube video URL.")
    try:
        parsed = urlsplit(video_url)
        if (
            parsed.scheme not in ("https", "http")
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
        ):
            raise TranscriptError("invalid_url", "Only public YouTube video URLs are supported.")
        host = parsed.hostname
    except ValueError as exc:
        raise TranscriptError("invalid_url", "Invalid YouTube video URL.") from exc
    if host in ("youtube.com", "www.youtube.com", "m.youtube.com"):
        if parsed.path == "/watch":
            try:
                ids = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=100).get("v")
            except ValueError as exc:
                raise TranscriptError("invalid_url", "Invalid YouTube video URL.") from exc
            video_id = ids[0] if ids and len(ids) == 1 else ""
        else:
            match = re.fullmatch(r"/(?:shorts|embed|live)/([A-Za-z0-9_-]{11})/?", parsed.path)
            video_id = match.group(1) if match else ""
    elif host in ("youtu.be", "www.youtu.be"):
        match = re.fullmatch(r"/([A-Za-z0-9_-]{11})/?", parsed.path)
        video_id = match.group(1) if match else ""
    else:
        video_id = ""
    if not _VIDEO_ID.fullmatch(video_id):
        raise TranscriptError(
            "invalid_url", "Only YouTube video URLs with an 11-character video ID are supported."
        )
    return video_id


class _TimedSession(requests.Session):
    def __init__(self) -> None:
        super().__init__()
        self.deadline = time.monotonic() + _FETCH_SECONDS

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TranscriptError("network", "YouTube caption request timed out.")
        return remaining

    def request(self, method: str, url: str, **kwargs: object) -> requests.Response:
        # Upstream caption URLs and redirects must not escape YouTube.
        try:
            target = urlsplit(url)
            allowed = (
                target.scheme == "https"
                and target.hostname == "www.youtube.com"
                and target.username is None
                and target.password is None
                and target.port is None
            )
        except ValueError:
            allowed = False
        if not allowed:
            raise TranscriptError("network", "YouTube returned an unsupported caption URL.")
        remaining = self.remaining()
        kwargs["timeout"] = (min(5, remaining), min(8, remaining))
        kwargs["allow_redirects"] = False
        kwargs["stream"] = True
        response = super().request(method, url, **kwargs)
        try:
            self.remaining()
            if response.status_code in (403, 429):
                raise TranscriptError(
                    "blocked", "YouTube is blocking transcript requests from this server."
                )
            if 300 <= response.status_code < 400:
                raise TranscriptError("network", "YouTube redirected the caption request.")
            response.raise_for_status()
            chunks = []
            size = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                self.remaining()
                size += len(chunk)
                if size > _MAX_PROVIDER_BYTES:
                    raise TranscriptError(
                        "limit", "YouTube caption response exceeds the size limit."
                    )
                chunks.append(chunk)
            # Requests' json/text accessors expect a buffered response. Limit decoded
            # bytes before giving it to the provider parser; never truncate.
            response._content = b"".join(chunks)
            response._content_consumed = True
            return response
        finally:
            response.close()


@dataclass(frozen=True)
class _CaptionData:
    language: str
    language_code: str
    is_generated: bool
    selection: str
    segments: tuple[tuple[float, float, str], ...]
    size_bytes: int


def _fetch_original(video_id: str) -> _CaptionData:
    try:
        with _TimedSession() as session:
            tracks = tuple(YouTubeTranscriptApi(http_client=session).list(video_id))
            if not tracks:
                raise TranscriptError("no_captions", "No captions are available for this video.")
            # The library's list exposes no default audio-language metadata. ASR tracks
            # reflect spoken audio; prefer them over manually uploaded tracks. If no ASR
            # exists, the first original (non-translated) manual track is used.
            generated = [track for track in tracks if track.is_generated]
            manual = [track for track in tracks if not track.is_generated]
            for track in (*generated, *manual):
                fetched = track.fetch()
                segments = []
                retained_bytes = 0
                for snippet in fetched:
                    session.remaining()
                    if not snippet.text:
                        continue
                    for offset in range(0, len(snippet.text), _MAX_SEGMENT_TEXT):
                        chunk = snippet.text[offset : offset + _MAX_SEGMENT_TEXT]
                        retained_bytes += len(chunk.encode("utf-8")) + 128
                        if retained_bytes > _MAX_CAPTION_BYTES or len(segments) >= _MAX_CUES:
                            raise TranscriptError(
                                "limit", "YouTube transcript exceeds the cache limit."
                            )
                        segments.append(
                            (float(snippet.start), float(snippet.start + snippet.duration), chunk)
                        )
                if segments:
                    return _CaptionData(
                        language=track.language,
                        language_code=track.language_code,
                        is_generated=track.is_generated,
                        selection=(
                            "auto-generated speech caption track"
                            if track.is_generated
                            else "first available manually uploaded caption track"
                        ),
                        segments=tuple(segments),
                        size_bytes=retained_bytes,
                    )
            raise TranscriptError("network", "Caption tracks were returned without subtitle text.")
    except (TranscriptsDisabled, NoTranscriptFound) as exc:
        raise TranscriptError("no_captions", "No captions are available for this video.") from exc
    except (AgeRestricted, PoTokenRequired) as exc:
        raise TranscriptError(
            "restricted", "This video's captions require restricted access."
        ) from exc
    except (IpBlocked, RequestBlocked) as exc:
        raise TranscriptError(
            "blocked", "YouTube is blocking transcript requests from this server."
        ) from exc
    except (VideoUnavailable, VideoUnplayable) as exc:
        raise TranscriptError(
            "unavailable", "This video is unavailable or cannot be played publicly."
        ) from exc
    except YouTubeRequestFailed as exc:
        raise TranscriptError("network", "YouTube caption request failed.") from exc
    except (requests.RequestException, YouTubeTranscriptApiException) as exc:
        raise TranscriptError("network", "YouTube caption request failed.") from exc


@dataclass(frozen=True)
class _Cached:
    token: str
    expires_at: float
    captions: _CaptionData


class TranscriptReader:
    """Read and page stable original caption tracks; no subscription or reader side effects.

    The instance keeps a short-lived snapshot for each video. Expired or evicted
    cursors fail explicitly rather than silently fetching a changed transcript.
    Provider errors become TranscriptError with stable, safe codes.
    """

    def __init__(self) -> None:
        self._cache: OrderedDict[str, _Cached] = OrderedDict()
        self._cache_bytes = 0
        self._pending: dict[str, asyncio.Task[_Cached]] = {}
        self._closed = False
        self._lock = asyncio.Lock()

    async def _load(self, video_id: str) -> _Cached:
        try:
            captions = await asyncio.to_thread(_fetch_original, video_id)
            cached = _Cached(secrets.token_urlsafe(16), time.monotonic() + _CACHE_SECONDS, captions)
            async with self._lock:
                if not self._closed:
                    while self._cache and (
                        len(self._cache) >= _CACHE_SIZE
                        or self._cache_bytes + captions.size_bytes > _MAX_CACHE_BYTES
                    ):
                        _, evicted = self._cache.popitem(last=False)
                        self._cache_bytes -= evicted.captions.size_bytes
                    self._cache[video_id] = cached
                    self._cache_bytes += captions.size_bytes
            return cached
        finally:
            async with self._lock:
                self._pending.pop(video_id, None)

    async def aclose(self) -> None:
        """Stop admission and wait for bounded in-flight provider work before shutdown."""
        async with self._lock:
            self._closed = True
            pending = tuple(self._pending.values())
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        async with self._lock:
            self._cache.clear()
            self._cache_bytes = 0

    async def get_youtube_transcript(self, video_url: str, cursor: str | None = None) -> dict:
        """Return a bounded page with timestamps, metadata and next_cursor if incomplete.

        A cursor is only valid for this instance's current snapshot of the video.
        Raises TranscriptError with a safe code for invalid URLs/cursors, absent
        captions, inaccessible videos, blocking or network failures.
        """
        video_id = parse_youtube_video_id(video_url)
        token = None
        offset = 0
        if cursor is not None:
            match = (
                _CURSOR.fullmatch(cursor) if isinstance(cursor, str) and len(cursor) <= 32 else None
            )
            if not match:
                raise TranscriptError("invalid_cursor", "Invalid transcript cursor.")
            token, offset_text = match.groups()
            offset = int(offset_text)

        async with self._lock:
            if self._closed:
                raise TranscriptError("closed", "Transcript reader is closed.")
            cached = self._cache.get(video_id)
            if cached is not None and cached.expires_at <= time.monotonic():
                del self._cache[video_id]
                self._cache_bytes -= cached.captions.size_bytes
                cached = None
            if token is not None:
                if cached is None or cached.token != token:
                    raise TranscriptError(
                        "cursor_expired", "Transcript cursor expired; start without a cursor."
                    )
            elif cached is None:
                pending = self._pending.get(video_id)
                if pending is None:
                    if len(self._pending) >= _MAX_PENDING:
                        raise TranscriptError("busy", "Transcript reader is busy; try again later.")
                    pending = asyncio.create_task(self._load(video_id))
                    pending.add_done_callback(
                        lambda finished: finished.exception() if not finished.cancelled() else None
                    )
                    self._pending[video_id] = pending
            if cached is not None:
                self._cache.move_to_end(video_id)
        if cached is None:
            cached = await asyncio.shield(pending)

        segments = cached.captions.segments
        if offset >= len(segments):
            raise TranscriptError(
                "invalid_cursor", "Transcript cursor is beyond the end of the captions."
            )
        page = []
        char_count = 0
        index = offset
        while index < len(segments) and len(page) < _MAX_SEGMENTS_PER_PAGE:
            start, end, text = segments[index]
            if char_count + len(text) > _MAX_TEXT_PER_PAGE:
                break
            page.append({"start": start, "end": end, "text": text})
            char_count += len(text)
            index += 1
        next_cursor = f"{cached.token}:{index}" if index < len(segments) else None
        return {
            "video_id": video_id,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "language": cached.captions.language,
            "language_code": cached.captions.language_code,
            "is_generated": cached.captions.is_generated,
            "language_selection": cached.captions.selection,
            "segments": page,
            "complete": next_cursor is None,
            "next_cursor": next_cursor,
        }

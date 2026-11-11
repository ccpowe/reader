"""Consumer behavior for public video captions and stable cursor pages."""

import asyncio
import os
from threading import Event
from types import SimpleNamespace

import pytest
import requests

pytest.importorskip("youtube_transcript_api")

from youtube_transcript_api import (  # noqa: E402
    AgeRestricted,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
)

from reader.mcp import transcripts  # noqa: E402
from reader.mcp.transcripts import (  # noqa: E402
    TranscriptError,
    TranscriptReader,
    parse_youtube_video_id,
)

VIDEO = "dQw4w9WgXcQ"
URL = f"https://www.youtube.com/watch?v={VIDEO}"


@pytest.mark.parametrize(
    "url",
    [
        URL,
        f"http://m.youtube.com/watch?feature=share&v={VIDEO}&t=12",
        f"https://youtube.com/shorts/{VIDEO}",
        f"https://www.youtube.com/embed/{VIDEO}/",
        f"https://youtube.com/live/{VIDEO}",
        f"https://youtu.be/{VIDEO}?t=12",
    ],
)
def test_accepts_public_video_url_forms(url: str) -> None:
    assert parse_youtube_video_id(url) == VIDEO


@pytest.mark.parametrize(
    "url",
    [
        VIDEO,
        f"https://youtube.com.evil.test/watch?v={VIDEO}",
        f"https://evil.test/watch?v={VIDEO}",
        f"https://user@youtube.com/watch?v={VIDEO}",
        f"https://youtube.com:443/watch?v={VIDEO}",
        f"file:///watch?v={VIDEO}",
        f"https://youtube.com/watch?v={VIDEO}&v={VIDEO}",
        "https://youtube.com/watch?v=bad-id",
        f"https://youtube.com/%77atch?v={VIDEO}",
        f"https://youtu.be/{VIDEO}/extra",
        f"https://youtube.com//shorts/{VIDEO}",
        f"https://youtube.com/watch?v={VIDEO}\nhttps://evil.test/",
    ],
)
def test_rejects_unsafe_or_ambiguous_video_urls(url: str) -> None:
    with pytest.raises(TranscriptError) as error:
        parse_youtube_video_id(url)
    assert error.value.code == "invalid_url"


class CaptionTrack:
    def __init__(self, language: str, generated: bool, snippets: list[object]):
        self.language = language
        self.language_code = "en" if generated else "de"
        self.is_generated = generated
        self.snippets = snippets
        self.fetches = 0

    def fetch(self) -> list[object]:
        self.fetches += 1
        return self.snippets

    def translate(self, _language: str) -> None:
        raise AssertionError("Original captions must never be translated")


def provider(monkeypatch: pytest.MonkeyPatch, tracks: list[CaptionTrack]) -> None:
    class API:
        def __init__(self, http_client: requests.Session):
            assert isinstance(http_client, requests.Session)

        def list(self, video_id: str) -> list[CaptionTrack]:
            assert video_id == VIDEO
            return tracks

    monkeypatch.setattr(transcripts, "YouTubeTranscriptApi", API)


@pytest.mark.asyncio
async def test_prefers_original_asr_and_pages_without_refetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manual = CaptionTrack("German", False, [SimpleNamespace(start=0, duration=1, text="manual")])
    auto = CaptionTrack(
        "English",
        True,
        [
            SimpleNamespace(start=float(index), duration=0.5, text=f"cue {index}")
            for index in range(61)
        ],
    )
    provider(monkeypatch, [manual, auto])
    reader = TranscriptReader()
    first = await reader.get_youtube_transcript(URL)
    assert first["video_id"] == VIDEO
    assert first["url"] == URL
    assert (first["language"], first["language_code"], first["is_generated"]) == (
        "English",
        "en",
        True,
    )
    assert first["segments"] == [
        {"start": float(index), "end": index + 0.5, "text": f"cue {index}"} for index in range(60)
    ]
    assert not first["complete"] and first["next_cursor"]
    last = await reader.get_youtube_transcript(f"https://youtu.be/{VIDEO}", first["next_cursor"])
    assert last["segments"] == [{"start": 60.0, "end": 60.5, "text": "cue 60"}]
    assert last["complete"] and last["next_cursor"] is None
    assert auto.fetches == 1 and manual.fetches == 0
    assert (await reader.get_youtube_transcript(URL)) == first


@pytest.mark.asyncio
async def test_long_cue_pages_without_losing_text(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "a" * 26_001
    provider(
        monkeypatch,
        [CaptionTrack("German", False, [SimpleNamespace(start=2, duration=3, text=text)])],
    )
    reader = TranscriptReader()
    page = await reader.get_youtube_transcript(URL)
    pages = [page]
    while page["next_cursor"]:
        page = await reader.get_youtube_transcript(URL, page["next_cursor"])
        pages.append(page)
    assert len(pages) == 3
    assert all(len("".join(segment["text"] for segment in p["segments"])) <= 12_000 for p in pages)
    assert "".join(segment["text"] for p in pages for segment in p["segments"]) == text
    assert all(
        segment["start"] == 2.0 and segment["end"] == 5.0
        for p in pages
        for segment in p["segments"]
    )
    assert page["complete"]


@pytest.mark.asyncio
async def test_cursor_rejects_other_video_and_expired_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider(
        monkeypatch,
        [
            CaptionTrack(
                "German",
                False,
                [SimpleNamespace(start=i, duration=1, text="x") for i in range(61)],
            )
        ],
    )
    reader = TranscriptReader()
    first = await reader.get_youtube_transcript(URL)
    cursor = first["next_cursor"]
    with pytest.raises(TranscriptError) as error:
        await reader.get_youtube_transcript(URL, "not-a-cursor")
    assert error.value.code == "invalid_cursor"
    with pytest.raises(TranscriptError) as error:
        await reader.get_youtube_transcript("https://youtu.be/jNQXAC9IVRw", cursor)
    assert error.value.code == "cursor_expired"
    now = transcripts.time.monotonic()
    monkeypatch.setattr(transcripts.time, "monotonic", lambda: now + 1801)
    with pytest.raises(TranscriptError) as error:
        await reader.get_youtube_transcript(URL, cursor)
    assert error.value.code == "cursor_expired"


@pytest.mark.parametrize(
    ("exception", "code"),
    [
        (TranscriptsDisabled(VIDEO), "no_captions"),
        (RequestBlocked(VIDEO), "blocked"),
        (AgeRestricted(VIDEO), "restricted"),
        (VideoUnavailable(VIDEO), "unavailable"),
        (requests.Timeout("private URL"), "network"),
    ],
)
@pytest.mark.asyncio
async def test_provider_failure_has_safe_distinct_code(
    monkeypatch: pytest.MonkeyPatch, exception: Exception, code: str
) -> None:
    class API:
        def __init__(self, http_client: requests.Session):
            pass

        def list(self, video_id: str) -> list[object]:
            raise exception

    monkeypatch.setattr(transcripts, "YouTubeTranscriptApi", API)
    with pytest.raises(TranscriptError) as error:
        await TranscriptReader().get_youtube_transcript(URL)
    assert error.value.code == code
    assert "private URL" not in str(error.value)


@pytest.mark.asyncio
async def test_empty_caption_track_is_not_success(monkeypatch: pytest.MonkeyPatch) -> None:
    provider(monkeypatch, [CaptionTrack("German", False, [])])
    with pytest.raises(TranscriptError) as error:
        await TranscriptReader().get_youtube_transcript(URL)
    assert error.value.code == "network"


@pytest.mark.parametrize(
    ("status", "code"),
    [(302, "network"), (403, "blocked"), (429, "blocked"), (500, "network"), (200, "limit")],
)
@pytest.mark.asyncio
async def test_http_failures_are_classified_and_bounded_without_redirects(
    monkeypatch: pytest.MonkeyPatch, status: int, code: str
) -> None:
    requests_seen = []

    def request(_session: requests.Session, _method: str, _url: str, **kwargs: object):
        requests_seen.append(kwargs)
        response = requests.Response()
        response.status_code = status
        response.url = "https://www.youtube.com/watch?v=403abcde789"
        response.headers["Location"] = "https://untrusted.example/private"
        chunk = b"x" * (4 * 1024 * 1024 + 1) if status == 200 else b""
        response.raw = SimpleNamespace(
            stream=lambda *_args, **_kwargs: iter([chunk]),
            close=lambda: None,
        )
        return response

    class API:
        def __init__(self, http_client: requests.Session):
            self.client = http_client

        def list(self, _video_id: str) -> list[object]:
            self.client.get("https://www.youtube.com/watch?v=403abcde789")
            raise AssertionError("HTTP error or size limit should prevent parsing")

    monkeypatch.setattr(requests.Session, "request", request)
    monkeypatch.setattr(transcripts, "YouTubeTranscriptApi", API)
    with pytest.raises(TranscriptError) as error:
        await TranscriptReader().get_youtube_transcript("https://youtu.be/403abcde789")
    assert error.value.code == code
    assert len(requests_seen) == 1
    assert requests_seen[0]["allow_redirects"] is False
    assert requests_seen[0]["stream"] is True
    assert "untrusted.example" not in str(error.value)


@pytest.mark.asyncio
async def test_expired_fetch_deadline_prevents_new_request(monkeypatch: pytest.MonkeyPatch) -> None:
    class API:
        def __init__(self, http_client: transcripts._TimedSession):
            self.client = http_client

        def list(self, _video_id: str) -> list[object]:
            self.client.deadline = transcripts.time.monotonic() - 1
            self.client.get(URL)
            raise AssertionError("Expired requests must not reach the network")

    monkeypatch.setattr(transcripts, "YouTubeTranscriptApi", API)
    with pytest.raises(TranscriptError) as error:
        await TranscriptReader().get_youtube_transcript(URL)
    assert error.value.code == "network"


@pytest.mark.asyncio
async def test_fetch_deadline_is_checked_between_decoded_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def request(session: requests.Session, _method: str, _url: str, **_kwargs: object):
        response = requests.Response()
        response.status_code = 200
        response.url = URL

        def chunks(*_args: object, **_kwargs: object):
            yield b"first"
            session.deadline = transcripts.time.monotonic() - 1
            yield b"second"

        response.raw = SimpleNamespace(stream=chunks, close=lambda: None)
        return response

    class API:
        def __init__(self, http_client: transcripts._TimedSession):
            self.client = http_client

        def list(self, _video_id: str) -> list[object]:
            self.client.get(URL)
            raise AssertionError("Deadline must stop parsing")

    monkeypatch.setattr(requests.Session, "request", request)
    monkeypatch.setattr(transcripts, "YouTubeTranscriptApi", API)
    with pytest.raises(TranscriptError) as error:
        await TranscriptReader().get_youtube_transcript(URL)
    assert error.value.code == "network"


@pytest.mark.asyncio
async def test_provider_caption_url_cannot_escape_youtube(monkeypatch: pytest.MonkeyPatch) -> None:
    def request(*_args: object, **_kwargs: object):
        raise AssertionError("No arbitrary host fetch permitted")

    class API:
        def __init__(self, http_client: transcripts._TimedSession):
            self.client = http_client

        def list(self, _video_id: str) -> list[object]:
            self.client.get("https://internal.example/private")
            raise AssertionError("Invalid provider URL must not be requested")

    monkeypatch.setattr(requests.Session, "request", request)
    monkeypatch.setattr(transcripts, "YouTubeTranscriptApi", API)
    with pytest.raises(TranscriptError) as error:
        await TranscriptReader().get_youtube_transcript(URL)
    assert error.value.code == "network"


@pytest.mark.asyncio
async def test_caption_cache_budget_evicts_stale_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    class API:
        def __init__(self, http_client: requests.Session):
            pass

        def list(self, video_id: str) -> list[CaptionTrack]:
            return [
                CaptionTrack(
                    "German",
                    False,
                    [SimpleNamespace(start=i, duration=1, text="x" * 100) for i in range(61)],
                )
            ]

    # Two 61-cue transcripts exceed the reduced aggregate budget, so choose
    # a reduced budget that still admits each one separately.
    monkeypatch.setattr(transcripts, "_MAX_CACHE_BYTES", 61 * 228 + 1)
    monkeypatch.setattr(transcripts, "YouTubeTranscriptApi", API)
    reader = TranscriptReader()
    first = await reader.get_youtube_transcript(URL)
    assert first["next_cursor"]
    await reader.get_youtube_transcript("https://youtu.be/jNQXAC9IVRw")
    with pytest.raises(TranscriptError) as error:
        await reader.get_youtube_transcript(URL, first["next_cursor"])
    assert error.value.code == "cursor_expired"


@pytest.mark.asyncio
async def test_retained_caption_limit_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    provider(
        monkeypatch,
        [
            CaptionTrack(
                "German",
                False,
                [SimpleNamespace(start=0, duration=1, text="x" * (2 * 1024 * 1024))],
            )
        ],
    )
    with pytest.raises(TranscriptError) as error:
        await TranscriptReader().get_youtube_transcript(URL)
    assert error.value.code == "limit"


@pytest.mark.asyncio
async def test_inflight_admission_and_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    release = Event()
    started = Event()
    count = 0

    def fetch(_video_id: str) -> transcripts._CaptionData:
        nonlocal count
        count += 1
        if count == 2:
            started.set()
        assert release.wait(5)
        return transcripts._CaptionData(
            "English",
            "en",
            False,
            "first available manually uploaded caption track",
            ((0.0, 1.0, "caption"),),
            135,
        )

    monkeypatch.setattr(transcripts, "_fetch_original", fetch)
    reader = TranscriptReader()
    one = asyncio.create_task(reader.get_youtube_transcript(URL))
    two = asyncio.create_task(reader.get_youtube_transcript("https://youtu.be/jNQXAC9IVRw"))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        with pytest.raises(TranscriptError) as error:
            await reader.get_youtube_transcript("https://youtu.be/403abcde789")
        assert error.value.code == "busy"
        one.cancel()
        with pytest.raises(asyncio.CancelledError):
            await one
        closing = asyncio.create_task(reader.aclose())
        await asyncio.sleep(0)
        with pytest.raises(TranscriptError) as error:
            await reader.get_youtube_transcript(URL)
        assert error.value.code == "closed"
    finally:
        release.set()
    await two
    await closing


@pytest.mark.live_provider
@pytest.mark.skipif(
    os.environ.get("READER_RUN_LIVE_YOUTUBE_TRANSCRIPT") != "1",
    reason="explicit live opt-in",
)
@pytest.mark.asyncio
async def test_live_public_video_returns_caption_text() -> None:
    result = await TranscriptReader().get_youtube_transcript(URL)
    assert result["video_id"] == VIDEO
    assert result["segments"] and any(segment["text"].strip() for segment in result["segments"])

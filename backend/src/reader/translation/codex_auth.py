"""Reader-owned Codex credentials: atomic rotation under a cross-process lock.

The store is deliberately separate from Codex CLI's live login. Import a fresh
login once; Reader owns its refresh chain afterwards. Reads never do network I/O.
"""

import base64
import json
import math
import os
import stat
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
from filelock import FileLock, Timeout

TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
REFRESH_SKEW_SECONDS = 120
REFRESH_TIMEOUT_SECONDS = 15
LOCK_TIMEOUT_SECONDS = 45
MAX_AUTH_BYTES = 1_048_576


class CodexAuthenticationError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class CodexRefreshError(RuntimeError):
    """Sanitized, retryable refresh failure; never retains a request or token."""

    def __init__(self, status_code: int = 503):
        super().__init__("Codex credential refresh temporarily unavailable")
        self.status_code = status_code
        self.response = httpx.Response(status_code, headers={"retry-after": "30"})


def _path(auth_file: Path | None) -> Path:
    if auth_file is None:
        raise CodexAuthenticationError("missing_codex_subscription_auth_file")
    # Canonical directory keeps relative aliases on the same lock. Symlinked
    # credential files are rejected, rather than replacing an unexpected target.
    path = auth_file.expanduser().absolute()
    if path.is_symlink():
        raise CodexAuthenticationError("invalid_codex_subscription_auth")
    return path.parent.resolve() / path.name


def _claims(auth: dict) -> tuple[dict, float]:
    try:
        if auth["auth_mode"] != "chatgpt":
            raise ValueError
        token = auth["tokens"]["access_token"]
        claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "==="))
        expiry = float(claims["exp"])
        if not math.isfinite(expiry):
            raise ValueError
        return claims, expiry
    except (ValueError, KeyError, TypeError, IndexError, AttributeError, OverflowError):
        raise CodexAuthenticationError("invalid_codex_subscription_auth") from None


def _headers(auth: dict) -> dict[str, str]:
    claims, _ = _claims(auth)
    try:
        tokens = auth["tokens"]
        account = claims.get("https://api.openai.com/auth", {})
        headers = {
            "Authorization": "Bearer " + tokens["access_token"],
            "ChatGPT-Account-ID": tokens.get("account_id") or account.get("chatgpt_account_id"),
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": "Reader/0.1.0",
            "originator": "reader",
        }
        residency = account.get("chatgpt_data_residency") or account.get(
            "chatgpt_compute_residency"
        )
        if residency:
            headers["x-openai-internal-codex-residency"] = residency
        if any(
            not isinstance(v, str) or not v or any(ord(c) < 32 or ord(c) > 126 for c in v)
            for v in headers.values()
        ):
            raise ValueError
        return headers
    except (ValueError, KeyError, TypeError, AttributeError):
        raise CodexAuthenticationError("invalid_codex_subscription_auth") from None


def _read(path: Path) -> dict:
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size > MAX_AUTH_BYTES
            or (os.name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o077)
        ):
            raise ValueError
        auth = json.loads(path.read_text(encoding="utf-8"))
        _headers(auth)
        if auth.get("reader_auth_version") != 1:
            raise CodexAuthenticationError("unmanaged_codex_subscription_auth")
        state = auth.get("reader_refresh", {})
        if not isinstance(state, dict) or state.get("status") not in {
            None,
            "pending",
            "retry",
            "rejected",
            "uncertain",
        }:
            raise ValueError
        retry_at = state.get("retry_at", 0)
        if not isinstance(retry_at, (float, int)) or not math.isfinite(retry_at):
            raise ValueError
        if state.get("status") in {"rejected", "uncertain"}:
            raise CodexAuthenticationError("codex_subscription_reauth_required")
        return auth
    except (OSError, ValueError, TypeError, AttributeError):
        raise CodexAuthenticationError("invalid_codex_subscription_auth") from None


def _has_refresh(auth: dict) -> bool:
    refresh = auth["tokens"].get("refresh_token")
    return isinstance(refresh, str) and bool(refresh.strip())


def subscription_unavailable_reason(auth_file: Path | None) -> str | None:
    """Catalog inspection is local only. An expired refreshable login is usable."""
    try:
        auth = _read(_path(auth_file))
        if _claims(auth)[1] <= time.time() and not _has_refresh(auth):
            return "expired_codex_subscription_auth"
    except CodexAuthenticationError as exc:
        return exc.reason
    return None


@contextmanager
def _store_lock(path: Path):
    try:
        with FileLock(str(path) + ".lock", timeout=LOCK_TIMEOUT_SECONDS, mode=0o600):
            yield
    except Timeout:
        raise CodexRefreshError() from None
    except OSError:
        raise CodexAuthenticationError("codex_subscription_auth_not_writable") from None


def _save(path: Path, auth: dict) -> None:
    """Write privately, fsync, replace, then fsync the directory (same filesystem)."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".codex-auth-", delete=False
        ) as stream:
            temporary = stream.name
            if os.name == "posix":
                os.fchmod(stream.fileno(), 0o600)
            json.dump(auth, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except OSError:
        raise CodexAuthenticationError("codex_subscription_auth_not_writable") from None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def import_subscription_auth(auth_file: Path | None, data: bytes, *, replace: bool = False) -> None:
    """Explicit one-time bootstrap. Never discovers or rewrites the CLI's store."""
    path = _path(auth_file)
    try:
        if len(data) > MAX_AUTH_BYTES:
            raise ValueError
        source = json.loads(data)
        _headers(source)
        if not _has_refresh(source):
            raise ValueError
        auth = {"auth_mode": "chatgpt", "tokens": source["tokens"], "reader_auth_version": 1}
        # Keep Codex's metadata, but never import another store's refresh state.
        if "last_refresh" in source:
            auth["last_refresh"] = source["last_refresh"]
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "posix" and stat.S_IMODE(path.parent.stat().st_mode) & 0o077:
            raise CodexAuthenticationError("codex_subscription_auth_not_writable")
    except (OSError, ValueError, TypeError):
        raise CodexAuthenticationError("invalid_codex_subscription_auth") from None
    with _store_lock(path):
        if path.exists():
            if not replace:
                raise CodexAuthenticationError("codex_subscription_auth_already_exists")
            try:
                previous = json.loads(path.read_text(encoding="utf-8"))
                failed = previous.get("reader_refresh", {}).get("status") in {
                    "pending",
                    "rejected",
                    "uncertain",
                }
                if (
                    failed
                    and previous["tokens"]["refresh_token"] == auth["tokens"]["refresh_token"]
                ):
                    raise CodexAuthenticationError("codex_subscription_import_requires_fresh_login")
            except (ValueError, KeyError, TypeError, AttributeError):
                pass  # An explicitly replaced corrupt store must be recoverable.
        _save(path, auth)


def subscription_auth_status(auth_file: Path | None) -> dict:
    reason = subscription_unavailable_reason(auth_file)
    result = {"available": reason is None, "reason": reason}
    if reason is None:
        auth = _read(_path(auth_file))
        result.update(expires_at=_claims(auth)[1], refreshable=_has_refresh(auth))
    return result


def _record_failure(path: Path, auth: dict, *, terminal: bool, uncertain: bool = False) -> None:
    auth["reader_refresh"] = {
        "status": "uncertain" if uncertain else "rejected" if terminal else "retry"
    }
    if not terminal:
        auth["reader_refresh"]["retry_at"] = time.time() + 30
    _save(path, auth)


def _refresh(path: Path, auth: dict) -> dict:
    # If the process dies after exchanging a single-use refresh token, do not
    # replay that token at restart. An unfinished marker requires a fresh login.
    auth["reader_refresh"] = {"status": "pending"}
    _save(path, auth)
    try:
        deadline = time.monotonic() + REFRESH_TIMEOUT_SECONDS
        with httpx.Client(timeout=5, follow_redirects=False) as client:
            with client.stream(
                "POST",
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": CLIENT_ID,
                    "refresh_token": auth["tokens"]["refresh_token"],
                },
                headers={"Accept": "application/json", "User-Agent": "Reader/0.1.0"},
            ) as exchange:
                content = bytearray()
                for chunk in exchange.iter_bytes():
                    content.extend(chunk)
                    # A slow-drip or oversized response must not hold the lock
                    # indefinitely. Socket operations have a separate 5s bound.
                    if len(content) > MAX_AUTH_BYTES or time.monotonic() > deadline:
                        raise httpx.ReadError("Codex refresh response exceeded limits")
                response = httpx.Response(exchange.status_code, content=bytes(content))
    except (httpx.ConnectError, httpx.ConnectTimeout):
        _record_failure(path, auth, terminal=False)
        raise CodexRefreshError() from None
    except httpx.TransportError:
        # The server may have consumed the token even though no reply arrived.
        _record_failure(path, auth, terminal=True, uncertain=True)
        raise CodexAuthenticationError("codex_subscription_reauth_required") from None
    if response.status_code != 200:
        transient = response.status_code in {408, 409, 425, 429} or response.status_code >= 500
        _record_failure(path, auth, terminal=not transient)
        if transient:
            raise CodexRefreshError(429 if response.status_code == 429 else 503)
        raise CodexAuthenticationError("codex_subscription_reauth_required")
    try:
        if len(response.content) > MAX_AUTH_BYTES:
            raise ValueError
        payload = response.json()
        updated = {**auth, "tokens": {**auth["tokens"]}}
        for key in ("access_token", "refresh_token", "id_token"):
            if key in payload:
                if not isinstance(payload[key], str) or not payload[key].strip():
                    raise ValueError
                updated["tokens"][key] = payload[key]
        if "access_token" not in payload or _claims(updated)[1] <= time.time():
            raise ValueError
        # Account identity must not silently change during refresh.
        if _headers(updated)["ChatGPT-Account-ID"] != _headers(auth)["ChatGPT-Account-ID"]:
            raise ValueError
        refreshed_account = (
            _claims(updated)[0].get("https://api.openai.com/auth", {}).get("chatgpt_account_id")
        )
        if refreshed_account and refreshed_account != _headers(auth)["ChatGPT-Account-ID"]:
            raise ValueError
    except (ValueError, KeyError, TypeError, CodexAuthenticationError):
        _record_failure(path, auth, terminal=True, uncertain=True)
        raise CodexAuthenticationError("codex_subscription_reauth_required") from None
    updated.pop("reader_refresh", None)
    updated["last_refresh"] = datetime.now(UTC).isoformat()
    _save(path, updated)
    return updated


def subscription_headers(
    auth_file: Path | None, *, rejected_access_token: str | None = None
) -> dict[str, str]:
    """Refresh near expiry, or once after a 401, adopting peer rotations first."""
    path = _path(auth_file)
    auth = _read(path)

    def needs_refresh(current: dict) -> bool:
        return (
            _claims(current)[1] <= time.time() + REFRESH_SKEW_SECONDS
            or current["tokens"]["access_token"] == rejected_access_token
            or current.get("reader_refresh", {}).get("status") == "pending"
        )

    if not needs_refresh(auth):
        return _headers(auth)
    with _store_lock(path):
        auth = _read(path)  # Another API/Worker may already have rotated it.
        if not needs_refresh(auth):
            return _headers(auth)
        state = auth.get("reader_refresh", {})
        if state.get("status") == "pending":
            _record_failure(path, auth, terminal=True, uncertain=True)
            raise CodexAuthenticationError("codex_subscription_reauth_required")
        may_use_current = _claims(auth)[1] > time.time() and rejected_access_token is None
        if state.get("retry_at", 0) > time.time():
            if may_use_current:
                return _headers(auth)
            raise CodexRefreshError()
        if not _has_refresh(auth):
            raise CodexAuthenticationError("expired_codex_subscription_auth")
        try:
            return _headers(_refresh(path, auth))
        except CodexRefreshError:
            if _claims(auth)[1] > time.time() and rejected_access_token is None:
                return _headers(auth)
            raise

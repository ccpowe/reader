"""Reader protocol v2 discovery, protected by the deployment access token."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_serializer

from app.core.configuration import reader_configuration_missing
from app.core.server_access import document_server_token
from app.core.settings import Settings, get_settings

router = APIRouter(tags=["discovery"], dependencies=[Depends(document_server_token)])


class ReaderDiscoveryResponse(BaseModel):
    """The versioned public contract consumed by Reader clients."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal[2]
    server_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^\S(?:.*\S)?$",
    )
    api_base_url: AnyHttpUrl

    @field_serializer("api_base_url")
    def serialize_base_url(self, value: AnyHttpUrl) -> str:
        return _normalise_base_url(str(value))


DISCOVERY_CONFIGURATION_ERROR = "reader_discovery_configuration_incomplete"


class ReaderDiscoveryConfigurationError(BaseModel):
    """Stable 503 payload when a deployment has not configured discovery."""

    model_config = ConfigDict(extra="forbid")

    code: Literal["reader_discovery_configuration_incomplete"]
    error_code: Literal["reader_discovery_configuration_incomplete"]
    missing: list[str]
    status: Literal["unavailable"]


def _missing_settings(settings: Settings) -> list[str]:
    """Return all missing public-discovery settings in stable order."""
    return reader_configuration_missing(settings)


def _configuration_error(missing: list[str]) -> JSONResponse:
    # Include both ``code`` and ``error_code`` while clients roll forward from
    # the first implementation.  Both values are stable and contain no
    # deployment details or credential material.
    detail = {
        "code": DISCOVERY_CONFIGURATION_ERROR,
        "error_code": DISCOVERY_CONFIGURATION_ERROR,
        "missing": missing,
        "status": "unavailable",
    }
    # Return the model at the top level. FastAPI's HTTPException convention
    # would wrap it in {"detail": ...}, which is not the discovery contract.
    return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=detail)


def _normalise_base_url(value: str) -> str:
    """Remove only the separator slash, retaining a configured path prefix."""

    normalised = value.rstrip("/")
    return normalised or value


def _request_base_url(request: Request) -> str:
    """Resolve the externally reachable API base from ASGI routing metadata.

    ``Request.base_url`` includes FastAPI's configured ``root_path``.  Proxy
    headers are intentionally not interpreted here: deployments that sit
    behind a reverse proxy should enable Uvicorn's trusted forwarded-header
    configuration (or an equivalent trusted middleware), after which
    ``base_url`` already contains the public scheme/host/prefix.
    """

    return _normalise_base_url(str(request.base_url))


@router.get(
    "/.well-known/reader.json",
    response_model=ReaderDiscoveryResponse,
    name="reader_discovery",
    summary="Discover the public Reader runtime configuration",
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ReaderDiscoveryConfigurationError,
            "description": "Required public discovery configuration is missing.",
        },
    },
    description=(
        "Returns the stable server identity and API base URL. "
        "Requires the deployment access token in X-Reader-Server-Token."
    ),
)
async def reader_discovery(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> ReaderDiscoveryResponse | JSONResponse:
    """Return this deployment's public Reader runtime coordinates."""

    missing = _missing_settings(settings)
    if missing:
        return _configuration_error(missing)

    assert settings.server_id is not None
    api_base_url = (
        _normalise_base_url(str(settings.public_api_base_url))
        if settings.public_api_base_url is not None
        else _request_base_url(request)
    )
    return ReaderDiscoveryResponse(
        api_base_url=api_base_url,
        protocol_version=2,
        server_id=settings.server_id.strip(),
    )


__all__ = [
    "DISCOVERY_CONFIGURATION_ERROR",
    "ReaderDiscoveryConfigurationError",
    "ReaderDiscoveryResponse",
    "router",
    "reader_discovery",
]

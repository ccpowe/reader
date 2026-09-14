"""Common page-oriented contract implemented by source scanners."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.ingestion.models import SourceScanPage, SourceScanRequest


class SourceAdapter(ABC):
    @abstractmethod
    async def scan_page(self, request: SourceScanRequest) -> SourceScanPage:
        """Fetch one replayable provider page without writing to the database."""

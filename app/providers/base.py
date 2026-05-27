"""Abstract base class for all AI provider implementations."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, ClassVar

logger = logging.getLogger(__name__)


class AIProvider(ABC):
    """Base class for every AI provider.

    Concrete subclasses implement ``generate_json`` for their specific API.
    All providers expose a uniform interface so callers never depend on a
    specific SDK or endpoint structure.
    """

    #: Stable identifier used in the registry and stored in the DB.
    name: ClassVar[str]
    #: Human-readable label shown in the UI.
    label: ClassVar[str]
    #: Default model name used when none is supplied.
    default_model: ClassVar[str]

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        timeout_seconds: int = 60,
        concurrency: int = 5,
    ) -> None:
        self.api_key = api_key
        self.model = model or self.default_model
        self.timeout_seconds = timeout_seconds
        self.concurrency = concurrency

    # ------------------------------------------------------------------
    # Core interface
    # ------------------------------------------------------------------

    @abstractmethod
    async def generate_json(
        self,
        system_prompt: str,
        user_text: str,
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Send a prompt and parse the response as JSON.

        Returns the parsed dict, or ``None`` on any error (never raises).
        Implementations must handle rate-limits, timeouts, and HTTP errors
        internally, logging warnings rather than propagating exceptions.
        """
        ...

    @abstractmethod
    async def generate_json_with_pdf(
        self,
        system_prompt: str,
        user_text: str,
        pdf_bytes: bytes,
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Like ``generate_json`` but attaches a raw PDF for visual parsing.

        Not all providers support this.  Implementations that do not should
        log a warning and return ``None``.
        """
        ...

    async def generate_json_with_images(
        self,
        system_prompt: str,
        user_text: str,
        images: list[bytes],
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Like ``generate_json`` but attaches one or more PNG images.

        Default implementation falls back to plain text (ignores images) so
        subclasses only override when their API supports vision.
        Providers that do support vision SHOULD override this.
        """
        logger.warning(
            "[%s] vision not implemented — falling back to text-only explanation",
            self.name,
        )
        return await self.generate_json(system_prompt, user_text, response_schema)

    # ------------------------------------------------------------------
    # Helpers shared by all subclasses
    # ------------------------------------------------------------------

    def _log_rate_limit(self) -> None:
        logger.warning("[%s] rate limit hit (429)", self.name)

    def _log_timeout(self) -> None:
        logger.warning("[%s] request timed out after %ds", self.name, self.timeout_seconds)

    def _log_http_error(self, status: int, body: str) -> None:
        logger.warning("[%s] HTTP %d — %s", self.name, status, body[:300])

    def _log_parse_error(self, raw: str) -> None:
        logger.warning("[%s] could not parse JSON — first 300 chars: %r", self.name, raw[:300])

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} model={self.model!r}>"

"""The one place that talks to the Claude API."""

from __future__ import annotations

import logging
from typing import Protocol

import anthropic
from pydantic import ValidationError

from commit_roaster.config import Settings
from commit_roaster.diff import Diff
from commit_roaster.exceptions import (
    ConfigError,
    RoastError,
    RoastIncompleteError,
    RoastRefusedError,
)
from commit_roaster.models import RoastResult, SpiceLevel
from commit_roaster.prompts import PromptBuilder

logger = logging.getLogger(__name__)

# Server-side refusal fallback: if the model's safety classifier declines the request,
# the API re-runs it on Anthropic's recommended fallback model inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

_AUTH_HELP = (
    "No Anthropic credentials found. Set ANTHROPIC_API_KEY "
    "(get a key at https://platform.claude.com/settings/keys)."
)


class SupportsRoast(Protocol):
    """Anything that can turn a diff into a :class:`RoastResult` (lets the CLI use fakes)."""

    def roast(self, diff: Diff, spice: SpiceLevel) -> RoastResult: ...


class Roaster:
    """Sends a diff to Claude and returns a validated :class:`RoastResult`."""

    def __init__(self, client: anthropic.Anthropic, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    @classmethod
    def from_settings(cls, settings: Settings) -> Roaster:
        """Create a roaster with a default client (credentials come from the environment)."""
        return cls(anthropic.Anthropic(max_retries=3), settings)

    def roast(self, diff: Diff, spice: SpiceLevel) -> RoastResult:
        prompts = PromptBuilder(spice)
        try:
            response = self._client.beta.messages.parse(
                model=self._settings.model,
                max_tokens=self._settings.max_tokens,
                system=prompts.system_prompt(),
                messages=[{"role": "user", "content": prompts.user_message(diff)}],
                output_format=RoastResult,
                output_config={"effort": self._settings.effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except ValidationError as exc:
            raise RoastIncompleteError(
                "Claude's reply did not match the expected format. Try again."
            ) from exc
        except anthropic.AuthenticationError as exc:
            raise ConfigError(f"Authentication failed: {exc.message}. {_AUTH_HELP}") from exc
        except anthropic.PermissionDeniedError as exc:
            raise RoastError(f"This API key cannot use {self._settings.model}.") from exc
        except anthropic.NotFoundError as exc:
            raise ConfigError(f"Unknown model {self._settings.model!r}.") from exc
        except anthropic.RateLimitError as exc:
            raise RoastError("Rate limited by the Claude API. Wait a moment and retry.") from exc
        except anthropic.APIStatusError as exc:
            raise RoastError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise RoastError("Could not reach the Claude API. Check your connection.") from exc
        except TypeError as exc:
            # The SDK raises TypeError before sending anything when it has no credentials.
            # Check the client's state, not the error text, which the SDK may reword.
            # (Profile and federation logins are resolved per request and never land here.)
            if self._client.api_key is None and self._client.auth_token is None:
                raise ConfigError(_AUTH_HELP) from exc
            raise

        logger.info(
            "request_id=%s model=%s stop_reason=%s input_tokens=%s output_tokens=%s",
            # Public despite the underscore: the SDK documents it for support requests.
            response._request_id,
            response.model,
            response.stop_reason,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )

        if response.stop_reason == "refusal":
            raise RoastRefusedError("Claude declined to roast this diff.")
        if response.stop_reason == "max_tokens":
            raise RoastIncompleteError("Claude's reply was cut off before it finished.")
        result = response.parsed_output
        if result is None:
            raise RoastIncompleteError("Claude returned an empty reply.")
        return result

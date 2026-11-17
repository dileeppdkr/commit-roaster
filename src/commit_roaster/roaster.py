"""The one place that talks to the Claude API."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

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
from commit_roaster.models import (
    RequestTokens,
    ReviewPlan,
    RoastOutcome,
    RoastResult,
    SpiceLevel,
    TokenUsage,
)
from commit_roaster.prompts import PromptBuilder
from commit_roaster.tools import TOOL_DEFINITIONS, RepoTools

logger = logging.getLogger(__name__)

# Server-side refusal fallback: if the model's safety classifier declines the request,
# the API re-runs it on Anthropic's recommended fallback model inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

_AUTH_HELP = (
    "No Anthropic credentials found. Set ANTHROPIC_API_KEY "
    "(get a key at https://platform.claude.com/settings/keys)."
)


class SupportsRoast(Protocol):
    """Anything that can turn a diff into a :class:`RoastOutcome` (lets the CLI use fakes)."""

    def roast(self, diff: Diff, spice: SpiceLevel, plan: ReviewPlan) -> RoastOutcome: ...


@dataclass
class _Totals:
    """Token usage summed over every request in one roast."""

    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    requests: list[RequestTokens] = field(default_factory=list)

    def add(self, response: Any) -> None:
        usage = response.usage
        request = RequestTokens(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_input_tokens or 0,
            cache_write_tokens=usage.cache_creation_input_tokens or 0,
        )
        self.model = response.model
        self.requests.append(request)
        self.input_tokens += request.input_tokens
        self.output_tokens += request.output_tokens
        self.cache_read_tokens += request.cache_read_tokens
        self.cache_write_tokens += request.cache_write_tokens

    def to_usage(self, tool_calls: int) -> TokenUsage:
        return TokenUsage(
            model=self.model,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cache_read_tokens=self.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens,
            tool_calls=tool_calls,
            requests=tuple(self.requests),
        )


class Roaster:
    """Sends a diff to Claude and returns a validated :class:`RoastResult` and its usage.

    A normal review is one request. A deep review (``plan.deep``) lets Claude call
    the read-only :class:`RepoTools` until it has what it needs or the tool budget
    runs out, then asks for the final answer.
    """

    def __init__(
        self, client: anthropic.Anthropic, settings: Settings, tools: RepoTools | None = None
    ) -> None:
        self._client = client
        self._settings = settings
        self._tools = tools

    @classmethod
    def from_settings(cls, settings: Settings, tools: RepoTools | None = None) -> Roaster:
        """Create a roaster with a default client (credentials come from the environment)."""
        return cls(anthropic.Anthropic(max_retries=3), settings, tools)

    def roast(self, diff: Diff, spice: SpiceLevel, plan: ReviewPlan) -> RoastOutcome:
        prompts = PromptBuilder(spice, plan)
        system = prompts.system_prompt()
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompts.user_message(diff)}]
        deep = plan.deep and self._tools is not None
        totals = _Totals()
        tool_calls = 0
        # Every turn but the last makes at least one tool call, so this always ends.
        for _turn in range(plan.max_tool_calls + 2 if deep else 1):
            allow_tools = tool_calls < plan.max_tool_calls
            response = self._send(system, messages, deep=deep, allow_tools=allow_tools)
            totals.add(response)
            if response.stop_reason == "refusal":
                raise RoastRefusedError("Claude declined to roast this diff.")
            if response.stop_reason == "max_tokens":
                raise RoastIncompleteError("Claude's reply was cut off before it finished.")
            if response.stop_reason != "tool_use" or self._tools is None:
                break
            # Append the assistant turn unchanged (thinking blocks included) and answer
            # every tool call in one user message.
            messages.append({"role": "assistant", "content": _as_params(response.content)})
            results: list[dict[str, Any]] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                tool_calls += 1
                if tool_calls > plan.max_tool_calls:
                    text, is_error = "The tool budget is used up.", True
                else:
                    text, is_error = self._tools.run(block.name, block.input)
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": text,
                        "is_error": is_error,
                    }
                )
            if tool_calls >= plan.max_tool_calls:
                results.append(
                    {
                        "type": "text",
                        "text": f"You have used all {plan.max_tool_calls} tool calls. "
                        "Give your final review now.",
                    }
                )
            messages.append({"role": "user", "content": results})
        else:
            raise RoastIncompleteError("Claude kept exploring and never gave a final answer.")

        result = response.parsed_output
        if result is None:
            raise RoastIncompleteError("Claude returned an empty reply.")
        return RoastOutcome(result=result, usage=totals.to_usage(tool_calls))

    def _send(
        self, system: str, messages: list[dict[str, Any]], *, deep: bool, allow_tools: bool
    ) -> Any:
        request: dict[str, Any] = {
            "model": self._settings.model,
            "max_tokens": self._settings.max_tokens,
            "system": system,
            "messages": messages,
            "output_format": RoastResult,
            "output_config": {"effort": self._settings.effort},
            "betas": [FALLBACK_BETA],
            "fallbacks": "default",
        }
        if deep:
            # Same tools on every turn (changing them would break the prompt cache);
            # tool_choice "none" asks for the final answer once the budget is spent.
            # Automatic caching keeps re-reading the growing conversation cheap.
            request["tools"] = TOOL_DEFINITIONS
            request["tool_choice"] = {"type": "auto" if allow_tools else "none"}
            request["cache_control"] = {"type": "ephemeral"}
        try:
            response = self._client.beta.messages.parse(**request)
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
        return response


def _as_params(content: Any) -> list[dict[str, Any]]:
    """Response blocks as plain request params. Parsed text blocks carry an extra
    ``parsed_output`` field that is not part of the API and must not be sent back."""
    params = []
    for block in content:
        param = block.to_dict()
        param.pop("parsed_output", None)
        params.append(param)
    return params

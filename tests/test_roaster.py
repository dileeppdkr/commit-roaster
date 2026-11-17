from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
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
from commit_roaster.roaster import FALLBACK_BETA, Roaster

_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


class FakeMessages:
    def __init__(self, outcome: Any) -> None:
        self._outcome = outcome
        self.kwargs: dict[str, Any] = {}

    def parse(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome


def _client(outcome: Any, api_key: str | None = "sk-ant-test") -> tuple[Any, FakeMessages]:
    messages = FakeMessages(outcome)
    client = SimpleNamespace(
        beta=SimpleNamespace(messages=messages), api_key=api_key, auth_token=None
    )
    return client, messages


def _response(parsed: RoastResult | None, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        parsed_output=parsed,
        stop_reason=stop_reason,
        model="claude-opus-5-5",
        _request_id="req_test",
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
    )


def _status_error(cls: type[anthropic.APIStatusError], status: int) -> anthropic.APIStatusError:
    response = httpx2.Response(status, request=_REQUEST)
    return cls("boom", response=response, body=None)


def test_roast_sends_expected_request(sample_diff: Diff, sample_result: RoastResult) -> None:
    client, messages = _client(_response(sample_result))
    settings = Settings(model="claude-opus-5-5", effort="high")

    result = Roaster(client, settings).roast(sample_diff, SpiceLevel.SAVAGE)

    assert result == sample_result
    kwargs = messages.kwargs
    assert kwargs["model"] == "claude-opus-5-5"
    assert kwargs["output_format"] is RoastResult
    assert kwargs["output_config"] == {"effort": "high"}
    assert kwargs["betas"] == [FALLBACK_BETA]
    assert kwargs["fallbacks"] == "default"
    assert SpiceLevel.SAVAGE.tone in kwargs["system"]
    assert sample_diff.text in kwargs["messages"][0]["content"]


@pytest.mark.parametrize(
    ("stop_reason", "parsed", "error"),
    [
        ("refusal", None, RoastRefusedError),
        ("max_tokens", None, RoastIncompleteError),
        ("end_turn", None, RoastIncompleteError),
    ],
)
def test_roast_handles_stop_reasons(
    sample_diff: Diff, stop_reason: str, parsed: RoastResult | None, error: type[Exception]
) -> None:
    client, _ = _client(_response(parsed, stop_reason))
    with pytest.raises(error):
        Roaster(client, Settings()).roast(sample_diff, SpiceLevel.MILD)


def test_roast_wraps_validation_error(sample_diff: Diff) -> None:
    try:
        RoastResult.model_validate_json("{not json")
    except ValidationError as exc:
        validation_error = exc
    client, _ = _client(validation_error)
    with pytest.raises(RoastIncompleteError, match="expected format"):
        Roaster(client, Settings()).roast(sample_diff, SpiceLevel.MILD)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_status_error(anthropic.AuthenticationError, 401), ConfigError),
        (_status_error(anthropic.PermissionDeniedError, 403), RoastError),
        (_status_error(anthropic.NotFoundError, 404), ConfigError),
        (_status_error(anthropic.RateLimitError, 429), RoastError),
        (_status_error(anthropic.InternalServerError, 500), RoastError),
        (anthropic.APIConnectionError(request=_REQUEST), RoastError),
    ],
)
def test_roast_maps_api_errors(
    sample_diff: Diff, error: BaseException, expected: type[Exception]
) -> None:
    client, _ = _client(error)
    with pytest.raises(expected):
        Roaster(client, Settings()).roast(sample_diff, SpiceLevel.MILD)


@pytest.mark.parametrize("message", ["Could not resolve authentication method.", "reworded"])
def test_type_error_without_credentials_asks_for_key(sample_diff: Diff, message: str) -> None:
    client, _ = _client(TypeError(message), api_key=None)
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        Roaster(client, Settings()).roast(sample_diff, SpiceLevel.MILD)


def test_type_error_with_credentials_is_not_swallowed(sample_diff: Diff) -> None:
    client, _ = _client(TypeError("something else"))
    with pytest.raises(TypeError, match="something else"):
        Roaster(client, Settings()).roast(sample_diff, SpiceLevel.MILD)


def test_from_settings_builds_real_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    roaster = Roaster.from_settings(Settings())
    assert isinstance(roaster, Roaster)

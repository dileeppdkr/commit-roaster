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
from commit_roaster.models import RequestTokens, ReviewPlan, RoastResult, SpiceLevel, TokenUsage
from commit_roaster.roaster import FALLBACK_BETA, Roaster

_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


class FakeMessages:
    """Returns the given outcomes in order (the last one repeats); records every request."""

    def __init__(self, *outcomes: Any) -> None:
        self._outcomes = list(outcomes)
        self.requests: list[dict[str, Any]] = []

    @property
    def kwargs(self) -> dict[str, Any]:
        return self.requests[-1]

    def parse(self, **kwargs: Any) -> Any:
        # Snapshot the messages: the roaster appends to the same list between turns.
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        outcome = self._outcomes.pop(0) if len(self._outcomes) > 1 else self._outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _client(*outcomes: Any, api_key: str | None = "sk-ant-test") -> tuple[Any, FakeMessages]:
    messages = FakeMessages(*outcomes)
    client = SimpleNamespace(
        beta=SimpleNamespace(messages=messages), api_key=api_key, auth_token=None
    )
    return client, messages


def _usage(cache_read: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=100,
        output_tokens=50,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=None,
    )


def _response(
    parsed: RoastResult | None, stop_reason: str = "end_turn", content: list[Any] | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        parsed_output=parsed,
        stop_reason=stop_reason,
        model="claude-opus-5-5",
        content=content or [],
        _request_id="req_test",
        usage=_usage(),
    )


def _status_error(cls: type[anthropic.APIStatusError], status: int) -> anthropic.APIStatusError:
    response = httpx2.Response(status, request=_REQUEST)
    return cls("boom", response=response, body=None)


def test_roast_sends_expected_request(sample_diff: Diff, sample_result: RoastResult) -> None:
    client, messages = _client(_response(sample_result))
    settings = Settings(model="claude-opus-5-5", effort="high")

    outcome = Roaster(client, settings).roast(sample_diff, SpiceLevel.SAVAGE, ReviewPlan())

    assert outcome.result == sample_result
    assert outcome.usage == TokenUsage(
        model="claude-opus-5-5",
        input_tokens=100,
        output_tokens=50,
        requests=(RequestTokens(input_tokens=100, output_tokens=50),),
    )
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
        Roaster(client, Settings()).roast(sample_diff, SpiceLevel.MILD, ReviewPlan())


def test_roast_wraps_validation_error(sample_diff: Diff) -> None:
    try:
        RoastResult.model_validate_json("{not json")
    except ValidationError as exc:
        validation_error = exc
    client, _ = _client(validation_error)
    with pytest.raises(RoastIncompleteError, match="expected format"):
        Roaster(client, Settings()).roast(sample_diff, SpiceLevel.MILD, ReviewPlan())


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
        Roaster(client, Settings()).roast(sample_diff, SpiceLevel.MILD, ReviewPlan())


@pytest.mark.parametrize("message", ["Could not resolve authentication method.", "reworded"])
def test_type_error_without_credentials_asks_for_key(sample_diff: Diff, message: str) -> None:
    client, _ = _client(TypeError(message), api_key=None)
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        Roaster(client, Settings()).roast(sample_diff, SpiceLevel.MILD, ReviewPlan())


def test_type_error_with_credentials_is_not_swallowed(sample_diff: Diff) -> None:
    client, _ = _client(TypeError("something else"))
    with pytest.raises(TypeError, match="something else"):
        Roaster(client, Settings()).roast(sample_diff, SpiceLevel.MILD, ReviewPlan())


def test_from_settings_builds_real_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    roaster = Roaster.from_settings(Settings())
    assert isinstance(roaster, Roaster)


class _Block:
    """Stands in for an SDK content block: attributes plus to_dict()."""

    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)
        self._fields = fields

    def to_dict(self) -> dict[str, Any]:
        return dict(self._fields)


class _FakeTools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def run(self, name: str, tool_input: object) -> tuple[str, bool]:
        self.calls.append((name, tool_input))
        return f"result of {name}", False


def _tool_turn(*ids: str) -> SimpleNamespace:
    blocks = [_Block(type="thinking", thinking="", signature="sig")]
    blocks += [
        _Block(type="tool_use", id=i, name="read_file", input={"path": "a.rb", "start_line": 1})
        for i in ids
    ]
    response = _response(None, stop_reason="tool_use", content=blocks)
    response.usage = _usage(cache_read=1_000)
    return response


def _deep(max_tool_calls: int = 5) -> ReviewPlan:
    return ReviewPlan(deep=True, max_tool_calls=max_tool_calls)


def test_deep_review_runs_tools_then_answers(sample_diff: Diff, sample_result: RoastResult) -> None:
    client, messages = _client(_tool_turn("t1", "t2"), _response(sample_result))
    tools = _FakeTools()
    roaster = Roaster(client, Settings(), tools)  # type: ignore[arg-type]

    outcome = roaster.roast(sample_diff, SpiceLevel.MILD, _deep())

    assert outcome.result == sample_result
    assert len(tools.calls) == 2
    first, second = messages.requests
    assert [t["name"] for t in first["tools"]] == ["list_files", "read_file", "search_code"]
    assert first["tool_choice"] == {"type": "auto"}
    assert first["cache_control"] == {"type": "ephemeral"}
    assistant, results = second["messages"][1:]
    assert assistant["role"] == "assistant"
    assert assistant["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig"}
    assert [r["tool_use_id"] for r in results["content"]] == ["t1", "t2"]
    assert outcome.usage == TokenUsage(
        model="claude-opus-5-5",
        input_tokens=200,
        output_tokens=100,
        cache_read_tokens=1_000,
        tool_calls=2,
        requests=(RequestTokens(100, 50, cache_read_tokens=1_000), RequestTokens(100, 50)),
    )


def test_deep_review_stops_at_the_tool_budget(
    sample_diff: Diff, sample_result: RoastResult
) -> None:
    client, messages = _client(_tool_turn("t1", "t2"), _response(sample_result))
    tools = _FakeTools()
    Roaster(client, Settings(), tools).roast(  # type: ignore[arg-type]
        sample_diff, SpiceLevel.MILD, _deep(max_tool_calls=1)
    )
    assert len(tools.calls) == 1  # the second call was refused, not run
    results = messages.requests[1]["messages"][-1]["content"]
    assert results[1]["is_error"] is True
    assert results[-1]["text"].startswith("You have used all 1 tool calls")
    assert messages.requests[1]["tool_choice"] == {"type": "none"}


def test_deep_review_that_never_answers_fails(sample_diff: Diff) -> None:
    client, _ = _client(_tool_turn("t"))  # asks for a tool every single turn
    roaster = Roaster(client, Settings(), _FakeTools())  # type: ignore[arg-type]
    with pytest.raises(RoastIncompleteError, match="never gave a final answer"):
        roaster.roast(sample_diff, SpiceLevel.MILD, _deep(max_tool_calls=2))


def test_parsed_output_is_not_sent_back(sample_diff: Diff, sample_result: RoastResult) -> None:
    text = _Block(type="text", text="{}", parsed_output=sample_result)
    turn = _tool_turn("t1")
    turn.content.insert(1, text)
    client, messages = _client(turn, _response(sample_result))
    Roaster(client, Settings(), _FakeTools()).roast(  # type: ignore[arg-type]
        sample_diff, SpiceLevel.MILD, _deep()
    )
    sent = messages.requests[1]["messages"][1]["content"][1]
    assert sent == {"type": "text", "text": "{}"}


def test_normal_review_sends_no_tools(sample_diff: Diff, sample_result: RoastResult) -> None:
    client, messages = _client(_response(sample_result))
    Roaster(client, Settings()).roast(sample_diff, SpiceLevel.MILD, ReviewPlan())
    assert "tools" not in messages.kwargs
    assert "cache_control" not in messages.kwargs

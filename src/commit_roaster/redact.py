"""Keep credentials on this machine.

Anything in the diff that looks like a credential is replaced with a
``[REDACTED ...]`` marker before the diff is sent to the API. The model still
sees that a secret was there, so it can roast it, but never sees the value.

The patterns are for well-known key formats only, so ordinary code is not
mangled by false positives. This is a safety net, not a full secret scanner.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from commit_roaster.patch import split_sections

# (what it is, pattern). Each matches only the secret itself.
_TOKEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Anthropic API key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("OpenAI API key", re.compile(r"\bsk-proj-[A-Za-z0-9_-]{20,}")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})")),
    ("Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("Stripe live key", re.compile(r"\b[sr]k_live_[A-Za-z0-9]{20,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
)
_PRIVATE_KEY = "private key"
_KEY_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_KEY_END = re.compile(r"-----END [A-Z0-9 ]*PRIVATE KEY-----")
# A whole key on one line, e.g. inside a JSON string with "\n" escapes.
_INLINE_KEY = re.compile(f"({_KEY_BEGIN.pattern}).*?({_KEY_END.pattern})")

_ENV_VALUES = "environment file values"
# `KEY=value`, `export KEY=value` or `KEY: value`, after the diff's +/-/space marker.
_ENV_ASSIGNMENT = re.compile(
    r"^([+\- ]\s*(?:export\s+)?[A-Za-z_][\w.-]*\s*[=:]\s*)(\S.*?)(\r?\n)?$"
)
_ENV_TEMPLATES = (".example", ".sample", ".template", ".dist", ".defaults")


@dataclass(frozen=True, slots=True)
class Redaction:
    """One kind of secret removed from one file."""

    path: str | None  # None when it was outside any file, e.g. in a commit message
    kind: str


def _is_env_file(path: str) -> bool:
    """``.env``, ``.env.production``, ``prod.env``, but not ``.env.example``."""
    name = path.rsplit("/", 1)[-1]
    is_env = name == ".env" or name.startswith(".env.") or name.endswith(".env")
    return is_env and not name.endswith(_ENV_TEMPLATES)


def _redact_tokens(line: str, found: set[str]) -> str:
    for kind, pattern in _TOKEN_PATTERNS:
        line, count = pattern.subn(f"[REDACTED {kind}]", line)
        if count:
            found.add(kind)
    line, count = _INLINE_KEY.subn(r"\1[REDACTED]\2", line)
    if count:
        found.add(_PRIVATE_KEY)
    return line


def _redact_lines(
    lines: tuple[str, ...], *, env_file: bool, found: set[str], in_hunk: bool = False
) -> list[str]:
    out: list[str] = []
    in_key = False
    for line in lines:
        if line.startswith("@@"):
            in_hunk = True
        if in_key:
            if _KEY_END.search(line):
                in_key = False
            else:
                # Keep the +/-/space marker so the diff still reads as a diff.
                marker = line[0] if in_hunk and line[:1] in "+- " else ""
                newline = "\n" if line.endswith("\n") else ""
                out.append(f"{marker}[REDACTED]{newline}")
                continue
        elif _KEY_BEGIN.search(line) and not _KEY_END.search(line):
            in_key = True
            found.add(_PRIVATE_KEY)
        if env_file and in_hunk and (match := _ENV_ASSIGNMENT.match(line)):
            prefix, _value, newline = match.groups()
            line = f"{prefix}[REDACTED]{newline or ''}"
            found.add(_ENV_VALUES)
        out.append(_redact_tokens(line, found))
    return out


def redact_file(path: str, text: str) -> tuple[str, tuple[Redaction, ...]]:
    """Like :func:`redact_secrets`, for a plain file's contents rather than a diff."""
    found: set[str] = set()
    # Mark every line as diff context, so the diff rules apply line by line.
    lines = tuple(f" {line}" for line in text.splitlines(keepends=True))
    out = _redact_lines(lines, env_file=_is_env_file(path), found=found, in_hunk=True)
    redacted = "".join(line[1:] for line in out)
    return redacted, tuple(Redaction(path, kind) for kind in sorted(found))


def redact_secrets(diff_text: str) -> tuple[str, tuple[Redaction, ...]]:
    """Return the diff with credentials replaced, and what was replaced where."""
    out: list[str] = []
    redactions: list[Redaction] = []
    for section in split_sections(diff_text):
        found: set[str] = set()
        env_file = section.name is not None and _is_env_file(section.name)
        out.extend(_redact_lines(section.lines, env_file=env_file, found=found))
        redactions.extend(Redaction(section.name, kind) for kind in sorted(found))
    return "".join(out), tuple(redactions)

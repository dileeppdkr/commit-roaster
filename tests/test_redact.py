from __future__ import annotations

import pytest

from commit_roaster.redact import Redaction, redact_file, redact_secrets

# Fake credentials are assembled at runtime so this file itself never contains
# anything that looks like a real key (and roasting this repo doesn't flag it).
AWS_KEY = "AKIA" + "Q" * 16
ANTHROPIC_KEY = "sk-ant-" + "x" * 30
GITHUB_TOKEN = "ghp_" + "a" * 36
KEY_BEGIN = "-----BEGIN " + "PRIVATE KEY-----"
KEY_END = "-----END " + "PRIVATE KEY-----"


def _file_diff(path: str, *added: str) -> str:
    body = "".join(f"+{line}\n" for line in added)
    return (
        f"diff --git a/{path} b/{path}\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        f"+++ b/{path}\n"
        f"@@ -0,0 +1,{len(added)} @@\n"
        f"{body}"
    )


@pytest.mark.parametrize(
    ("secret", "kind"),
    [
        (AWS_KEY, "AWS access key"),
        (ANTHROPIC_KEY, "Anthropic API key"),
        (GITHUB_TOKEN, "GitHub token"),
    ],
)
def test_known_token_is_replaced(secret: str, kind: str) -> None:
    text, redactions = redact_secrets(_file_diff("config/app.py", f'KEY = "{secret}"'))
    assert secret not in text
    assert f'+KEY = "[REDACTED {kind}]"' in text
    assert redactions == (Redaction("config/app.py", kind),)


def test_private_key_block_is_replaced_line_by_line() -> None:
    diff = _file_diff("deploy/id_rsa", KEY_BEGIN, "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC", KEY_END)
    text, redactions = redact_secrets(diff)
    assert "MIIEvQ" not in text
    assert f"+{KEY_BEGIN}\n+[REDACTED]\n+{KEY_END}\n" in text
    assert redactions == (Redaction("deploy/id_rsa", "private key"),)


def test_private_key_on_one_line_is_replaced() -> None:
    line = f'"key": "{KEY_BEGIN}\\nMIIEvQIBADAN\\n{KEY_END}"'
    text, _ = redact_secrets(_file_diff("creds.json", line))
    assert "MIIEvQ" not in text
    assert f"{KEY_BEGIN}[REDACTED]{KEY_END}" in text


def test_env_file_values_are_replaced_but_keys_kept() -> None:
    diff = _file_diff("api/.env.production", "DATABASE_URL=postgres://u:p@db/app", "# note")
    text, redactions = redact_secrets(diff)
    assert "postgres://" not in text
    assert "+DATABASE_URL=[REDACTED]\n+# note\n" in text
    assert redactions == (Redaction("api/.env.production", "environment file values"),)


@pytest.mark.parametrize("path", [".env.example", "config/.env.sample", "src/environment.py"])
def test_env_templates_and_code_are_left_alone(path: str) -> None:
    diff = _file_diff(path, "DATABASE_URL=postgres://localhost/app")
    assert redact_secrets(diff) == (diff, ())


def test_secret_in_commit_message_has_no_file() -> None:
    text, redactions = redact_secrets(f"commit abc123\n\n    Rotate {AWS_KEY}\n")
    assert AWS_KEY not in text
    assert redactions == (Redaction(None, "AWS access key"),)


def test_clean_diff_is_unchanged() -> None:
    diff = _file_diff("app.py", "def add(a, b):", "    return a + b")
    assert redact_secrets(diff) == (diff, ())


def test_line_count_is_preserved() -> None:
    diff = _file_diff("k.pem", KEY_BEGIN, "AAAA", "BBBB", KEY_END) + _file_diff(".env", "A=1")
    text, _ = redact_secrets(diff)
    assert text.count("\n") == diff.count("\n")


def test_redact_file_handles_plain_text() -> None:
    text, redactions = redact_file("deploy/.env", "API_KEY=abc123\n# keep\n")
    assert text == "API_KEY=[REDACTED]\n# keep\n"
    assert redactions == (Redaction("deploy/.env", "environment file values"),)
    key_file = f"{KEY_BEGIN}\nMIIEvQ\n{KEY_END}\n"
    assert redact_file("id_rsa", key_file)[0] == f"{KEY_BEGIN}\n[REDACTED]\n{KEY_END}\n"

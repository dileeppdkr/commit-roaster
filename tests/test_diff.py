from __future__ import annotations

import io

import pytest

from commit_roaster.diff import (
    RevisionRangeDiffSource,
    StagedDiffSource,
    StdinDiffSource,
    exclude_files,
    truncate_at_line,
)
from commit_roaster.exceptions import ConfigError, EmptyDiffError
from tests.fakes import SAMPLE_DIFF, FakeGit


def test_truncate_keeps_short_text() -> None:
    assert truncate_at_line("abc\n", 10) == "abc\n"


def test_truncate_ends_on_line_boundary() -> None:
    assert truncate_at_line("line one\nline two\nline three\n", 15) == "line one\n"


def test_truncate_without_newline_hard_cuts() -> None:
    assert truncate_at_line("x" * 20, 5) == "xxxxx"


def test_staged_source_passes_excludes() -> None:
    git = FakeGit()
    diff = StagedDiffSource(git, ["*.lock"]).load(max_chars=10_000)
    assert diff.text == SAMPLE_DIFF
    assert not diff.truncated
    assert git.diff_calls == [((), True, ("*.lock",))]


def test_range_source_passes_revision() -> None:
    git = FakeGit()
    diff = RevisionRangeDiffSource(git, " HEAD~2..HEAD ").load(max_chars=10_000)
    assert diff.description == "revision range HEAD~2..HEAD"
    assert git.diff_calls == [(("HEAD~2..HEAD",), False, ())]


@pytest.mark.parametrize("bad", ["", "   ", "--output=/tmp/x"])
def test_range_source_rejects_bad_input(bad: str) -> None:
    with pytest.raises(ConfigError):
        RevisionRangeDiffSource(FakeGit(), bad)


def test_stdin_source_reads_stream() -> None:
    diff = StdinDiffSource(io.StringIO(SAMPLE_DIFF)).load(max_chars=10_000)
    assert diff.text == SAMPLE_DIFF
    assert diff.description == "diff from stdin"


_LOCK_SECTION = """\
diff --git a/web/yarn.lock b/web/yarn.lock
index 3333333..4444444 100644
--- a/web/yarn.lock
+++ b/web/yarn.lock
@@ -1 +1 @@
-old
+new
"""

_VENDOR_SECTION = """\
diff --git a/vendor/lib.py b/vendor/lib.py
new file mode 100644
--- /dev/null
+++ b/vendor/lib.py
@@ -0,0 +1 @@
+x = 1
"""

_COMMIT_HEADER = "commit abc123\nAuthor: Someone\n\n    Bump deps\n\n"


def test_stdin_source_drops_excluded_files() -> None:
    piped = _COMMIT_HEADER + _LOCK_SECTION + SAMPLE_DIFF
    diff = StdinDiffSource(io.StringIO(piped), ["*.lock"]).load(max_chars=10_000)
    assert diff.text == _COMMIT_HEADER + SAMPLE_DIFF


def test_exclude_files_matches_directory_prefix() -> None:
    assert exclude_files(_VENDOR_SECTION + SAMPLE_DIFF, ["vendor"]) == SAMPLE_DIFF


def test_exclude_files_ignores_paths_inside_hunks() -> None:
    # A removed line whose text was "-- a/yarn.lock" looks like a file header.
    hunk_mentions_lock = SAMPLE_DIFF + "--- a/yarn.lock\n"
    assert exclude_files(hunk_mentions_lock, ["*.lock"]) == hunk_mentions_lock


def test_exclude_files_keeps_commit_after_excluded_file() -> None:
    piped = _LOCK_SECTION + _COMMIT_HEADER + SAMPLE_DIFF
    assert exclude_files(piped, ["*.lock"]) == _COMMIT_HEADER + SAMPLE_DIFF


# Captured from `git diff --cached` after adding these three files.
_SPECIAL_NAMES_DIFF = """\
diff --git "a/caf\\303\\251.lock" "b/caf\\303\\251.lock"
new file mode 100644
--- /dev/null
+++ "b/caf\\303\\251.lock"
@@ -0,0 +1 @@
+x
diff --git a/my file.lock b/my file.lock
new file mode 100644
--- /dev/null
+++ b/my file.lock\t
@@ -0,0 +1 @@
+x
diff --git "a/tab\\tname.lock" "b/tab\\tname.lock"
new file mode 100644
--- /dev/null
+++ "b/tab\\tname.lock"
@@ -0,0 +1 @@
+x
"""


@pytest.mark.parametrize("patterns", [["*.lock"], ["café.lock", "my file.lock", "tab\tname.lock"]])
def test_exclude_files_handles_quoted_and_spaced_names(patterns: list[str]) -> None:
    assert exclude_files(_SPECIAL_NAMES_DIFF + SAMPLE_DIFF, patterns) == SAMPLE_DIFF


def test_exclude_files_keeps_section_with_unparseable_quoting() -> None:
    broken = 'diff --git "a/\\x.lock" "b/\\x.lock"\n'
    assert exclude_files(broken, ["*.lock"]) == broken


@pytest.mark.parametrize("text", ["--- old.txt\n+++ new.txt\n@@ -1 +1 @@\n-a\n+b\n", ""])
def test_exclude_files_passes_non_git_text_through(text: str) -> None:
    assert exclude_files(text, ["*.txt"]) == text


def test_load_truncates_large_diff() -> None:
    diff = StdinDiffSource(io.StringIO(SAMPLE_DIFF)).load(max_chars=50)
    assert diff.truncated
    assert diff.original_chars == len(SAMPLE_DIFF)
    assert len(diff.text) <= 50


def test_empty_staged_diff_gives_hint() -> None:
    with pytest.raises(EmptyDiffError, match="git add"):
        StagedDiffSource(FakeGit(diff_text="  \n")).load(max_chars=100)


def test_empty_stdin_has_no_hint() -> None:
    with pytest.raises(EmptyDiffError, match=r"in the diff from stdin\.$"):
        StdinDiffSource(io.StringIO("")).load(max_chars=100)

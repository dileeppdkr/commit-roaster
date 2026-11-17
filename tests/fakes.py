"""Hand-written test doubles shared across test modules."""

from __future__ import annotations

from collections.abc import Sequence

from commit_roaster.diff import Diff
from commit_roaster.git import GitClient
from commit_roaster.models import RoastResult, SpiceLevel

SAMPLE_DIFF = """\
diff --git a/app.py b/app.py
index 1111111..2222222 100644
--- a/app.py
+++ b/app.py
@@ -1,2 +1,3 @@
 def add(a, b):
+    print("DEBUG", a, b)
     return a + b
"""


class FakeGit(GitClient):
    """In-memory stand-in for GitClient."""

    def __init__(self, diff_text: str = SAMPLE_DIFF) -> None:
        super().__init__()
        self.diff_text = diff_text
        self.diff_calls: list[tuple[tuple[str, ...], bool, tuple[str, ...]]] = []
        self.commits: list[str] = []

    def ensure_repository(self) -> None:
        return None

    def diff(
        self, *revisions: str, staged: bool = False, exclude_patterns: Sequence[str] = ()
    ) -> str:
        self.diff_calls.append((revisions, staged, tuple(exclude_patterns)))
        return self.diff_text

    def commit(self, message: str) -> str:
        self.commits.append(message)
        return "[main abc1234] committed"


class FakeRoaster:
    def __init__(self, result: RoastResult) -> None:
        self.result = result
        self.calls: list[tuple[Diff, SpiceLevel]] = []

    def roast(self, diff: Diff, spice: SpiceLevel) -> RoastResult:
        self.calls.append((diff, spice))
        return self.result

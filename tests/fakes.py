"""Hand-written test doubles shared across test modules."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from commit_roaster.diff import Diff
from commit_roaster.exceptions import GitCommandError
from commit_roaster.git import GitClient
from commit_roaster.models import ReviewPlan, RoastOutcome, RoastResult, SpiceLevel, TokenUsage

FAKE_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
SAMPLE_USAGE = TokenUsage(model="claude-haiku-5-5", input_tokens=1_200, output_tokens=300)

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

    def __init__(
        self,
        diff_text: str = SAMPLE_DIFF,
        *,
        root: Path | None = None,
        files: Sequence[str] = (),
    ) -> None:
        super().__init__()
        self.diff_text = diff_text
        self.root = root  # None: behave as if not inside a repository
        self.files = list(files)
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

    def staged_tree(self) -> str:
        return FAKE_TREE

    def toplevel(self) -> Path:
        if self.root is None:
            raise GitCommandError("git rev-parse failed (exit 128): not a git repository")
        return self.root

    def ls_files(self, pathspec: str = "") -> list[str]:
        return list(self.files)


class FakeRoaster:
    def __init__(self, result: RoastResult) -> None:
        self.result = result
        self.calls: list[tuple[Diff, SpiceLevel]] = []
        self.plans: list[ReviewPlan] = []

    def roast(self, diff: Diff, spice: SpiceLevel, plan: ReviewPlan) -> RoastOutcome:
        self.calls.append((diff, spice))
        self.plans.append(plan)
        return RoastOutcome(self.result, SAMPLE_USAGE)

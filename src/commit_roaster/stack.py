"""Which stack a repository uses, and what to check for in each one.

Detection only looks at file names (plus a peek inside ``package.json`` and the
Java build file), so it is fast and never sends anything anywhere. The
checklists are short on purpose: they point Claude at the classic mistakes of
each stack, they are not a full rulebook.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from commit_roaster.exceptions import ConfigError
from commit_roaster.models import ReviewFocus

F = ReviewFocus

STACK_NAMES: dict[str, str] = {
    "rails": "Ruby on Rails",
    "ruby": "Ruby",
    "react": "React",
    "angular": "Angular",
    "node": "JavaScript/TypeScript",
    "laravel": "Laravel",
    "php": "PHP",
    "django": "Django",
    "python": "Python",
    "spring": "Spring (Java/Kotlin)",
    "java": "Java/Kotlin",
    "go": "Go",
    "dotnet": ".NET",
}
# A framework implies its language; listing both would repeat the language's checks.
_IMPLIES = {
    "rails": "ruby",
    "laravel": "php",
    "django": "python",
    "spring": "java",
    "react": "node",
    "angular": "node",
}
_MAX_FILES_TO_READ = 50  # small files, read only until a framework is found

STACK_CHECKS: dict[str, dict[ReviewFocus, tuple[str, ...]]] = {
    "rails": {
        F.SQL: (
            "String interpolation in where, order, pluck, find_by_sql or execute instead of "
            "bind parameters or hash conditions.",
            "Migrations: no index on a new foreign key, NOT NULL or unique rules only in the "
            "model, or long locks on big tables.",
        ),
        F.PERFORMANCE: (
            "N+1 queries: associations used inside each/map, views or serializers without "
            "includes, preload or eager_load.",
            "Model.all.each on large tables instead of find_each; count instead of size on "
            "loaded associations.",
        ),
        F.SECURITY: (
            "permit! or permitting role, admin or owner fields in strong parameters.",
            "html_safe or raw on user content; send, constantize or public_send with user "
            "input; controller actions missing an authorization check.",
        ),
        F.BEST_PRACTICES: (
            "Business logic in controllers; callbacks that send email or call APIs; "
            "multi-record writes without a transaction.",
        ),
        F.SIDE_EFFECTS: (
            "Changed callbacks, validations, default_scope or concerns change behavior for "
            "every caller.",
        ),
    },
    "ruby": {
        F.BUGS: ("rescue => e that swallows errors; mutating frozen or shared constants.",),
    },
    "react": {
        F.BUGS: (
            "Stale closures in effects and timers; effects without cleanup for subscriptions "
            "or timers.",
        ),
        F.PERFORMANCE: (
            "useEffect, useMemo or useCallback dependency arrays that miss values or loop.",
            "Missing or index-based keys in lists; new object or function props that defeat "
            "memoization.",
        ),
        F.SECURITY: (
            "dangerouslySetInnerHTML with unsanitized data; secrets in client-side env "
            "variables that end up in the bundle.",
        ),
        F.BEST_PRACTICES: (
            "State copied from props; direct state mutation; effects that should be event "
            "handlers.",
        ),
    },
    "angular": {
        F.PERFORMANCE: (
            "Subscriptions never unsubscribed (prefer the async pipe or takeUntilDestroyed).",
            "Function calls in templates; *ngFor without trackBy on large lists.",
        ),
        F.SECURITY: ("bypassSecurityTrust* used with user-controlled data.",),
    },
    "node": {
        F.BUGS: ("Floating promises (missing await); errors swallowed in catch blocks.",),
        F.SECURITY: ("Shell commands, file paths or regexes built from user input.",),
    },
    "laravel": {
        F.SQL: ("DB::raw, whereRaw, selectRaw or orderByRaw with interpolated input.",),
        F.PERFORMANCE: ("N+1 queries: relations used in loops without with() or load().",),
        F.SECURITY: (
            "Mass assignment: $guarded = [] or sensitive fields in $fillable.",
            "Unescaped {!! !!} output in Blade; controller actions without a policy or gate.",
        ),
    },
    "php": {
        F.SQL: ("Queries built by string concatenation instead of prepared statements.",),
        F.SECURITY: ("unserialize, eval or include with user input.",),
    },
    "django": {
        F.SQL: ("raw(), extra() or cursor.execute with f-strings or % formatting.",),
        F.PERFORMANCE: (
            "N+1 queries: related objects used in loops without select_related or "
            "prefetch_related.",
        ),
        F.SECURITY: ("csrf_exempt, DEBUG=True or a wide ALLOWED_HOSTS; |safe on user data.",),
    },
    "python": {
        F.BUGS: ("Mutable default arguments; bare except or except Exception: pass.",),
        F.SECURITY: (
            "subprocess with shell=True, pickle or yaml.load on untrusted data, requests "
            "without a timeout.",
        ),
    },
    "spring": {
        F.PERFORMANCE: (
            "N+1 queries from lazy associations in loops; missing fetch join or @EntityGraph.",
        ),
        F.BUGS: ("@Transactional on private methods or called from the same class.",),
        F.SQL: ("String concatenation in JPQL or native queries.",),
    },
    "java": {
        F.BUGS: (
            "equals without hashCode; resources not closed with try-with-resources; "
            "swallowed exceptions.",
        ),
    },
    "go": {
        F.BUGS: (
            "Ignored errors; goroutine leaks; writes to a nil map; loops that capture the "
            "loop variable in old Go versions.",
        ),
        F.SQL: ("fmt.Sprintf to build SQL instead of query parameters.",),
        F.PERFORMANCE: ("Queries or HTTP calls inside loops; missing context timeouts.",),
    },
    "dotnet": {
        F.SQL: ("FromSqlRaw or ExecuteSqlRaw with interpolation (use FromSqlInterpolated).",),
        F.PERFORMANCE: (
            "N+1 queries with lazy loading or a missing Include; .Result or .Wait() on async code.",
        ),
        F.BUGS: ("async void methods; IDisposable objects not disposed.",),
    },
}


def parse_stacks(value: str) -> tuple[str, ...]:
    """Validate a comma-separated list of stack names (``--stack`` or ``ROAST_STACK``)."""
    stacks = tuple(name.strip().lower() for name in value.split(",") if name.strip())
    unknown = [name for name in stacks if name not in STACK_NAMES]
    if unknown:
        raise ConfigError(
            f"Unknown stack {', '.join(unknown)}. Choose from: {', '.join(STACK_NAMES)}."
        )
    return stacks


def detect_stacks(paths: Iterable[str], read: Callable[[str], str | None]) -> tuple[str, ...]:
    """Guess the stacks from a repository's file names.

    ``read`` returns a small file's text, or None if it can't be read. It is only
    called for ``package.json`` and Java build files, shallowest first, and only
    until React or Spring is found (at most 50 reads).
    """
    found: set[str] = set()
    to_read: list[tuple[str, str]] = []  # (path, what it can reveal)
    for path in paths:
        if "node_modules/" in path or path.startswith("vendor/"):
            continue
        name = path.rsplit("/", 1)[-1]
        if name == "Gemfile":
            found.add("ruby")
        elif path.endswith(("config/application.rb", "bin/rails")):
            found.add("rails")
        elif name == "package.json":
            found.add("node")
            to_read.append((path, "react"))
        elif name == "angular.json":
            found.add("angular")
        elif name == "composer.json":
            found.add("php")
        elif name == "artisan":
            found.add("laravel")
        elif name in {"pyproject.toml", "requirements.txt", "setup.py", "Pipfile"}:
            found.add("python")
        elif name == "manage.py":
            found.add("django")
        elif name in {"pom.xml", "build.gradle", "build.gradle.kts"}:
            found.add("java")
            to_read.append((path, "spring"))
        elif name == "go.mod":
            found.add("go")
        elif name.endswith((".csproj", ".sln", ".fsproj")):
            found.add("dotnet")
    # Shallow files first (the root and top-level apps of a monorepo), and stop reading
    # for a framework once it is found, so the read limit is spent where it helps.
    to_read.sort(key=lambda item: item[0].count("/"))
    reads = 0
    for path, stack in to_read:
        if stack in found:
            continue
        if reads == _MAX_FILES_TO_READ:
            break
        reads += 1
        text = read(path) or ""
        if stack == "react" and '"react"' in text:
            found.add("react")
        elif stack == "spring" and "spring-boot" in text:
            found.add("spring")
    found -= {_IMPLIES[stack] for stack in found if stack in _IMPLIES}
    return tuple(stack for stack in STACK_NAMES if stack in found)


def stack_checklist(
    stacks: Iterable[str], focus: Iterable[ReviewFocus]
) -> list[tuple[str, ReviewFocus, str]]:
    """(stack, area, what to check) for every stack and chosen area, frameworks first."""
    wanted = list(focus) or list(ReviewFocus)
    expanded: list[str] = []
    for stack in stacks:
        for name in (stack, _IMPLIES.get(stack)):
            if name and name not in expanded:
                expanded.append(name)
    return [
        (stack, area, check)
        for stack in expanded
        for area in wanted
        for check in STACK_CHECKS.get(stack, {}).get(area, ())
    ]

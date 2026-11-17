from __future__ import annotations

import pytest

from commit_roaster.exceptions import ConfigError
from commit_roaster.models import ReviewFocus
from commit_roaster.stack import detect_stacks, parse_stacks, stack_checklist


def _no_read(_path: str) -> str | None:
    return None


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["Gemfile", "config/application.rb", "app/models/user.rb"], ("rails",)),
        (["Gemfile", "lib/gem.rb"], ("ruby",)),
        (["composer.json", "artisan"], ("laravel",)),
        (["pyproject.toml", "manage.py"], ("django",)),
        (["requirements.txt"], ("python",)),
        (["go.mod", "cmd/main.go"], ("go",)),
        (["src/App/App.csproj"], ("dotnet",)),
        (["package.json", "angular.json"], ("angular",)),
        (["backend/Gemfile", "backend/bin/rails", "web/package.json"], ("rails", "node")),
        (["node_modules/react/package.json"], ()),
        (["README.md"], ()),
    ],
)
def test_detects_stacks_from_file_names(paths: list[str], expected: tuple[str, ...]) -> None:
    assert detect_stacks(paths, _no_read) == expected


def test_reads_package_json_to_spot_react() -> None:
    files = {"web/package.json": '{"dependencies": {"react": "^19.0.0"}}'}
    assert detect_stacks(["web/package.json"], files.get) == ("react",)


def test_reads_build_file_to_spot_spring() -> None:
    files = {"pom.xml": "<artifactId>spring-boot-starter-web</artifactId>"}
    assert detect_stacks(["pom.xml"], files.get) == ("spring",)


def test_checklist_follows_focus_and_includes_the_language() -> None:
    checks = stack_checklist(["rails"], [ReviewFocus.SQL, ReviewFocus.BUGS])
    assert {(stack, area) for stack, area, _ in checks} == {
        ("rails", ReviewFocus.SQL),
        ("ruby", ReviewFocus.BUGS),
    }
    assert any("find_by_sql" in check for _, _, check in checks)


def test_checklist_without_focus_covers_every_area() -> None:
    areas = {area for _, area, _ in stack_checklist(["rails"], [])}
    assert ReviewFocus.PERFORMANCE in areas
    assert ReviewFocus.SECURITY in areas


def test_parse_stacks() -> None:
    assert parse_stacks(" Rails, react ") == ("rails", "react")
    with pytest.raises(ConfigError, match="Unknown stack cobol"):
        parse_stacks("rails,cobol")


def test_monorepo_finds_react_beyond_many_packages() -> None:
    packages = {f"packages/lib{i}/package.json": "{}" for i in range(15)}
    packages["web/package.json"] = '{"dependencies": {"react": "19.0.0"}}'
    reads: list[str] = []

    def read(path: str) -> str | None:
        reads.append(path)
        return packages[path]

    # Listed deepest-first, the way a big monorepo might sort; web/ is shallower.
    assert detect_stacks([*packages][::-1], read) == ("react",)
    assert reads[0] == "web/package.json"  # shallow files are read first...
    assert len(reads) == 1  # ...and reading stops once React is found

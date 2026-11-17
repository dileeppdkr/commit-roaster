# commit-roaster

A grumpy senior dev in your terminal. `roast` reads your git diff, roasts it, lists the
real problems it spots, and writes the commit message you should have written.

```text
$ git add app.py
$ roast
The roast  (cringe score: 6/10)
A print statement labelled DEBUG, sitting in the middle of add(). Bold move,
shipping your diary to production. The function adds two numbers; it does not
need a narrator.

Issues spotted
  - Leftover debug print in add() will spam stdout on every call.

Suggested commit message
  Add debug output to add()

  Prints both operands before summing them, to trace a wrong total.
```

The roast is always about the code, never the person.

## Install

Needs Python 3.12+ and git. Install it once, as a command-line tool, and `roast` works in
every git repository on your machine:

```bash
pipx install commit-roaster            # or: uv tool install commit-roaster

# Before the first PyPI release, install straight from GitHub:
pipx install git+https://github.com/dileeppdkr/commit-roaster.git
```

Then set your Anthropic API key (get one at https://platform.claude.com/settings/keys),
for example in your shell profile:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

## Use it in your project

`roast` works on any language: Rails, React, Angular, PHP, Python, Java, Go, .NET or
anything else in git.
Run it in any repository after `git add`. To get roasted on every commit, call it from the
git hook tool your project already uses. Always pass `--exit-zero` in a hook: without it,
a missing key or an API outage would block the commit.

| Stack | Usual hook tool | Needs `roast` installed by each developer? |
|---|---|---|
| Python, PHP, Java, Go, any | [pre-commit](https://pre-commit.com) | No, pre-commit installs it |
| React, Angular | [husky](https://typicode.github.io/husky/) | Yes (`pipx install commit-roaster`) |
| Rails, PHP, Go, Java, any | [lefthook](https://lefthook.dev) | Yes (`pipx install commit-roaster`) |

**pre-commit**, in `.pre-commit-config.yaml`, then run `pre-commit install`:

```yaml
repos:
  - repo: https://github.com/dileeppdkr/commit-roaster
    rev: v0.1.0
    hooks:
      - id: roast
        # Optional. Setting `args` replaces the hook's own args, so keep --exit-zero.
        # --exclude adds to the built-in excludes; it does not replace them.
        # args: [--exit-zero, --spice, savage, --exclude, "app/assets/builds"]
```

**husky** (React, Angular), in `.husky/pre-commit`:

```sh
roast --exit-zero
```

**lefthook** (Rails, PHP), in `lefthook.yml`, then run `lefthook install`:

```yaml
pre-commit:
  commands:
    roast:
      run: roast --exit-zero
```

Everyone who commits needs `ANTHROPIC_API_KEY` set. Without it, they see an error and the
commit still goes through. Every commit makes one API call. To skip one, use
`SKIP=roast git commit ...` with pre-commit, `HUSKY=0` with husky, or `LEFTHOOK=0` with
lefthook.

## Usage

| Command | What it does |
|---|---|
| `roast` | Roast your staged changes (what the next commit will contain) |
| `roast --spice mild` / `savage` | Turn the heat down or up (default `medium`) |
| `roast --range HEAD~3..HEAD` | Roast any revision or range git understands |
| `git show abc123 \| roast --stdin` | Roast any diff you can pipe in |
| `roast --commit` | Roast, then commit with the suggested message (asks first) |
| `roast --commit --yes` | Same, without asking |
| `roast --json` | Machine-readable output for scripts |
| `roast --exit-zero` | Report errors but always exit 0 (for git hooks) |
| `roast -v` / `-vv` | Show request ID and token usage / full debug logs |

Run `roast --help` for every flag.

### Configuration

CLI flags win over environment variables, which win over defaults.

| Env var | Flag | Default | Meaning |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | | (required) | Your API key |
| `ROAST_MODEL` | `--model` | `claude-haiku-5-5` | Claude model ID |
| `ROAST_EFFORT` | `--effort` | `low` | Thinking effort: `low` `medium` `high` `xhigh` `max` |
| `ROAST_MAX_DIFF_CHARS` | `--max-diff-chars` | `60000` | Larger diffs are cut, with a warning |
| `ROAST_EXCLUDE` | `-x`, `--exclude` | (none) | More files to leave out, added to the defaults below |
| | `--no-default-excludes` | off | Send the default-excluded files too |
| `NO_COLOR` | | unset | Set it to turn off colors |

### Excluded files

Generated and vendored files are left out of the diff, so you don't pay to send noise:

| Stack | Left out by default |
|---|---|
| All | `*.lock` and `*.lockfile` (`Gemfile.lock`, `yarn.lock`, `composer.lock`, `poetry.lock`, `Cargo.lock`, `gradle.lockfile`, ...), `*.min.js`, `*.min.css`, `*.map` |
| React, Angular | `package-lock.json`, `npm-shrinkwrap.json`, `pnpm-lock.yaml`, Jest/Vitest `*.snap` |
| Rails | `db/schema.rb`, `db/structure.sql` (review the migration instead), `public/assets/`, `public/packs/` |
| PHP, Laravel | `vendor/` (root only), `public/build/`, `_ide_helper.php`, `_ide_helper_models.php`, `.phpstorm.meta.php` |
| Python | Lock files, protobuf/gRPC code (`*_pb2.py`, `*_pb2.pyi`, `*_pb2_grpc.py`) |
| Java, Kotlin | `gradlew`, `gradlew.bat`, `mvnw`, `mvnw.cmd`, `gradle.lockfile` |
| Go | `go.sum`, `go.work.sum`, `*.pb.go`, `*.pb.gw.go`, `zz_generated.*`, `vendor/` (root only) |
| .NET | `packages.lock.json`, `*.Designer.cs`, the EF Core `*ModelSnapshot.cs` in `Migrations/` |
| Swift, iOS | `Package.resolved`, `Podfile.lock`, `*.pbxproj` |
| Dart, Flutter | `pubspec.lock`, `*.g.dart`, `*.freezed.dart` |
| Rust, Elixir | `Cargo.lock`, `mix.lock` |
| Terraform | `.terraform.lock.hcl` |

File patterns match in any folder, so monorepos work too (`web/package-lock.json`,
`android/gradlew`). Folder patterns (`vendor/`, `public/assets/`, `public/packs/`,
`public/build/`) match only at the root, so a hand-written `app/services/vendor/` is
still reviewed. In a monorepo, add nested ones yourself, e.g.
`-x backend/vendor -x services/api/vendor`.

To leave out more, use `--exclude` (repeat it, or separate patterns with commas) or
`ROAST_EXCLUDE`. Patterns follow git's rules from the repository root: `*` also matches
`/`, and a folder name leaves out everything under it.

```bash
roast -x app/assets/builds -x 'coverage,storage/*.json'
export ROAST_EXCLUDE='app/assets/builds,coverage'
```

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Runtime error (API or git failure) |
| 2 | Usage or configuration error (bad flag, missing key, not a git repo) |
| 3 | Nothing to roast (empty diff) |
| 130 | Interrupted with Ctrl+C |

### Cost

Each run makes one API call. Cost grows with diff size. On the default `claude-haiku-5-5`
at `low` effort, even a maximum-size diff costs well under a cent. For sharper roasts, try
`--model claude-sonnet-5-5` or `claude-opus-5-5` (roughly 20 to 40 times the price).

## How it works

The code is small and object-oriented. Each class has one job and gets its collaborators
passed in, so every part can be tested on its own.

```text
cli.CommitRoasterCLI        parses flags, wires everything up, maps errors to exit codes
 ├─ config.Settings         immutable settings: defaults → env vars → flags
 ├─ diff.DiffSource (ABC)   where the diff comes from (template method)
 │   ├─ StagedDiffSource
 │   ├─ RevisionRangeDiffSource
 │   └─ StdinDiffSource
 │       └─ git.GitClient   runs git with argument lists, never a shell
 ├─ roaster.Roaster         the only class that calls the Claude API
 │   └─ prompts.PromptBuilder
 ├─ models.RoastResult      Pydantic model; Claude must return exactly this shape
 └─ render.Renderer (ABC)   TextRenderer | JsonRenderer
```

A few decisions worth knowing:

- **Structured output.** `Roaster` asks Claude for JSON that matches `RoastResult`, and the
  SDK validates it. No regex parsing of free text.
- **Refusal fallback.** Requests opt in to the API's server-side fallback
  (`fallbacks="default"`). If the model's safety classifier declines a diff, the API retries
  it on a fallback model in the same call. If every model declines, you get a clear error.
  Haiku models have no fallback, so a declined diff gives the error right away.
- **Prompt injection.** The diff is wrapped in `<diff>` tags and the system prompt tells
  Claude to treat anything inside as code to review, not as instructions.
- **No silent truncation.** Oversized diffs are cut at a line boundary, you get a warning,
  and Claude is told that it only saw part of the change.
- **Expected errors vs bugs.** Expected failures raise a `CommitRoasterError` subclass and
  become a one-line message plus an exit code. Anything else shows a full traceback,
  because it's a bug.

## Development

```bash
git clone https://github.com/dileeppdkr/commit-roaster.git
cd commit-roaster
make install    # creates .venv, installs the package and dev tools, sets up pre-commit
source .venv/bin/activate

make check      # lint + type check + tests: the same steps CI runs
make format     # auto-fix and format
make test       # pytest with coverage (fails under 90%)
```

- Lint and format: [ruff](https://docs.astral.sh/ruff/)
- Types: mypy in `--strict` mode
- Tests: pytest. Tests never call the real API: the Claude client and git are swapped for
  fakes in `tests/fakes.py`. The git tests also run against throwaway repos in a temp dir.
- CI: GitHub Actions on Python 3.12, 3.13 and 3.14 (`.github/workflows/ci.yml`)
- Pre-commit hooks run ruff and mypy before each commit (`make install` sets them up)

```text
src/commit_roaster/
  cli.py         entry point and wiring
  config.py      Settings
  diff.py        DiffSource classes
  git.py         GitClient
  models.py      SpiceLevel, RoastResult
  prompts.py     PromptBuilder
  roaster.py     Roaster (Claude API)
  render.py      TextRenderer, JsonRenderer
  exceptions.py  error types and exit codes
tests/           one test module per source module
```

### Releasing

Pushing a version tag publishes the package to PyPI through
`.github/workflows/release.yml`. It uses PyPI trusted publishing, so no token is stored in
GitHub.

One-time setup:

1. Create an account on [pypi.org](https://pypi.org).
2. Under *Your projects → Publishing*, add a pending trusted publisher: project
   `commit-roaster`, owner `dileeppdkr`, repository `commit-roaster`, workflow
   `release.yml`, environment `pypi`.
3. In the GitHub repo, go to *Settings → Environments* and create an environment named
   `pypi`.

For each release, bump `version` in `pyproject.toml`, commit, then:

```bash
git tag v0.1.0
git push origin main v0.1.0
```

The workflow refuses to publish if the tag does not match the version. The same tag is
what pre-commit users put in `rev:`.

## License

MIT

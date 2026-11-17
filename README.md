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

Needs Python 3.12+ and git.

```bash
git clone https://github.com/dileeppdkr/commit-roaster.git
cd commit-roaster
make install            # creates .venv, installs the package and dev tools
source .venv/bin/activate
```

Then set your Anthropic API key (get one at https://platform.claude.com/settings/keys):

```bash
export ANTHROPIC_API_KEY=sk-ant-...
# or: cp .env.example .env, edit it, then:  set -a; source .env; set +a
```

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
| `roast -v` / `-vv` | Show request ID and token usage / full debug logs |

Run `roast --help` for every flag.

### Configuration

CLI flags win over environment variables, which win over defaults.

| Env var | Flag | Default | Meaning |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | | (required) | Your API key |
| `ROAST_MODEL` | `--model` | `claude-opus-5-5` | Claude model ID |
| `ROAST_EFFORT` | `--effort` | `medium` | Thinking effort: `low` `medium` `high` `xhigh` `max` |
| `ROAST_MAX_DIFF_CHARS` | `--max-diff-chars` | `60000` | Larger diffs are cut, with a warning |
| `NO_COLOR` | | unset | Set it to turn off colors |

Lock files and minified assets (`*.lock`, `package-lock.json`, `*.min.js`, ...) are left
out of the diff, so you don't pay to send noise.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Runtime error (API or git failure) |
| 2 | Usage or configuration error (bad flag, missing key, not a git repo) |
| 3 | Nothing to roast (empty diff) |
| 130 | Interrupted with Ctrl+C |

### Cost

Each run makes one API call. Cost grows with diff size; a typical small commit costs about
a cent on `claude-opus-5-5`. Use `--effort low` or `ROAST_MODEL=claude-haiku-5-5` for
cheaper runs.

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
- **Prompt injection.** The diff is wrapped in `<diff>` tags and the system prompt tells
  Claude to treat anything inside as code to review, not as instructions.
- **No silent truncation.** Oversized diffs are cut at a line boundary, you get a warning,
  and Claude is told that it only saw part of the change.
- **Expected errors vs bugs.** Expected failures raise a `CommitRoasterError` subclass and
  become a one-line message plus an exit code. Anything else shows a full traceback,
  because it's a bug.

## Development

```bash
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

## License

MIT

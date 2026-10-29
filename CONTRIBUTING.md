# Contributing

## Workflow

1. Branch off `main`: `git switch -c feature/<short-name>`.
2. Make the change, with tests.
3. Run local CI (below) until it passes.
4. Push the branch and open a pull request into `main`. Never commit directly to `main`.

## Setup

```bash
uv venv && uv pip install -e ".[dev]"
git config core.hooksPath .githooks   # run CI automatically before every push
```

[uv](https://docs.astral.sh/uv/) is required for CI. It also downloads any Python versions the matrix needs.

## Local CI

```bash
scripts/ci.sh           # full run, about 10-20 s
scripts/ci.sh --quick   # lint + tests on .venv only, for quick iteration
```

The full run does the following:

| Check | What it does |
|---|---|
| Lint | `ruff check` and `ruff format --check` |
| Tests | Unit tests and the example suite on Python 3.10 and 3.12. Override with `CI_PYTHONS="3.11 3.13"`. |
| Package | Builds the wheel and sdist, and fails if either contains private files |
| Privacy | Fails on private files tracked by git, email addresses in committed files, or a commit author or committer email that isn't a noreply address |
| Personal terms | Fails if any term in your local `.privacy-patterns` file appears in committed content or commit metadata |

Once enabled, the `pre-push` hook runs the full CI, and a failure cancels the push.

### `.privacy-patterns`

`.privacy-patterns` is an optional, gitignored file in the repo root. It holds one case-insensitive regular expression per line, and `#` starts a comment. Put names, employers and anything else that must never be published there. The list stays on your machine, so it is never published itself.

## Commit identity

Commit with your GitHub noreply address so no personal email lands in the history:

```bash
git config user.email "<id>+<username>@users.noreply.github.com"
```

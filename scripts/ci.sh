#!/usr/bin/env bash
# Local CI: the checks a hosted CI run would do, run on this machine.
#
#   scripts/ci.sh          full run: lint, tests + examples on each Python, package, privacy
#   scripts/ci.sh --quick  lint + tests on the dev venv (.venv) only
#
# CI_PYTHONS overrides the Python matrix, e.g. CI_PYTHONS="3.11" scripts/ci.sh
# Requires uv (https://docs.astral.sh/uv/), which also fetches missing Pythons.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
CI_DIR="$ROOT/.ci"
PYTHONS="${CI_PYTHONS:-3.10 3.12}"
QUICK=0
[[ "${1:-}" == "--quick" ]] && QUICK=1

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\033[31mCI FAILED: %s\033[0m\n' "$*" >&2; exit 1; }
started=$(date +%s)

command -v uv >/dev/null || fail "uv is required: https://docs.astral.sh/uv/"

# -- Python matrix -----------------------------------------------------------
if [[ $QUICK == 1 ]]; then
    [[ -x .venv/bin/python ]] || fail ".venv not found; run: uv venv && uv pip install -e '.[dev]'"
    venvs=".venv"
else
    venvs=""
    for py in $PYTHONS; do
        venv="$CI_DIR/py$py"
        step "Python $py: install"
        [[ -x "$venv/bin/python" ]] || uv venv -q --python "$py" "$venv"
        uv pip install -q --python "$venv/bin/python" -e ".[dev]"
        venvs="$venvs $venv"
    done
fi
for first_venv in $venvs; do break; done

step "Lint (ruff)"
"$first_venv/bin/ruff" check .
"$first_venv/bin/ruff" format --check .

for venv in $venvs; do
    version="$("$venv/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    step "Python $version: unit tests"
    "$venv/bin/pytest" -q -p no:cacheprovider
    step "Python $version: example suite"
    "$venv/bin/benchpilot" run examples/cruise_control
done

if [[ $QUICK == 1 ]]; then
    printf '\n\033[32mQuick CI passed in %ss\033[0m\n' "$(( $(date +%s) - started ))"
    exit 0
fi

# -- Package -----------------------------------------------------------------
step "Package: build and inspect contents"
rm -rf "$CI_DIR/dist"
uv build -q --out-dir "$CI_DIR/dist" .
"$first_venv/bin/python" -I - "$CI_DIR/dist" <<'PY'
import re, sys, tarfile, zipfile
from pathlib import Path

dist = Path(sys.argv[1])
forbidden = re.compile(r"(\.pdf$|(^|/)CLAUDE(\.local)?\.md$|(^|/)\.env|(^|/)\.privacy-patterns$)", re.I)
bad = []
for wheel in dist.glob("*.whl"):
    bad += [f"{wheel.name}: {n}" for n in zipfile.ZipFile(wheel).namelist() if forbidden.search(n)]
for sdist in dist.glob("*.tar.gz"):
    with tarfile.open(sdist) as tar:
        bad += [f"{sdist.name}: {n}" for n in tar.getnames() if forbidden.search(n)]
if bad:
    sys.exit("private files in the package:\n  " + "\n  ".join(bad))
print("ok:", ", ".join(p.name for p in sorted([*dist.glob("*.whl"), *dist.glob("*.tar.gz")])))
PY

# -- Privacy -----------------------------------------------------------------
# Checks what is committed (HEAD), since that is what a push publishes.
step "Privacy: tracked files"
if git ls-files | grep -iE '\.pdf$|(^|/)CLAUDE(\.local)?\.md$|(^|/)\.privacy-patterns$'; then
    fail "private files are tracked by git (see above)"
fi
echo "ok"

step "Privacy: email addresses in committed content"
emails="$(git grep -n -I -E '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' HEAD -- . ':!LICENSE' \
    | grep -v -E 'noreply|@example\.(com|org)' || true)"
[[ -z "$emails" ]] || { printf '%s\n' "$emails"; fail "email addresses found in committed files"; }
echo "ok"

step "Privacy: commit authors use a noreply address"
bad_ids="$(git log --format='%h %ae %ce' HEAD | grep -v -E '^[0-9a-f]+ \S*noreply\S* \S*noreply\S*$' || true)"
[[ -z "$bad_ids" ]] || { printf '%s\n' "$bad_ids"; fail "commits with a non-noreply author/committer email"; }
echo "ok"

# Personal terms (names, employers...) live in an untracked, gitignored file so
# the list itself is never published. One case-insensitive regex per line.
# The repo owner's GitHub username is public by definition (it's in the URL),
# so it's removed before matching.
owner="$(git remote get-url origin 2>/dev/null | sed -nE 's#.*github\.com[:/]([^/]+)/.*#\1#p')"
strip_owner() { if [[ -n "$owner" ]]; then sed "s#$owner##g"; else cat; fi; }

step "Privacy: personal terms from .privacy-patterns"
if [[ -f .privacy-patterns ]]; then
    patterns="$CI_DIR/privacy-patterns.clean"
    grep -v -E '^\s*(#|$)' .privacy-patterns > "$patterns" || true
    if [[ -s "$patterns" ]]; then
        hits="$(git grep -n -I -i -E -f "$patterns" HEAD -- . ':!LICENSE' | strip_owner \
            | grep -i -E -f "$patterns" || true)"
        meta="$(git log --format='%h %an %cn %B' HEAD | strip_owner \
            | grep -i -E -f "$patterns" || true)"
        [[ -z "$hits$meta" ]] || { printf '%s\n%s\n' "$hits" "$meta"; fail "personal terms found"; }
    fi
    echo "ok"
else
    echo "skipped: no .privacy-patterns file (see CONTRIBUTING.md)"
fi

printf '\n\033[32mCI passed in %ss\033[0m\n' "$(( $(date +%s) - started ))"

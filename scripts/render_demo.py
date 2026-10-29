"""Render docs/demo.svg: an animated terminal showing real `benchpilot` runs.

The output in the SVG is captured from actually running the commands, so the
demo can't drift from what the tool really prints. Re-run after changing CLI
output:

    .venv/bin/python scripts/render_demo.py
"""

from __future__ import annotations

import subprocess
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "demo.svg"

COMMANDS = [
    "benchpilot run examples/cruise_control",
    "benchpilot run examples/cruise_control --bench examples/cruise_control/faulty.bench.yaml",
]

# Look
FONT_SIZE = 13
CHAR_W = 7.83  # Menlo/SF Mono advance width at 13px
LINE_H = 19
PAD = 18
TITLE_H = 30
COLORS = {
    "bg": "#0d1117",
    "title": "#161b22",
    "fg": "#c9d1d9",
    "dim": "#8b949e",
    "prompt": "#3fb950",
    "pass": "#3fb950",
    "fail": "#f85149",
    "error": "#d29922",
    "detail": "#ffa198",
}

# Pacing, in seconds
TYPE_PER_CHAR = 0.025
AFTER_TYPING = 0.35
PER_OUTPUT_LINE = 0.09
AFTER_OUTPUT = 2.2
HOLD_AT_END = 5.0


def run(command: str) -> list[str]:
    args = command.split()[1:]
    proc = subprocess.run(
        [sys.executable, "-m", "benchpilot", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return (proc.stdout + proc.stderr).rstrip("\n").splitlines()


def styled(line: str) -> str:
    """Escape a line of CLI output and colour its status word."""
    for word, color in (("PASS", "pass"), ("FAIL", "fail"), ("ERROR", "error")):
        if line.startswith(word):
            rest = escape(line[len(word) :])
            return f'<tspan fill="{COLORS[color]}" font-weight="bold">{word}</tspan>{rest}'
    if line.startswith("         "):  # failure detail under a FAIL line
        return f'<tspan fill="{COLORS["detail"]}">{escape(line)}</tspan>'
    if line.startswith("benchpilot "):
        return f'<tspan fill="{COLORS["dim"]}">{escape(line)}</tspan>'
    if " passed, " in line:
        return f'<tspan font-weight="bold">{escape(line)}</tspan>'
    return escape(line)


def build() -> str:
    # Each item: (kind, text, appear_at, typing_seconds)
    items: list[tuple[str, str, float, float]] = []
    t = 0.6
    for i, command in enumerate(COMMANDS):
        if i:
            items.append(("blank", "", t, 0.0))
        typing = len(command) * TYPE_PER_CHAR
        items.append(("cmd", command, t, typing))
        t += typing + AFTER_TYPING
        for line in run(command):
            items.append(("out", line, t, 0.0))
            t += PER_OUTPUT_LINE
        t += AFTER_OUTPUT
    total = t + HOLD_AT_END

    longest = max(len(text) + (2 if kind == "cmd" else 0) for kind, text, _, _ in items)
    width = round(PAD * 2 + longest * CHAR_W)
    height = TITLE_H + PAD * 2 + LINE_H * len(items)

    def pct(seconds: float) -> str:
        return f"{seconds / total * 100:.3f}%"

    css = [
        f"text{{font:{FONT_SIZE}px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;"
        f"fill:{COLORS['fg']}}}"
    ]
    body = []
    for n, (kind, text, at, typing) in enumerate(items):
        y = TITLE_H + PAD + LINE_H * n + FONT_SIZE
        css.append(
            f"@keyframes a{n}{{0%,{pct(at)}{{opacity:0}}"
            f"{pct(at + 0.001)},98%{{opacity:1}}100%{{opacity:0}}}}"
            f".a{n}{{opacity:0;animation:a{n} {total:.2f}s linear infinite}}"
        )
        if kind == "cmd":
            x_cmd = PAD + 2 * CHAR_W
            cover_w = len(text) * CHAR_W + 4
            # A background-coloured box slides right in character steps, "typing" the command.
            css.append(
                f"@keyframes c{n}{{0%,{pct(at)}{{transform:translateX(0);"
                f"animation-timing-function:steps({len(text)},end)}}"
                f"{pct(at + typing)},100%{{transform:translateX({cover_w:.1f}px)}}}}"
                f".c{n}{{animation:c{n} {total:.2f}s linear infinite}}"
            )
            body.append(
                f'<g class="a{n}">'
                f'<text x="{PAD}" y="{y}" xml:space="preserve">'
                f'<tspan fill="{COLORS["prompt"]}" font-weight="bold">$</tspan></text>'
                f'<text x="{x_cmd:.1f}" y="{y}" xml:space="preserve">{escape(text)}</text>'
                f'<rect class="c{n}" x="{x_cmd - 1:.1f}" y="{y - FONT_SIZE}" '
                f'width="{cover_w:.1f}" height="{LINE_H}" fill="{COLORS["bg"]}"/></g>'
            )
        elif kind == "out":
            body.append(
                f'<text class="a{n}" x="{PAD}" y="{y}" xml:space="preserve">{styled(text)}</text>'
            )

    dots = "".join(
        f'<circle cx="{18 + i * 20}" cy="{TITLE_H / 2}" r="6" fill="{c}"/>'
        for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840"))
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Terminal demo: BenchPilot runs the cruise-control suite, all 5 tests '
        f'pass; then against faulty firmware, the brake test fails and catches the bug.">'
        f"<style>{''.join(css)}</style>"
        f'<defs><clipPath id="win"><rect width="{width}" height="{height}" rx="10"/></clipPath>'
        f"</defs>"
        f'<g clip-path="url(#win)">'
        f'<rect width="{width}" height="{height}" fill="{COLORS["bg"]}"/>'
        f'<rect width="{width}" height="{TITLE_H}" fill="{COLORS["title"]}"/>{dots}'
        f"{''.join(body)}</g></svg>\n"
    )


def main() -> None:
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(build(), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

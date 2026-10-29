"""Self-contained HTML report with a plot per captured signal.

One file, no scripts, no external assets: lab PCs are often offline and
reports get emailed. Plots are inline SVG; each `expect` is drawn as a band
over the time it was checked (green if it held, red if not), and each write
to the plotted signal as a dashed marker.
"""

from __future__ import annotations

import math
from datetime import datetime
from html import escape
from pathlib import Path

from benchpilot import __version__
from benchpilot.testcases.executor import Outcome, StepRecord, TestResult

MAX_POINTS = 1500  # per series; longer series are min/max-decimated

_LABEL = {Outcome.PASSED: "PASS", Outcome.FAILED: "FAIL", Outcome.ERROR: "ERROR"}

CSS = """
:root{--bg:#ffffff;--fg:#1f2328;--muted:#656d76;--line:#d0d7de;--panel:#f6f8fa;
--plot:#0969da;--pass:#1a7f37;--fail:#cf222e;--error:#9a6700;
--band-pass:rgba(26,127,55,.14);--band-fail:rgba(207,34,46,.30);--mark:#8250df}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#8d96a0;
--line:#30363d;--panel:#161b22;--plot:#4493f8;--pass:#3fb950;--fail:#f85149;--error:#d29922;
--band-pass:rgba(63,185,80,.18);--band-fail:rgba(248,81,73,.34);--mark:#a371f7}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1000px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:22px;margin:0 0 4px}
.meta{color:var(--muted);margin:0 0 16px}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 20px}
.chip{border:1px solid var(--line);border-radius:999px;padding:2px 12px;font-weight:600}
.badge{display:inline-block;min-width:52px;text-align:center;border-radius:6px;
padding:1px 6px;font-weight:700;font-size:12px;color:#fff}
.passed{background:var(--pass)}.failed{background:var(--fail)}.error{background:var(--error)}
.t-passed{color:var(--pass)}.t-failed{color:var(--fail)}.t-error{color:var(--error)}
table{border-collapse:collapse;width:100%}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase}
.scroll{overflow-x:auto}
a{color:var(--plot)}
details{border:1px solid var(--line);border-radius:10px;margin:16px 0;background:var(--bg)}
summary{cursor:pointer;padding:12px 16px;font-weight:600;list-style-position:inside}
.body{padding:0 16px 16px}
.why{background:var(--panel);border-left:4px solid var(--fail);padding:8px 12px;
border-radius:6px;margin:8px 0 12px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,
monospace;font-size:13px;overflow-wrap:anywhere}
.why.error{border-left-color:var(--error)}
.chart{margin:12px 0}
.chart h3{font-size:13px;margin:0 0 4px;font-family:ui-monospace,SFMono-Regular,Menlo,
Consolas,monospace}
svg.plot{width:100%;height:auto;display:block;background:var(--panel);border-radius:8px}
svg .axis{stroke:var(--line)}svg .series{stroke:var(--plot);fill:none;stroke-width:1.5}
svg .tick{fill:var(--muted);font:11px system-ui,sans-serif}
svg .band-passed{fill:var(--band-pass)}svg .band-failed,svg .band-error{fill:var(--band-fail)}
svg .mark{stroke:var(--mark);stroke-dasharray:3 3}
.small{font-size:12px;color:var(--muted)}
td.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px}
"""


def render_html(
    results: list[TestResult],
    bench_name: str,
    adapter: str,
    started_at: datetime | None = None,
) -> str:
    started = (started_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    counts = {o: sum(r.outcome is o for r in results) for o in Outcome}
    bench_time = sum(r.bench_time_s for r in results)

    rows = "".join(_summary_row(r) for r in results)
    sections = "".join(_test_section(r) for r in results)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BenchPilot report</title><style>{CSS}</style></head>
<body><main>
<h1>BenchPilot report</h1>
<p class="meta">Bench <strong>{escape(bench_name)}</strong> ({escape(adapter)}) ·
started {started} · BenchPilot {__version__}</p>
<div class="chips">
<span class="chip t-passed">{counts[Outcome.PASSED]} passed</span>
<span class="chip t-failed">{counts[Outcome.FAILED]} failed</span>
<span class="chip t-error">{counts[Outcome.ERROR]} error(s)</span>
<span class="chip">{bench_time:.1f}s bench time</span>
</div>
<div class="scroll"><table>
<thead><tr><th>Result</th><th>Test</th><th>Requirement</th><th>Bench time</th></tr></thead>
<tbody>{rows}</tbody></table></div>
{sections}
</main></body></html>
"""


def write_html(
    results: list[TestResult],
    path: str | Path,
    bench_name: str,
    adapter: str,
    started_at: datetime | None = None,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(results, bench_name, adapter, started_at), encoding="utf-8")
    return path


# -- page parts ------------------------------------------------------------------


def _anchor(r: TestResult) -> str:
    return "t-" + "".join(c if c.isalnum() or c in "-_" else "_" for c in r.testcase.id)


def _badge(outcome: Outcome) -> str:
    return f'<span class="badge {outcome.value}">{_LABEL[outcome]}</span>'


def _summary_row(r: TestResult) -> str:
    tc = r.testcase
    return (
        f"<tr><td>{_badge(r.outcome)}</td>"
        f'<td><a href="#{_anchor(r)}">{escape(tc.id)}</a> {escape(tc.title)}</td>'
        f"<td>{escape(tc.requirement or '')}</td><td>{r.bench_time_s:.1f}s</td></tr>"
    )


def _test_section(r: TestResult) -> str:
    tc = r.testcase
    opened = "" if r.passed else " open"
    meta = [f"bench time {r.bench_time_s:.1f}s", f"wall time {r.wall_time_s:.2f}s"]
    if tc.requirement:
        meta.insert(0, f"requirement {escape(tc.requirement)}")
    if tc.tags:
        meta.append("tags " + escape(", ".join(tc.tags)))
    if r.source is not None:
        meta.append(escape(str(r.source)))

    why = ""
    if not r.passed:
        where = f"{escape(r.location)}: " if r.location else ""
        why = f'<div class="why {r.outcome.value}">{where}{escape(r.message)}</div>'

    charts = ""
    t0 = _time_origin(r)
    span = _time_span(r, t0)  # every chart in a test shares one time axis
    if r.capture is not None and r.capture.time:
        ordered = sorted(r.capture.values, key=lambda s: not _has_expect(r.steps, s))
        for signal in ordered:
            steps = [s for s in r.steps if s.signal == signal]
            svg = _chart(r.capture.time, r.capture.values[signal], steps, t0, span)
            charts += f'<div class="chart"><h3>{escape(signal)}</h3>{svg}</div>'
    # ECU variables aren't captured by the bench; plot what each expect read instead.
    for var in dict.fromkeys(s.ecu_var for s in r.steps if s.ecu_var and s.samples):
        steps = [s for s in r.steps if s.ecu_var == var]
        times, values = _expect_samples(steps)
        if sum(math.isfinite(v) for v in values) < 2:
            continue  # a single instantaneous reading says nothing a chart can show
        svg = _chart(times, values, steps, t0, span)
        charts += (
            f'<div class="chart"><h3>ecu_var {escape(var)} '
            f'<span class="small">(read during expects)</span></h3>{svg}</div>'
        )

    step_rows = ""
    if r.steps:
        for s in r.steps:
            step_rows += (
                f'<tr><td class="mono">{escape(s.location)}</td>'
                f'<td class="mono">{escape(s.description)}{_metrics_note(s)}</td>'
                f"<td>{s.start - t0:.2f}–{s.end - t0:.2f}s</td>"
                f"<td>{_badge(s.outcome)}</td></tr>"
            )
        step_rows = (
            '<div class="scroll"><table><thead><tr><th>Step</th><th>What</th><th>When</th>'
            f"<th>Result</th></tr></thead><tbody>{step_rows}</tbody></table></div>"
        )

    return (
        f'<details id="{_anchor(r)}"{opened}><summary>{_badge(r.outcome)} '
        f"{escape(tc.id)} · {escape(tc.title)}</summary>"
        f'<div class="body"><p class="small">{" · ".join(meta)}</p>{why}{charts}{step_rows}'
        "</div></details>"
    )


_METRIC_LABELS = {
    "settling_time_s": ("settled after", "{:.2f}s"),
    "overshoot": ("overshoot", "{:.4g}"),
    "overshoot_pct": ("", "({:.1f}%)"),
    "rise_time_s": ("rise", "{:.2f}s"),
}


def _metrics_note(step: StepRecord) -> str:
    if not step.metrics:
        return ""
    parts = []
    for key, (label, fmt) in _METRIC_LABELS.items():
        if key in step.metrics:
            parts.append(f"{label} {fmt.format(step.metrics[key])}".strip())
    return f'<br><span class="small">measured: {escape(" ".join(parts))}</span>'


def _time_origin(r: TestResult) -> float:
    if r.capture is not None and r.capture.time:
        return r.capture.time[0]
    return r.steps[0].start if r.steps else 0.0


def _time_span(r: TestResult, t0: float) -> float:
    ends = [s.end for s in r.steps]
    if r.capture is not None and r.capture.time:
        ends.append(r.capture.time[-1])
    return max((e - t0 for e in ends), default=0.0) or 1.0


def _expect_samples(steps: list[StepRecord]) -> tuple[list[float], list[float]]:
    """Join the readings of several expects, with a gap between them."""
    times: list[float] = []
    values: list[float] = []
    for step in sorted(steps, key=lambda s: s.start):
        if not step.samples:
            continue
        if times:
            times.append(step.samples[0][0])
            values.append(math.nan)  # don't draw a line across time nobody looked
        for t, v in step.samples:
            times.append(t)
            values.append(v)
    return times, values


def _has_expect(steps: list[StepRecord], signal: str) -> bool:
    return any(s.signal == signal and s.band is not None for s in steps)


# -- charts --------------------------------------------------------------------------

W, H = 900, 180
LEFT, RIGHT, TOP, BOTTOM = 56, 12, 10, 24
MIN_BAND_PX = 8


def _chart(
    times: list[float], values: list[float], steps: list[StepRecord], t0: float, t_end: float
) -> str:
    ts, vs = _decimate([t - t0 for t in times], values)
    finite = [v for v in vs if math.isfinite(v)]
    if not finite:
        return '<p class="small">No finite samples recorded.</p>'
    # The expected range always belongs on the axis, even if the data never got there.
    bands = [b for s in steps if s.band for b in s.band if math.isfinite(b)]
    full = (min(finite + bands), max(finite + bands))
    zoom = _checked_range(ts, vs, steps, t0)
    zoomed = zoom is not None and (zoom[1] - zoom[0]) * 3 < (full[1] - full[0])
    data_lo, data_hi = zoom if zoomed else full
    lo, hi = data_lo, data_hi
    if hi - lo < 1e-12:
        lo, hi = lo - 1, hi + 1
    pad = (hi - lo) * 0.08
    lo, hi = lo - pad, hi + pad

    def x(t: float) -> float:
        return LEFT + (W - LEFT - RIGHT) * t / t_end

    def y(v: float) -> float:
        v = min(max(v, lo), hi)  # clamp infinite band edges to the plot
        return TOP + (H - TOP - BOTTOM) * (hi - v) / (hi - lo)

    parts = [
        f'<line class="axis" x1="{LEFT}" y1="{TOP}" x2="{LEFT}" y2="{H - BOTTOM}"/>',
        f'<line class="axis" x1="{LEFT}" y1="{H - BOTTOM}" x2="{W - RIGHT}" y2="{H - BOTTOM}"/>',
        f'<text class="tick" x="{LEFT - 6}" y="{y(data_hi) + 4:.1f}" '
        f'text-anchor="end">{data_hi:.4g}</text>',
        f'<text class="tick" x="{LEFT - 6}" y="{y(data_lo) + 4:.1f}" '
        f'text-anchor="end">{data_lo:.4g}</text>',
        f'<text class="tick" x="{LEFT}" y="{H - 6}">0s</text>',
        f'<text class="tick" x="{W - RIGHT}" y="{H - 6}" text-anchor="end">{t_end:.4g}s</text>',
    ]
    if zoomed:
        parts.append(
            f'<text class="tick" x="{W - RIGHT - 4}" y="{TOP + 12}" text-anchor="end">'
            f"zoomed to checked range · full range {full[0]:.4g} to {full[1]:.4g}</text>"
        )
    for s in steps:
        tip = f"<title>{escape(s.location)}: {escape(s.description)} ({s.outcome.value})</title>"
        if s.band is not None:
            x0, x1 = x(s.start - t0), x(s.end - t0)
            y0, y1 = y(s.band[1]), y(s.band[0])
            if y1 - y0 < MIN_BAND_PX:  # exact-value checks: keep the band visible
                mid = (y0 + y1) / 2
                y0, y1 = mid - MIN_BAND_PX / 2, mid + MIN_BAND_PX / 2
            width = max(x1 - x0, 2.0)  # instantaneous checks still show up
            parts.append(
                f'<rect class="band-{s.outcome.value}" x="{x0:.1f}" y="{y0:.1f}" '
                f'width="{width:.1f}" height="{y1 - y0:.1f}">{tip}</rect>'
            )
        elif s.location.endswith(("set", "ramp")):
            xm = x(s.start - t0)
            parts.append(
                f'<line class="mark" x1="{xm:.1f}" y1="{TOP}" x2="{xm:.1f}" '
                f'y2="{H - BOTTOM}">{tip}</line>'
            )
    for run in _finite_runs(ts, vs):
        points = " ".join(f"{x(t):.1f},{y(v):.1f}" for t, v in run)
        if len(run) == 1:
            ((t, v),) = run
            parts.append(f'<circle class="series" cx="{x(t):.1f}" cy="{y(v):.1f}" r="1.5"/>')
        else:
            parts.append(f'<polyline class="series" points="{points}"/>')
    return (
        f'<svg class="plot" viewBox="0 0 {W} {H}" role="img" '
        f'aria-label="signal plot">{"".join(parts)}</svg>'
    )


def _checked_range(
    ts: list[float], vs: list[float], steps: list[StepRecord], t0: float
) -> tuple[float, float] | None:
    """Value range that matters for the expects: their bands plus data inside their windows."""
    vals: list[float] = []
    for s in steps:
        if s.band is None:
            continue
        vals.extend(b for b in s.band if math.isfinite(b))
        start, end = s.start - t0, s.end - t0
        vals.extend(
            v for t, v in zip(ts, vs, strict=True) if start <= t <= end and math.isfinite(v)
        )
    return (min(vals), max(vals)) if vals else None


def _finite_runs(ts: list[float], vs: list[float]) -> list[list[tuple[float, float]]]:
    """Split a series at NaN/inf samples so gaps show as gaps."""
    runs: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    for t, v in zip(ts, vs, strict=True):
        if math.isfinite(v):
            current.append((t, v))
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs


def _decimate(ts: list[float], vs: list[float]) -> tuple[list[float], list[float]]:
    """Keep each bucket's min and max so short spikes survive downsampling."""
    n = len(ts)
    if n <= MAX_POINTS:
        return list(ts), list(vs)
    buckets = MAX_POINTS // 2
    out_t: list[float] = []
    out_v: list[float] = []
    for b in range(buckets):
        i0, i1 = b * n // buckets, (b + 1) * n // buckets
        idx = [i for i in range(i0, i1) if math.isfinite(vs[i])]
        if not idx:  # keep a gap marker
            out_t.append(ts[i0])
            out_v.append(math.nan)
            continue
        i_min = min(idx, key=vs.__getitem__)
        i_max = max(idx, key=vs.__getitem__)
        for i in sorted({i_min, i_max}):
            out_t.append(ts[i])
            out_v.append(vs[i])
    return out_t, out_v

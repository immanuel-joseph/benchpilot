# Running BenchPilot on a dSPACE bench (experimental)

BenchPilot talks to dSPACE HIL benches through the **ASAM XIL API**, the standard automation interface that dSPACE installs with its Release. This guide takes you from a fresh Windows lab PC to a first test.

> **Status: experimental.** The XIL adapter was written from the public XIL API documentation and tested against an in-memory fake with the same interface. It has **not been validated on a physical bench**. Run `check-bench` (step 5) before anything else, and follow your lab's safety procedures for every run.

## 1. What you need

- A Windows PC that can reach the bench, with a dSPACE Release installed, **including the XIL API**.
- **Python 3.10 or newer, 64-bit**, from [python.org](https://www.python.org/downloads/windows/). It doesn't need to be the Python that ships with dSPACE.
- **Git**, to get the code.
- Access to the real-time application on the bench, and its variable names. You can browse these in ControlDesk.

## 2. Install

```powershell
git clone https://github.com/immanuel-joseph/benchpilot.git
cd benchpilot
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[xil]"          # with a Lauterbach debugger too: ".[xil,trace32]"
```

The `[xil]` extra installs `pythonnet`, which lets Python call the XIL API's .NET libraries.

Check the install with the built-in simulator (no bench involved):

```powershell
benchpilot run examples\cruise_control
```

## 3. Keep your files outside this repository

Create a folder of your own, for example `C:\hil\my-project\`. Put your bench file and tests there. **Don't** add bench files, signal names or tests from your workplace to the BenchPilot repository.

```
C:\hil\my-project\
├── bench.yaml        # how to reach your bench
└── tests\
    └── smoke.yaml    # your first test
```

## 4. Write the bench file

Start from the template [`examples/cruise_control/dspace.bench.yaml`](../examples/cruise_control/dspace.bench.yaml), copy it to `C:\hil\my-project\bench.yaml`, and fill in the values marked `TODO`:

| Option | Where to find it |
|---|---|
| `product_version` | The dSPACE Release installed on the PC, e.g. `"2023-B"`. |
| `maport_config` | An MAPort configuration XML for your platform and real-time application. The XIL API demos installed with dSPACE include one you can copy and adapt. |
| `start_simulation` | Leave it at `false` unless you want BenchPilot to start the real-time application on connect. |
| `sample_period` | How often BenchPilot reads signals while waiting. `0.05` (50 ms) is a good start. |
| `assembly_version` | Only needed if the XIL API fails to load (see Troubleshooting). Default `"2.1.0.0"`. |

Under `signals:`, map each name your tests will use to the variable path on your bench. You can copy the paths from ControlDesk's variable browser, for example `"Model Root/Plant/Vehicle/Speed/Value"`.

Under `limits:`, **set the real safe range for every signal you will write.** A write outside these ranges is blocked before it reaches the bench.

## 5. Check the bench: read-only

```powershell
benchpilot check-bench --bench C:\hil\my-project\bench.yaml
```

This **only reads** from the bench. It checks that it can connect, that every mapped signal can be read, that an unknown path is rejected, that waiting works, and that signals can be recorded:

```
benchpilot 0.1.0 · checking bench 'my-rig' (xil) · read-only
PASS   connect
PASS   read vehicle.speed_kph  = 0
...
PASS   capture  4 samples, ordered=True, aligned=True
PASS   disconnect

14 passed, 0 failed
```

Fix every FAIL before going further. A `read <signal>` FAIL usually means a mistyped path.

## 6. Check one write (optional)

Pick a signal that is **safe to change** on your rig, such as a test parameter or a stimulus with no physical effect:

```powershell
benchpilot check-bench --bench C:\hil\my-project\bench.yaml --write road.grade_pct=1
```

BenchPilot writes the value (through the safety limits), reads it back, and **restores the original value**.

## 7. A first test

Start with a test that mostly reads, writes one value inside its limits, and puts it back in `teardown`:

```yaml
id: SMOKE-001
title: Bench responds to a grade change
steps:
  - expect: {signal: vehicle.speed_kph, min: 0}          # read only
  - set:    {signal: road.grade_pct, value: 1}
  - expect: {signal: road.grade_pct, equals: 1, tolerance: 0.01, within: 1s}
teardown:
  - set:    {signal: road.grade_pct, value: 0}
```

First, a dry run. It sends nothing to the bench. It shows every write with its bench path and limit check, and flags anything that would be blocked:

```powershell
benchpilot run C:\hil\my-project\tests --bench C:\hil\my-project\bench.yaml --dry-run
```

When the plan looks right, run it for real, with a report:

```powershell
benchpilot run C:\hil\my-project\tests --bench C:\hil\my-project\bench.yaml --html C:\hil\my-project\reports\run.html
```

Open `run.html` in a browser. It plots every recorded signal, with each `expect` shaded green (held) or red (failed) over the time it was checked.

During a run:

- **Ctrl+C** stops the run, but runs the test's `teardown` first.
- `teardown` runs after every test, pass or fail, and attempts every step.
- Any value outside `limits` is blocked, and the test ends as ERROR without touching the bench.

## Troubleshooting

| Message | What to do |
|---|---|
| `the xil adapter needs pythonnet` | Install the extra: `pip install -e ".[xil]"` |
| `could not load the XIL API (...)` | Check that the XIL API is installed. If it is, set `assembly_version` to the version of `ASAM.XIL.Implementation.TestbenchFactory` installed with your Release. |
| `could not create the XIL testbench ...` | `product_version` doesn't match the installed dSPACE Release. |
| `XIL MAPort setup failed` | Check the `maport_config` path and its contents, and that the bench is on and reachable. |
| `XIL read '...' failed` | The variable path is wrong, or isn't in the loaded application. |
| `BLOCKED` in a dry run | A value is outside its `limits`. Fix the test, or the limit if the limit is wrong. |
| `is int; can't write 2.5` | The variable is an integer or boolean on the bench. Write whole numbers (and 0/1 for booleans). |

## ECU internals through a debugger

To read or write variables inside the ECU (`ecu_var` steps) on the same bench, add a Lauterbach TRACE32 debugger. See **[trace32.md](trace32.md)**.

## Limitations

- XIL's native capture service. Signals are recorded by reading them every `sample_period`.

If something doesn't work, the output of `check-bench` is the most useful thing to report. Remove any workplace-specific variable names first.

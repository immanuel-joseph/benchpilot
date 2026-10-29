# Reading ECU internals with a Lauterbach TRACE32 debugger (experimental)

A HIL bench drives the ECU from the outside, through its wiring harness. A debugger on the ECU's debug port lets a test look **inside**: read a state machine's current state, a calibration value, or a counter. In BenchPilot, these are the `ecu_var` steps:

```yaml
- expect: {ecu_var: cc_state, equals: 2}     # read through the debugger
- set:    {ecu_var: test_mode, value: 1}     # write through the debugger (checked against ecu_limits)
```

```
           wiring harness
   ECU  ───────────────────  HIL bench  ──── PC (ControlDesk / XIL)   ← bench adapter: xil
    │                                          │
    └── debug port ── Lauterbach ── USB/Ethernet                       ← debug adapter: trace32
                                               │
                                        BenchPilot runs here
```

> **Status: experimental.** The adapter was written from Lauterbach's public Remote API documentation and tested against an in-memory fake with the same interface. It has **not been validated on a physical target**. Start with the read-only `check-bench` below.

## 1. Enable the Remote API in TRACE32

TRACE32 PowerView must be running, with its Remote API enabled. Add these lines to its configuration file (`config.t32`, or whichever file your start-up shortcut uses), then restart PowerView:

```
RCL=NETTCP
PORT=20000
```

Any free port works. Use the same number in `bench.yaml`.

## 2. Install

```powershell
pip install -e ".[trace32]"        # add ,xil if the same PC also drives the HIL: ".[xil,trace32]"
```

This installs Lauterbach's official Python module, `lauterbach-trace32-rcl`.

## 3. Add the debugger to your bench file

```yaml
debug:
  adapter: trace32
  options:
    node: localhost        # the PC running PowerView
    port: 20000            # the PORT from config.t32
    reset_commands: []     # see "Resets" below

ecu_limits:                # safe ranges for every variable a test may WRITE
  test_mode: {min: 0, max: 1}
```

Variable names are the ECU program's symbols, as you'd type them in PowerView, e.g. `cc_state` or `ctrl.mode`. The template [`examples/cruise_control/dspace.bench.yaml`](../examples/cruise_control/dspace.bench.yaml) shows the debugger and a dSPACE bench together.

## 4. Check it: read-only

```powershell
benchpilot check-bench --bench C:\hil\my-project\bench.yaml --read-var cc_state
```

On top of the bench checks, this reads each `--read-var` and every variable listed in `ecu_limits`, and confirms that an unknown symbol is rejected. **Nothing is written.**

To check one write, pick a variable that is safe to change on your target. BenchPilot writes it, reads it back, and restores the original value:

```powershell
benchpilot check-bench --bench C:\hil\my-project\bench.yaml --write-var test_mode=1
```

## Writing while the ECU runs

Reads and writes while the target is running need TRACE32's **run-time memory access** to be set up for your target. If it isn't, writes, and on some targets reads, fail while the ECU runs. See Lauterbach's documentation for `SYStem.MemAccess` on your processor family.

## Resets

By default BenchPilot **never resets the ECU**. To reset it before every test, list the PRACTICE commands to run, and set `reset_between_tests: true` in `bench.yaml` (the default):

```yaml
    reset_commands: ["SYStem.Up", "Go"]
```

Use whatever sequence your lab uses to bring the target up. These commands run exactly as written.

## Troubleshooting

| Message | What to do |
|---|---|
| `the trace32 adapter needs Lauterbach's module` | `pip install -e ".[trace32]"` |
| `could not connect to TRACE32 at localhost:20000` | Start PowerView, and check `RCL=NETTCP` and `PORT=` in its config file and in `bench.yaml`. |
| `TRACE32 read 'x' failed: ... symbol not found` | Check the symbol name and that the program's symbols (debug info) are loaded in PowerView. |
| `TRACE32 write ... failed` while running | Enable run-time memory access for your target (see above), or check the variable's type. |
| `blocked write ecu_var ...` | The value is outside `ecu_limits`. Fix the test, or the limit if the limit is wrong. |

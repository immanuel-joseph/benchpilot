"""Toy cruise-control ECU.

Inputs arrive as if from the vehicle bus (switch, buttons, pedals, speed); the
only output is a throttle command. Internal variables are exposed by name so a
virtual debugger can read and write them like a real one reads RAM.
"""

from __future__ import annotations

from enum import IntEnum


class CruiseState(IntEnum):
    OFF = 0
    STANDBY = 1
    ACTIVE = 2


class CruiseControlEcu:
    MIN_ENGAGE_KPH = 40.0
    MAX_SET_KPH = 160.0
    SET_STEP_KPH = 5.0
    BRAKE_CANCEL_THRESHOLD = 0.05
    KP = 0.12  # throttle per kph of error
    KI = 0.04  # throttle per kph·s of error

    # Names readable/writable through the debug adapter, mapped to attributes.
    VARIABLES = {
        "cc_state": "state",
        "cc_set_speed_kph": "set_speed_kph",
        "cc_integrator": "integrator",
        "throttle_cmd": "throttle_cmd",
    }

    # Injectable firmware bugs, for demos and for proving that tests catch them.
    # Each one overrides calibration constants on this ECU instance only.
    FAULTS = {
        "brake_cancel_ignored": ({"BRAKE_CANCEL_THRESHOLD": 1.01}, "brake does not cancel cruise"),
        "no_integral": ({"KI": 0.0}, "controller loses its integral term; sags on hills"),
        "low_min_engage": ({"MIN_ENGAGE_KPH": 20.0}, "cruise engages in slow traffic"),
        "double_accel_step": ({"SET_STEP_KPH": 10.0}, "ACCEL adds 10 kph instead of 5"),
    }

    def __init__(self, faults: list[str] | None = None) -> None:
        for name in faults or []:
            self.inject_fault(name)
        self.reset()

    def inject_fault(self, name: str) -> None:
        try:
            overrides, _ = self.FAULTS[name]
        except KeyError:
            known = ", ".join(self.FAULTS)
            raise ValueError(f"unknown fault {name!r}; known: {known}") from None
        for attr, value in overrides.items():
            setattr(self, attr, value)

    def reset(self) -> None:
        # Inputs
        self.main_switch = 0.0
        self.set_button = 0.0
        self.cancel_button = 0.0
        self.accel_button = 0.0
        self.accel_pedal = 0.0  # 0..1
        self.brake_pedal = 0.0  # 0..1
        self.vehicle_speed_kph = 0.0
        # Internal state
        self.state = CruiseState.OFF
        self.set_speed_kph = 0.0
        self.integrator = 0.0
        self._prev_buttons = (0.0, 0.0, 0.0)
        # Output
        self.throttle_cmd = 0.0

    def step(self, dt: float) -> None:
        set_edge, cancel_edge, accel_edge = self._button_edges()

        if self.main_switch < 0.5:
            self.state = CruiseState.OFF
        elif self.state == CruiseState.OFF:
            self.state = CruiseState.STANDBY

        if self.state == CruiseState.ACTIVE and (
            cancel_edge or self.brake_pedal > self.BRAKE_CANCEL_THRESHOLD
        ):
            self.state = CruiseState.STANDBY

        if (
            self.state == CruiseState.STANDBY
            and set_edge
            and self.vehicle_speed_kph >= self.MIN_ENGAGE_KPH
            and self.brake_pedal <= self.BRAKE_CANCEL_THRESHOLD
        ):
            self.state = CruiseState.ACTIVE
            self.set_speed_kph = min(self.vehicle_speed_kph, self.MAX_SET_KPH)
            # Start the integrator at the driver's pedal so engaging doesn't jerk.
            self.integrator = self.accel_pedal
        elif self.state == CruiseState.ACTIVE and accel_edge:
            self.set_speed_kph = min(self.set_speed_kph + self.SET_STEP_KPH, self.MAX_SET_KPH)

        if self.state == CruiseState.ACTIVE:
            error = self.set_speed_kph - self.vehicle_speed_kph
            command = self.KP * error + self.integrator
            # Anti-windup: stop integrating in the direction the command is saturated.
            pushing_past_max = command >= 1.0 and error > 0
            pushing_past_min = command <= 0.0 and error < 0
            if not (pushing_past_max or pushing_past_min):
                self.integrator = _clamp01(self.integrator + self.KI * error * dt)
            # The driver can always ask for more than cruise does.
            self.throttle_cmd = max(_clamp01(command), self.accel_pedal)
        else:
            self.throttle_cmd = self.accel_pedal

    def read(self, name: str) -> float:
        return float(getattr(self, self._attr(name)))

    def write(self, name: str, value: float) -> None:
        attr = self._attr(name)
        setattr(self, attr, CruiseState(int(value)) if attr == "state" else float(value))

    def _attr(self, name: str) -> str:
        try:
            return self.VARIABLES[name]
        except KeyError:
            known = ", ".join(self.VARIABLES)
            raise KeyError(f"unknown ECU variable {name!r}; known: {known}") from None

    def _button_edges(self) -> tuple[bool, bool, bool]:
        now = (self.set_button, self.cancel_button, self.accel_button)
        edges = tuple(cur >= 0.5 > prev for cur, prev in zip(now, self._prev_buttons, strict=True))
        self._prev_buttons = now
        return edges  # type: ignore[return-value]


def _clamp01(x: float) -> float:
    return min(1.0, max(0.0, x))

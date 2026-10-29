"""Simple longitudinal vehicle model.

One point mass pushed by the drive force and held back by aerodynamic drag,
rolling resistance, road grade and the brakes. Accurate enough to make a cruise
controller work for a living, nothing more.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

G = 9.81


@dataclass
class VehicleParams:
    mass_kg: float = 1500.0
    max_drive_force_n: float = 4000.0
    max_brake_force_n: float = 9000.0
    drag_coefficient: float = 0.30
    frontal_area_m2: float = 2.2
    air_density_kg_m3: float = 1.2
    rolling_coefficient: float = 0.012


class VehiclePlant:
    def __init__(self, params: VehicleParams | None = None):
        self.params = params or VehicleParams()
        self.reset()

    def reset(self) -> None:
        self.speed_mps = 0.0
        self.throttle = 0.0  # 0..1, commanded by the ECU
        self.brake = 0.0  # 0..1, from the brake pedal
        self.grade_pct = 0.0  # positive = uphill

    @property
    def speed_kph(self) -> float:
        return self.speed_mps * 3.6

    @speed_kph.setter
    def speed_kph(self, value: float) -> None:
        self.speed_mps = max(0.0, value / 3.6)

    def step(self, dt: float) -> None:
        p = self.params
        v = self.speed_mps
        theta = math.atan(self.grade_pct / 100.0)

        drive = p.max_drive_force_n * _clamp01(self.throttle)
        grade = p.mass_kg * G * math.sin(theta)
        # Drag, rolling resistance and brakes only act while the car is moving.
        resist = 0.0
        if v > 0:
            resist += 0.5 * p.air_density_kg_m3 * p.drag_coefficient * p.frontal_area_m2 * v * v
            resist += p.rolling_coefficient * p.mass_kg * G * math.cos(theta)
            resist += p.max_brake_force_n * _clamp01(self.brake)

        accel = (drive - grade - resist) / p.mass_kg
        self.speed_mps = max(0.0, v + accel * dt)


def _clamp01(x: float) -> float:
    return min(1.0, max(0.0, x))

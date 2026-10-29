"""Built-in simulation models: a vehicle plant and a toy cruise-control ECU."""

from benchpilot.sim.plant import VehicleParams, VehiclePlant
from benchpilot.sim.virtual_ecu import CruiseControlEcu, CruiseState

__all__ = ["CruiseControlEcu", "CruiseState", "VehicleParams", "VehiclePlant"]

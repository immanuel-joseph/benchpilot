"""Signal map: logical name -> adapter path."""

from __future__ import annotations

import difflib

from benchpilot.core.errors import UnknownSignal


class SignalMap:
    """Translates the logical names used in tests into a specific bench's paths.

    Tests say `vehicle.speed_kph`; the simulator calls it `plant/speed_kph` and a
    real bench might call it `Model Root/Vehicle/v_kph`. Only bench.yaml knows.
    """

    def __init__(self, mapping: dict[str, str]):
        self._to_path = dict(mapping)
        self._to_name = {path: name for name, path in self._to_path.items()}

    def resolve(self, name: str) -> str:
        try:
            return self._to_path[name]
        except KeyError:
            raise UnknownSignal(_unknown_message(name, self._to_path)) from None

    def name_of(self, path: str) -> str:
        return self._to_name.get(path, path)

    def __contains__(self, name: str) -> bool:
        return name in self._to_path

    @property
    def names(self) -> list[str]:
        return list(self._to_path)


def _unknown_message(name: str, known: dict[str, str]) -> str:
    message = f"unknown signal {name!r} (not in the bench's signal map)"
    close = difflib.get_close_matches(name, known, n=1)
    if close:
        message += f"; did you mean {close[0]!r}?"
    return message

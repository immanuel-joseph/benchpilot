# Cruise control: example requirements

Fictional requirements for the built-in virtual ECU. They exist so the example
tests have something to trace back to.

| ID | Requirement |
|---|---|
| REQ-CC-01 | With the main switch off the system shall be OFF (state 0). With it on, the system shall be in STANDBY (state 1). |
| REQ-CC-02 | Pressing SET in STANDBY at a vehicle speed of 40 kph or more shall engage cruise (ACTIVE, state 2) and use the current speed as the set speed. |
| REQ-CC-03 | SET shall be ignored below 40 kph. |
| REQ-CC-04 | While ACTIVE, the system shall hold the set speed within ±1.5 kph on grades from 0 to 5 %, and shall recover to that band within 10 s of a grade change. |
| REQ-CC-05 | Applying the brake pedal shall disengage cruise (STANDBY) within 100 ms and release the throttle. |
| REQ-CC-06 | Each ACCEL press while ACTIVE shall raise the set speed by 5 kph. The vehicle shall reach the new set speed ±1 kph within 15 s without overshooting it by more than 1.5 kph. |

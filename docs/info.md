<!---

This file is used to generate your project datasheet. Please fill in the information below and delete any unused
sections.

You can also include images in this folder and reference them in the markdown. Each image must be less than
512 kb in size, and the combined size of all images must be less than 1 MB.
-->

## How it works

Every winter people in Ciudad Juárez, Mexico, are poisoned by carbon monoxide (CO) from gas space
heaters and water heaters that run in closed rooms. Centinela-CO ("CO sentinel") is the decision
logic of a shut-off for such an appliance. It is a teaching design made by a team of beginners at the
IEEE Open Silicon Bootcamp 2026. It is **not** a certified safety component and must not be used to
protect people.

An external front end (an electrochemical CO sensor and an ADC) puts the CO concentration on `ui_in`
as an 8-bit number, 2 ppm per count, and pulses `STROBE` once per second. The chip:

1. **Integrates the dose.** Each sample adds `CO - 40 ppm` to an accumulator when the reading is at or
   above 40 ppm, and removes half of the difference (rounded up) when it is below: the dose rises fast
   and decays slowly. The valve permission is withdrawn when the dose reaches 2880 ppm·min. With one
   sample per second that is 8.0 min at 400 ppm, 26.2 min at 150 ppm and 96 min at 70 ppm, and never
   at 30 ppm, inside the alarm windows of UL 2034 (table 41.1).
2. **Computes it twice, in two different ways.** Channel A counts the dose up from zero. Channel B holds
   the one's complement and counts down. The two registers must always be complementary; any mismatch
   latches `FAULT`.
3. **Gives the permission as motion, not as a level.** `VALVE_A` and `VALVE_B` are 1.024 kHz square
   waves in quadrature. They come from a two-flop ring in which each flop advances only if its own
   channel agrees that running is safe. The valve driver must be AC-coupled, so a stuck output, a
   stopped clock or a dead chip all close the valve.
4. **Proves the trip path before opening the valve.** After reset both channels are driven to the trip
   dose and must report it, then one channel is deliberately disturbed and the comparator must notice.
   Only then is the dose cleared. The display shows `8` during this test (12 ms).
5. **Watches the samples.** The watchdog is fed only when both channels have really taken a sample. In
   field mode the sample period must stay between 0.75 s and 1.5 s, measured with the chip's own clock;
   over that whole range the trip times stay inside the UL 2034 windows (6 to 12 min at 400 ppm,
   20 to 39 min at 150 ppm, 72 to 144 min at 70 ppm). A silent or runaway front end latches `FAULT`.
6. **Asks for clean air first.** The valve can only be permitted after 64 consecutive samples below
   40 ppm. The count starts again after every reset, so cycling the power does not let anyone relight
   the appliance while CO is still in the room.
7. **Locks out.** After a trip the valve stays closed and `ALARM` pulses until the dose has fallen below
   half of the trip dose (at least 72 minutes in clean air) and someone presses `REARM` twice: once to
   acknowledge, once to permit the valve. A press only counts if the button was quiet for 0.5 s, so
   contact bounce is not a second press. The dose is integrated all the time: the alarm also trips with
   the valve closed, when the CO comes from somewhere else.

Display (7 segments, decimal point = valve permitted):

| Shown | Meaning |
|---|---|
| `8` | start-up self-test |
| `P` | standby, waiting for 64 clean samples |
| `0`-`9`, `A` | dose in steps of 8192 counts, about one tenth of the trip dose (standby or running) |
| `C` | tripped by CO, locked |
| `c` | tripped by CO, dose below one half: `REARM` will be accepted |
| `F` | fault: channels disagree, illegal state, samples lost or too fast, self-test failed. Reset to clear |

Known limits:

- One clock, one supply, one sensor input register and one supervisor are shared by both channels.
- The chip cannot detect a sensor that has lost sensitivity.
- There is no non-volatile memory: a reset clears the dose and the lock-out. Only the clean-air check
  remains.
- A fault that appears on the shared clear signal while running is only found by the next self-test.
- UL 2034 is a standard for alarms. This design only borrows its time-concentration curve as a trip
  criterion; it does not claim conformity with any standard.
- `ALARM` is a 4 Hz on/off envelope for a buzzer with its own oscillator. It is not asserted in `FAULT`.

Timing of the inputs: `ui_in` must be valid when `STROBE` rises and stay stable for 4 clock cycles.
`DEMO` is read once, at the end of the self-test. `REARM` needs a pulse of at least 3 clock cycles.

## How to test

Clock: 32768 Hz. `uio[3:0]` are inputs, `uio[7:4]` are outputs.

**Demo mode (no extra hardware).** Hold `DEMO` and `CHAIN_OK` high and reset. In demo mode the chip
samples `ui_in` 64 times per second, so time runs 64 times faster and the strobe is not needed.

```python
import time
from ttboard.demoboard import DemoBoard
tt = DemoBoard.get()
tt.shuttle.tt_um_utcj_centinela_co.enable()
tt.clock_project_PWM(32768)
tt.uio_oe_pico.value = 0b00001111      # the board drives uio[3:0]
tt.ui_in.value = 0                     # clean air
tt.uio_in.value = 0b1100               # DEMO = 1, CHAIN_OK = 1
tt.reset_project(True); tt.reset_project(False)
time.sleep(1.5)                        # display: 8, then P for one second, then 0
tt.uio_in.value = 0b1110; time.sleep(0.01); tt.uio_in.value = 0b1100   # press REARM: decimal point on
tt.ui_in.value = 200                   # 400 ppm
time.sleep(8)                          # after 7.5 s: display C, decimal point off, ALARM pulsing
```

Expected times in demo mode: 400 ppm (`ui_in` = 200) trips in 7.5 s, 150 ppm (75) in 24.5 s, 70 ppm (35)
in 90 s, 30 ppm (15) never. To set `ui_in` with the DIP switches, put the demo board in manual-input
mode first; by default the on-board microcontroller drives those pins.

Other checks:

- Drop `CHAIN_OK` while running: the decimal point goes off at once and `REARM` is refused until it is
  high again.
- After a trip, set `ui_in` to 0: the display stays at `C` for 68 s (72 min / 64) and then shows `c`.
  Press `REARM` (display: dose digit `5`), wait half a second and press again (decimal point on).
- Reset with `ui_in` at 20 or more (40 ppm): the display stays at `P` and `REARM` is refused.
- Leave the valve closed and set `ui_in` to 255: after 5.8 s the display shows `C` and `ALARM` pulses.
- Field mode (`DEMO` = 0 at reset): pulse `STROBE` once per second. `REARM` is accepted after 64 clean
  samples. Stop the pulses for more than 1.5 s, or send them faster than every 0.75 s, and the display
  shows `F`.
- Probe `VALVE_A` and `VALVE_B` with a scope: two 1.024 kHz square waves, 90 degrees apart, only while
  the decimal point is on.

The cocotb tests in `test/` check all of the above through the pins, and inject faults in the RTL.

## External hardware

None for demo mode: the microcontroller, switches and 7-segment display of the demo board are enough.

For a bench demonstrator: an electrochemical CO sensor with its amplifier, an 8-bit ADC with parallel
output and end-of-conversion pulse (or a microcontroller acting as front end), a debounced push button
for `REARM`, a buzzer on `ALARM`, and an AC-coupled driver (capacitor, rectifier and transistor) between
`VALVE_A` and a normally-closed gas solenoid valve.

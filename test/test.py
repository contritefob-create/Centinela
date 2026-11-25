# SPDX-FileCopyrightText: © 2026 Equipo UTCJ - IEEE Open Silicon Bootcamp 2026
# SPDX-License-Identifier: Apache-2.0
"""Tests for Centinela-CO.

Most tests only use the pins, so the same file runs on the RTL and on the
gate-level netlist. The slowest tests and the fault-injection tests (which
force internal signals) are skipped in the gate-level run (GATES=yes).
"""

import os

import cocotb
from cocotb.clock import Clock
from cocotb.handle import Force, Release
from cocotb.triggers import ClockCycles, RisingEdge

GATES = os.environ.get("GATES", "no") == "yes"

# 7-segment patterns, uo_out[6:0] = gfedcba
SEG = {0x3F: "0", 0x06: "1", 0x5B: "2", 0x4F: "3", 0x66: "4", 0x6D: "5",
       0x7D: "6", 0x07: "7", 0x7F: "8", 0x6F: "9", 0x77: "A", 0x39: "C", 0x58: "c",
       0x71: "F", 0x73: "P"}

# uio_in bits
STB, ARM, DEMO, CHAIN = 1, 2, 4, 8

TICK = 512        # clock cycles per 64 Hz tick (one sample in demo mode)
PURGE = 64        # clean samples needed before the valve can be permitted
QUIET = 33        # ticks the re-arm button must be quiet before a press counts
PERIOD = 25000    # field-mode strobe period in clocks (0.763 s, inside 0.75..1.5 s)
C0 = 20           # 40 ppm baseline
TH = 86400        # trip dose, in (2 ppm) x samples

# UL 2034 table 41.1, minutes: ppm -> (must not alarm before, must alarm by)
UL2034 = {70: (60, 240), 150: (10, 50), 400: (4, 15)}
# Field-mode strobe period accepted by the watchdog, in seconds
WINDOW = (0.75, 1.5)


def seg(dut):
    return SEG.get(int(dut.uo_out.value) & 0x7F, "?")


def permitted(dut):
    return (int(dut.uo_out.value) >> 7) & 1


def valve_a(dut):
    return (int(dut.uio_out.value) >> 4) & 1


def valve_b(dut):
    return (int(dut.uio_out.value) >> 5) & 1


def alarm(dut):
    return (int(dut.uio_out.value) >> 6) & 1


def fault(dut):
    return (int(dut.uio_out.value) >> 7) & 1


async def power_up(dut, uio, co):
    cocotb.start_soon(Clock(dut.clk, 30, unit="us").start())
    dut.ena.value = 1
    dut.ui_in.value = co
    dut.uio_in.value = uio
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)


async def start(dut, uio=CHAIN | DEMO, co=0):
    """Reset, wait for the self-test and (demo mode, clean air) the clean-air check."""
    await power_up(dut, uio, co)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 8)
    assert seg(dut) == "8", "self-test must show the lamp test"
    assert permitted(dut) == 0 and valve_a(dut) == 0 and valve_b(dut) == 0
    await ClockCycles(dut.clk, 420)
    assert fault(dut) == 0, "self-test failed"
    assert seg(dut) == "P", f"expected P (waiting for clean air), got {seg(dut)}"
    assert permitted(dut) == 0
    if (uio & DEMO) and co < C0:
        await ClockCycles(dut.clk, (PURGE + 2) * TICK)
        assert seg(dut) == "0", f"expected dose 0 after the clean-air check, got {seg(dut)}"


async def press_rearm(dut, bounce=False):
    """A deliberate press: quiet for 0.5 s, then the button goes down and up."""
    await ClockCycles(dut.clk, QUIET * TICK)
    dut.uio_in.value = int(dut.uio_in.value) | ARM
    await ClockCycles(dut.clk, 6)
    if bounce:                                   # contact bounce: a second edge
        dut.uio_in.value = int(dut.uio_in.value) & ~ARM
        await ClockCycles(dut.clk, 4)
        dut.uio_in.value = int(dut.uio_in.value) | ARM
        await ClockCycles(dut.clk, 6)
    dut.uio_in.value = int(dut.uio_in.value) & ~ARM
    await ClockCycles(dut.clk, 6)


async def edges(dut, getter, cycles):
    """Count the transitions of an output over a number of clock cycles."""
    last, n = getter(dut), 0
    for _ in range(cycles):
        await RisingEdge(dut.clk)
        now = getter(dut)
        n += now != last
        last = now
    return n


async def ring_is_stopped(dut):
    return await edges(dut, valve_a, 64) == 0 and await edges(dut, valve_b, 64) == 0


async def samples_until_trip(dut, limit):
    """Demo mode: count 64 Hz samples until the permission is removed."""
    for n in range(limit):
        await ClockCycles(dut.clk, TICK)
        if not permitted(dut):
            return n + 1
    return None


async def strobe(dut, period):
    """One sensor strobe followed by the rest of the period."""
    dut.uio_in.value = int(dut.uio_in.value) | STB
    await ClockCycles(dut.clk, 16)
    dut.uio_in.value = int(dut.uio_in.value) & ~STB
    await ClockCycles(dut.clk, period - 16)


async def check_trip_time(dut, ppm):
    await start(dut)
    await press_rearm(dut)
    assert permitted(dut) == 1, "valve should be permitted after re-arm"
    dut.ui_in.value = ppm // 2
    expected = -(-TH // (ppm // 2 - C0))         # ceil
    n = await samples_until_trip(dut, expected + 8)
    assert n is not None, f"no trip at {ppm} ppm"
    assert abs(n - expected) <= 2, f"expected {expected} samples, got {n}"
    lo, hi = UL2034[ppm]
    fast, slow = n * WINDOW[0] / 60, n * WINDOW[1] / 60
    dut._log.info(f"{ppm} ppm: trip after {n} samples = {n / 60:.1f} min at 1 Hz "
                  f"({fast:.1f} to {slow:.1f} min over the watchdog window)")
    assert lo < fast and slow < hi, \
        f"{fast:.1f}..{slow:.1f} min is outside the UL 2034 window {lo}-{hi}"
    await ClockCycles(dut.clk, 4)
    assert seg(dut) == "C" and fault(dut) == 0
    assert await ring_is_stopped(dut), "valve ring must be static after the trip"
    assert await edges(dut, alarm, 9000) >= 2, "alarm must beep"


# --------------------------------------------------------------------------
# Start-up and valve permission
# --------------------------------------------------------------------------
@cocotb.test()
async def test_selftest_and_standby(dut):
    await start(dut)
    assert await ring_is_stopped(dut)
    assert alarm(dut) == 0
    assert int(dut.uio_oe.value) == 0xF0


@cocotb.test()
async def test_valve_ring(dut):
    """Permission is two quadrature square waves, period 32 clocks (1.024 kHz)."""
    await start(dut)
    await press_rearm(dut)
    assert permitted(dut) == 1
    await ClockCycles(dut.clk, 40)
    assert await edges(dut, valve_a, 320) == 20
    assert await edges(dut, valve_b, 320) == 20
    seen = set()
    for _ in range(64):
        await RisingEdge(dut.clk)
        seen.add((valve_a(dut), valve_b(dut)))
    assert seen == {(0, 0), (1, 0), (1, 1), (0, 1)}


# --------------------------------------------------------------------------
# Trip curve
# --------------------------------------------------------------------------
@cocotb.test()
async def test_trip_400ppm(dut):
    await check_trip_time(dut, 400)


@cocotb.test()
async def test_trip_150ppm(dut):
    await check_trip_time(dut, 150)


@cocotb.test(skip=GATES)
async def test_trip_70ppm(dut):
    await check_trip_time(dut, 70)


@cocotb.test()
async def test_no_trip_at_30ppm(dut):
    """30 ppm never accumulates dose (UL 2034: no alarm at 30 ppm for 30 days)."""
    await start(dut)
    await press_rearm(dut)
    dut.ui_in.value = 15
    for _ in range(40):
        await ClockCycles(dut.clk, 25 * TICK)
        assert permitted(dut) == 1 and seg(dut) == "0" and fault(dut) == 0
    assert await edges(dut, valve_a, 320) == 20


@cocotb.test()
async def test_dose_rises_fast_and_decays_slowly(dut):
    """200 samples at 510 ppm, 100 in clean air, then 510 ppm again."""
    await start(dut)
    await press_rearm(dut)
    dut.ui_in.value = 255
    await ClockCycles(dut.clk, 200 * TICK)       # 200 x 235 = 47000
    assert permitted(dut) == 1 and seg(dut) == "5"
    dut.ui_in.value = 0
    await ClockCycles(dut.clk, 100 * TICK)       # decays 100 x 10 = 1000 -> 46000
    assert permitted(dut) == 1 and seg(dut) == "5"
    dut.ui_in.value = 255
    n = await samples_until_trip(dut, 200)
    # (86400 - 46000) / 235 = 171.9 -> 172 samples. Without decay it would be 168,
    # with full-rate decay 176.
    assert n is not None and 171 <= n <= 173, f"second exposure tripped after {n}"


@cocotb.test()
async def test_alarm_without_running(dut):
    """CO from another source: the alarm trips even if the valve was never opened."""
    await start(dut)
    dut.ui_in.value = 255
    await ClockCycles(dut.clk, 372 * TICK)
    assert seg(dut) == "C" and permitted(dut) == 0 and fault(dut) == 0
    assert await edges(dut, alarm, 9000) >= 2


# --------------------------------------------------------------------------
# Interlocks
# --------------------------------------------------------------------------
@cocotb.test()
async def test_safety_chain(dut):
    """Opening the external safety chain closes the valve at once."""
    await start(dut)
    await press_rearm(dut)
    assert permitted(dut) == 1
    dut.uio_in.value = DEMO                      # chain_ok low
    await ClockCycles(dut.clk, 6)
    assert permitted(dut) == 0 and fault(dut) == 0
    assert await ring_is_stopped(dut)
    await press_rearm(dut)                       # re-arm refused with the chain open
    assert permitted(dut) == 0
    dut.uio_in.value = DEMO | CHAIN
    await press_rearm(dut)
    assert permitted(dut) == 1


@cocotb.test()
async def test_clean_air_is_required(dut):
    """No permission until 64 consecutive samples are below 40 ppm."""
    await start(dut, co=30)                      # 60 ppm from power-up
    await ClockCycles(dut.clk, 100 * TICK)
    assert seg(dut) == "P"
    await press_rearm(dut)
    assert permitted(dut) == 0, "re-arm must be refused while the air is not clean"
    dut.ui_in.value = 0
    await ClockCycles(dut.clk, 20 * TICK)
    dut.ui_in.value = 30                         # one dirty spell restarts the count
    await ClockCycles(dut.clk, 3 * TICK)
    dut.ui_in.value = 0
    await press_rearm(dut)                       # 33 clean samples so far
    assert permitted(dut) == 0 and seg(dut) == "P"
    await ClockCycles(dut.clk, 35 * TICK)
    assert seg(dut) == "0"
    await press_rearm(dut)
    assert permitted(dut) == 1


@cocotb.test()
async def test_standby_refuses_rearm_above_half_dose(dut):
    """With clean air but more than half the trip dose, re-arm is refused."""
    await start(dut)
    await press_rearm(dut)
    dut.ui_in.value = 255
    await ClockCycles(dut.clk, 200 * TICK)       # 47000 > 43200, not tripped
    assert permitted(dut) == 1 and seg(dut) == "5"
    dut.ui_in.value = 0
    dut.uio_in.value = DEMO                      # chain opens -> standby
    await ClockCycles(dut.clk, 6)
    dut.uio_in.value = DEMO | CHAIN
    await ClockCycles(dut.clk, 70 * TICK)        # air is clean again, dose 46300
    assert seg(dut) == "5"
    await press_rearm(dut)
    assert permitted(dut) == 0, "re-arm accepted with more than half the trip dose"
    await ClockCycles(dut.clk, 300 * TICK)       # decays below 43200
    await press_rearm(dut)
    assert permitted(dut) == 1


@cocotb.test()
async def test_lockout_survives_long_exposure(dut):
    """The dose is held after the trip: it must not wrap around and unlock."""
    await start(dut)
    await press_rearm(dut)
    dut.ui_in.value = 255
    assert await samples_until_trip(dut, 380) is not None
    await ClockCycles(dut.clk, 260 * TICK)       # 190 more samples would overflow 17 bits
    dut.ui_in.value = 0
    await press_rearm(dut)
    await press_rearm(dut)
    assert seg(dut) == "C" and permitted(dut) == 0, "lock-out lost after a long exposure"
    assert fault(dut) == 0


@cocotb.test()
async def test_reset_does_not_bypass_co(dut):
    """Cycling the power after a trip cannot relight while CO is still present."""
    await start(dut)
    await press_rearm(dut)
    dut.ui_in.value = 255
    assert await samples_until_trip(dut, 380) is not None
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 430)
    assert seg(dut) == "P" and fault(dut) == 0
    for _ in range(4):
        await press_rearm(dut)
        assert permitted(dut) == 0
        assert await ring_is_stopped(dut)


@cocotb.test(skip=GATES)
async def test_rearm_lockout(dut):
    """After a trip the valve stays closed until the dose has halved, and it
    takes two deliberate presses (bounce does not count as the second one)."""
    await start(dut)
    await press_rearm(dut)
    dut.ui_in.value = 255
    assert await samples_until_trip(dut, 380) is not None
    dut.ui_in.value = 0
    await press_rearm(dut)
    assert seg(dut) == "C" and permitted(dut) == 0, "re-arm must be refused"
    # the dose decays 10 per sample in clean air: half of TH needs about 4330 samples
    await ClockCycles(dut.clk, 4150 * TICK)
    assert seg(dut) == "C", "still above half dose"
    await ClockCycles(dut.clk, 200 * TICK)
    assert seg(dut) == "c", "below half dose the display must offer the re-arm"
    await press_rearm(dut, bounce=True)
    assert seg(dut) == "5" and permitted(dut) == 0, "first press: back to standby only"
    await press_rearm(dut)
    assert permitted(dut) == 1, "second press: valve permitted again"


# --------------------------------------------------------------------------
# Field mode and sensor watchdog
# --------------------------------------------------------------------------
@cocotb.test()
async def test_field_mode_integrates_on_strobes(dut):
    """One sample per external strobe: the dose digit changes on the 35th sample."""
    await start(dut, uio=CHAIN)
    for _ in range(PURGE + 1):                   # clean air
        await strobe(dut, PERIOD)
    assert seg(dut) == "0" and fault(dut) == 0
    await press_rearm(dut)
    assert permitted(dut) == 1
    dut.ui_in.value = 255
    for _ in range(34):                          # 34 x 235 = 7990 < 8192
        await strobe(dut, PERIOD)
    assert seg(dut) == "0" and permitted(dut) == 1 and fault(dut) == 0
    await strobe(dut, PERIOD)                    # 35 x 235 = 8225
    assert seg(dut) == "1" and permitted(dut) == 1 and fault(dut) == 0


@cocotb.test()
async def test_watchdog_window(dut):
    """Strobe periods of 0.80 s and 1.45 s are accepted, 0.70 s is not."""
    await start(dut, uio=CHAIN)
    for period in (26300, 26300, 47500, 47500):
        await strobe(dut, period)
    assert fault(dut) == 0
    await strobe(dut, 22900)
    assert fault(dut) == 0
    await strobe(dut, 100)                       # this edge came 0.70 s after the last
    assert fault(dut) == 1 and seg(dut) == "F"


@cocotb.test()
async def test_watchdog_silent_sensor(dut):
    await start(dut, uio=CHAIN)
    await strobe(dut, PERIOD)
    await ClockCycles(dut.clk, 20000)            # 1.37 s since the last strobe: still fine
    assert fault(dut) == 0
    await ClockCycles(dut.clk, 6000)             # 1.56 s: fault
    assert fault(dut) == 1 and seg(dut) == "F" and permitted(dut) == 0
    assert await ring_is_stopped(dut)
    await strobe(dut, PERIOD)
    await press_rearm(dut)
    assert fault(dut) == 1, "fault must stay latched until reset"


@cocotb.test()
async def test_watchdog_never_started(dut):
    """A sensor that never strobes: no re-arm, then fault after 1.5 s."""
    await start(dut, uio=CHAIN)
    await press_rearm(dut)
    assert permitted(dut) == 0
    await ClockCycles(dut.clk, 36000)
    assert fault(dut) == 1


@cocotb.test()
async def test_watchdog_runaway_sensor(dut):
    await start(dut, uio=CHAIN)
    await strobe(dut, PERIOD)
    assert fault(dut) == 0
    await strobe(dut, 4096)                      # 0.125 s: far too fast
    await strobe(dut, 4096)
    assert fault(dut) == 1 and seg(dut) == "F"


# --------------------------------------------------------------------------
# Fault injection (internal signals: RTL only)
# --------------------------------------------------------------------------
async def selftest_with_fault(dut, signals, value):
    """Start-up self-test with internal signals stuck: it must end in FAULT."""
    await power_up(dut, CHAIN | DEMO, 0)
    for name in signals:
        getattr(dut.user_project, name).value = Force(value)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 430)
    f, s = fault(dut), seg(dut)
    for name in signals:
        getattr(dut.user_project, name).value = Release()
    assert f == 1 and s == "F", f"self-test passed with {signals} stuck at {value}"
    await press_rearm(dut)
    assert permitted(dut) == 0


@cocotb.test(skip=GATES)
async def test_selftest_fails_if_dose_cannot_rise(dut):
    await selftest_with_fault(dut, ["upd_a", "upd_b"], 0)


@cocotb.test(skip=GATES)
async def test_selftest_fails_if_comparator_is_blind(dut):
    await selftest_with_fault(dut, ["mism"], 0)


@cocotb.test(skip=GATES)
async def test_channel_mismatch_is_caught(dut):
    """Corrupt one channel: the comparator must latch FAULT and stop the ring."""
    await start(dut)
    await press_rearm(dut)
    dut.ui_in.value = 100
    await ClockCycles(dut.clk, 20 * TICK)
    assert permitted(dut) == 1
    dut.user_project.acc_b.value = 0x1F000       # single-channel corruption
    await ClockCycles(dut.clk, 4)
    assert fault(dut) == 1 and seg(dut) == "F" and permitted(dut) == 0
    assert await ring_is_stopped(dut)


@cocotb.test(skip=GATES)
async def test_illegal_state_is_caught(dut):
    await start(dut)
    await press_rearm(dut)
    assert permitted(dut) == 1
    dut.user_project.st.value = 0b001100         # two states at once
    await ClockCycles(dut.clk, 4)
    assert fault(dut) == 1 and permitted(dut) == 0
    assert await ring_is_stopped(dut)


@cocotb.test(skip=GATES)
async def test_one_blind_channel_is_caught(dut):
    """Channel A stuck at zero dose: the comparator stops the valve as soon as
    channel B moves, long before the dose is reached."""
    await start(dut)
    await press_rearm(dut)
    dut.ui_in.value = 200
    for _ in range(4):
        await ClockCycles(dut.clk, TICK)
        dut.user_project.acc_a.value = 0         # channel A cannot accumulate
    await ClockCycles(dut.clk, 4)
    assert permitted(dut) == 0 and fault(dut) == 1


@cocotb.test(skip=GATES)
async def test_ring_stops_even_if_fsm_is_stuck_in_run(dut):
    """Each ring flop is gated by its own channel, independently of the state machine."""
    await start(dut)
    await press_rearm(dut)
    dut.user_project.st.value = Force(0b001000)
    dut.ui_in.value = 255
    await ClockCycles(dut.clk, 380 * TICK)
    stopped = await ring_is_stopped(dut)
    dut.user_project.st.value = Release()
    assert stopped, "ring kept running past the trip dose with the FSM stuck in RUN"


@cocotb.test(skip=GATES)
async def test_frozen_sampling_is_caught(dut):
    """If no sample reaches the channels, the watchdog must stop the valve."""
    await start(dut)
    await press_rearm(dut)
    assert permitted(dut) == 1
    dut.user_project.samp.value = Force(0)
    dut.ui_in.value = 255
    await ClockCycles(dut.clk, 110 * TICK)       # more than 1.5 s without samples
    f, stopped = fault(dut), await ring_is_stopped(dut)
    dut.user_project.samp.value = Release()
    assert f == 1 and stopped, "valve stayed permitted with the sampling path frozen"

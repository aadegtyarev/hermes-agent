"""Autotests for the wb-wiring ERC layer (SKiDL electrical twin).

schemdraw draws the picture (test_wb_wiring_skill.py); this module verifies the
*electrical* model: a well-formed circuit must validate to [], and each seeded
fault must be caught. Skipped where SKiDL isn't installed (it IS in the deploy
image, see deploy/multi-agent/Dockerfile).
"""
import os
import sys

import pytest

pytest.importorskip("skidl")

_SKILL = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..",
    "deploy", "multi-agent", "base", "skills", "wb-wiring", "references"))
sys.path.insert(0, _SKILL)

import wb_netlist as nl  # noqa: E402


# --- clean circuits validate to [] -----------------------------------------
def test_clean_bus_validates_clean():
    nl.new_circuit()
    nl.bus(nl.wb("WB7"), nl.wb("WB-MR6C v.2"), nl.wb("WB-MCM8"))
    assert nl.validate() == []


def test_clean_dimmer_with_lamps_validates_clean():
    nl.new_circuit()
    ctrl, dim = nl.wb("WB7"), nl.wb("WB-MDM3")
    nl.bus(ctrl, dim)
    ac = nl.mains(phases=1, pe=False)
    L, N = nl.Net("L"), nl.Net("N")
    L += ac["L"], dim["L"]
    N += ac["N"], dim["N"]
    for i in range(1, 4):
        lamp = nl.load(f"LAMP{i}")
        dim[f"O{i}"] += lamp["1"]
        N += lamp["2"]
    assert nl.validate() == []


# --- each seeded fault must be caught --------------------------------------
def test_unpowered_device_is_caught():
    nl.new_circuit()
    ctrl, relay = nl.wb("WB7"), nl.wb("WB-MR6C v.2")
    vp, gnd = nl.power_rails()
    a, b = nl.rs485_pair()
    ps = nl.psu()
    vp += ps["V+"], ctrl["V+"]          # relay V+ intentionally left floating
    gnd += ps["GND"], ctrl["GND"], relay["GND"]
    a += ctrl["A"], relay["A"]
    b += ctrl["B"], relay["B"]
    nl.unused(*[relay[f"K{i}"] for i in range(1, 7)],
              *[relay[f"COM{i}"] for i in range(1, 7)])
    issues = nl.validate()
    assert any("V+" in m for m in issues), issues


def test_shorted_ab_bus_is_caught():
    nl.new_circuit()
    ctrl, dev = nl.wb("WB7"), nl.wb("WB-MSW v.4")
    vp, gnd = nl.power_rails()
    ps = nl.psu()
    ab = nl.Net("AB")
    ab.drive = nl.POWER
    vp += ps["V+"]
    gnd += ps["GND"]
    for d in (ctrl, dev):
        vp += d["V+"]
        gnd += d["GND"]
        ab += d["A"], d["B"]            # A and B tied together == RS-485 fault
    assert any("A and B" in m for m in nl.validate())


def test_load_without_return_is_caught():
    nl.new_circuit()
    ctrl, dim = nl.wb("WB7"), nl.wb("WB-MDM3")
    nl.bus(ctrl, dim)
    ac = nl.mains(phases=1, pe=False)
    nl.Net("L").connect(ac["L"], dim["L"])
    nl.Net("N").connect(ac["N"], dim["N"])
    lamp = nl.load("LAMP")
    dim["O1"] += lamp["1"]              # lamp["2"] left dangling (no neutral)
    assert any("LAMP" in m for m in nl.validate())


# --- anti-fabrication: catalog pins the channel count ----------------------
def test_catalog_rejects_fabricated_channel_count():
    nl.new_circuit()
    with pytest.raises(ValueError):
        nl.wb("WB-MDM3", channels=2)   # WB-MDM3 is a 3-channel dimmer


def test_catalog_builds_known_models():
    for model in nl.CATALOG:
        nl.new_circuit()
        assert nl.wb(model) is not None


def test_catalog_relay_rejects_wrong_channel_count():
    nl.new_circuit()
    with pytest.raises(ValueError):
        nl.wb("WB-MRM2-mini", channels=6)   # it is a 2-channel relay


# --- typical connection scenarios all validate clean ----------------------
@pytest.mark.parametrize("scenario", [
    nl.scenario_relay_load,
    nl.scenario_contactor_motor,
    nl.scenario_dimming_0_10v,
    nl.scenario_inputs,
])
def test_scenarios_validate_clean(scenario):
    nl.new_circuit()
    scenario()
    assert nl.validate() == []


def test_contactor_coil_unpowered_is_caught():
    # coil fed on A1 through the relay contact, but A2 left off neutral
    nl.new_circuit()
    ctrl, relay = nl.wb("WB7"), nl.wb("WB-MR6C v.2")
    nl.bus(ctrl, relay)
    km = nl.contactor("KM1", poles=1)
    live = nl.Net("live")
    live.drive = nl.POWER
    relay["K1"] += live
    km["A1"] += live                    # coil A2 intentionally left floating
    nl.unused(km["L1"], km["T1"])
    assert any("A2" in m for m in nl.validate())


def test_dimming_signal_floating_is_caught():
    nl.new_circuit()
    ctrl, ao = nl.wb("WB7"), nl.wb("WB-MAO4")
    nl.bus(ctrl, ao)
    ac = nl.mains(phases=1, pe=False)
    drv = nl.led_driver_0_10v("DRV1")
    nl.Net("L").connect(ac["L"], drv["L"])
    nl.Net("N").connect(ac["N"], drv["N"])
    # DIM+/DIM- and LED+/LED- left unwired -> dangling control + output
    issues = nl.validate()
    assert any("DIM" in m or "LED" in m for m in issues), issues

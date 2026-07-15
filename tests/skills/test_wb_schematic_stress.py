"""Stress/robustness tests for the 2D schematic renderer (wb_schematic).

The renderer's fragile part is the collision resolver (nudging rows until no two
risers collide). These tests lock in that it stays clean across realistic WB
installs and a seeded fuzz of random buses — the "keep it readable for a support
ticket" guarantee. Every rendered diagram must pass ALL geometric audits.

Skipped where schemdraw/skidl aren't installed (both are in the deploy image).
"""
import os
import random
import sys

import pytest

pytest.importorskip("schemdraw")
pytest.importorskip("skidl")

_SKILL = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..",
    "deploy", "multi-agent", "base", "skills", "wb-wiring", "references"))
sys.path.insert(0, _SKILL)

import wb_blocks as wb  # noqa: E402
import wb_netlist as nl  # noqa: E402
import wb_schematic as ws  # noqa: E402


def _clean(d):
    return (wb.audit_drawing(d) == [] and wb.audit_overlaps(d) == []
            and wb.audit_dots(d) == [] and wb.audit_labels(d) == [])


# realistic mixed installs (bare bus of typical device combos)
_MIXES = {
    "lighting":    ["WB7", "WB-MDM3", "WB-MDM3", "WB-MRGBW-D", "WB-MCM8"],
    "hvac_power":  ["WB7", "WB-MR6C v.2", "WB-MAP3ET", "WB-MSW v.4"],
    "water":       ["WB7", "WB-MWAC v.2", "WB-MR6C v.2"],
    "metering":    ["WB7", "WB-MAP6S", "WB-MAP3ET", "WB-MAP3EV"],
    "sensors":     ["WB7", "WB-MSW v.4", "WB-MS v.2", "WB-M1W2 v.3", "WB-MAI6"],
    "big_mixed":   ["WB7", "WB-MR6C v.2", "WB-MR6C v.2", "WB-MDM3", "WB-MDM3",
                    "WB-MCM8", "WB-MAI6", "WB-MAP6S", "WB-MSW v.4"],
    "relays_x6":   ["WB7"] + ["WB-MR6C v.2"] * 6,
    "dimmers_x5":  ["WB7"] + ["WB-MDM3"] * 5,
}


@pytest.mark.parametrize("models", _MIXES.values(), ids=list(_MIXES))
def test_realistic_mixes_render_clean(models):
    nl.new_circuit()
    nl.bus(*[nl.wb(m) for m in models])
    assert nl.validate() == []
    assert _clean(ws.render())


def test_controller_ethernet_lan_renders_clean():
    nl.new_circuit()
    nl.bus(nl.wb("WB7"), nl.wb("WB-MDM3"), nl.wb("WB-MCM8"), add_lan=True)
    assert nl.validate() == []
    assert _clean(ws.render())          # controller ETH -> LAN node, still clean


def test_fuzz_load_heavy_scenes_render_clean():
    """Valid load-heavy scenes (modules each driving >=1 real load, plus input
    switches) must be ERC-clean AND render with all geometric audits empty. This
    is the coverage the bare-bus fuzz missed."""
    rng = random.Random(3)
    for _ in range(30):
        nl.new_circuit()
        ctrl = nl.wb("WB7")
        mods = [nl.wb("WB-MR6C v.2") for _ in range(rng.randint(1, 3))]
        mcm = nl.wb("WB-MCM8")
        nl.bus(ctrl, *mods, mcm)
        ac = nl.mains(phases=1, pe=False)
        L, N = nl.Net("L"), nl.Net("N")
        L += ac["L"]
        N += ac["N"]
        k = 0
        for m in mods:
            for i in range(1, rng.randint(1, 3) + 1):   # >=1 load per module
                ld = rng.choice([nl.lamp, nl.fan, nl.heater, nl.pump])(f"LD{k}")
                k += 1
                sw = nl.Net(f"o{k}")
                sw.drive = nl.POWER
                L += m[f"COM{i}"]
                sw += m[f"K{i}"], ld["1"]
                N += ld["2"]
        ig = nl.Net("iGND")
        ig.drive = nl.POWER
        ig += mcm["iGND"]
        for i in (1, 2):
            s = nl.switch(f"SW{i}")
            nl.Net(f"in{k}{i}").connect(mcm[f"In{i}"], s["1"])
            ig += s["2"]
        assert nl.validate() == []
        assert _clean(ws.render())


def test_fuzz_random_buses_render_clean():
    pool = [m for m in nl.CATALOG if nl.CATALOG[m][0] != "controller"]
    rng = random.Random(1)
    for _ in range(40):
        models = ["WB7"] + [rng.choice(pool) for _ in range(rng.randint(1, 11))]
        nl.new_circuit()
        nl.bus(*[nl.wb(m) for m in models])
        assert nl.validate() == []
        assert _clean(ws.render()), models

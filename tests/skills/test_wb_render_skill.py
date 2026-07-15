"""Autotests for the netlist-driven renderer + trace/label auditors.

The point of drawing FROM the netlist is that ERC validates the SAME graph that
gets drawn. These tests lock that in: every scenario, rendered from its circuit,
must pass ALL geometric audits (no floating ends, no trace overlaps, no literal-
escape labels) — and the auditors must actually fire on seeded faults.

Skipped where schemdraw/skidl aren't installed (both are in the deploy image).
"""
import os
import sys

import pytest

pytest.importorskip("schemdraw")
pytest.importorskip("skidl")

_SKILL = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..",
    "deploy", "multi-agent", "base", "skills", "wb-wiring", "references"))
sys.path.insert(0, _SKILL)

import schemdraw.elements as e  # noqa: E402
import wb_blocks as wb  # noqa: E402
import wb_netlist as nl  # noqa: E402
import wb_schematic as ws  # noqa: E402


# --- the overlap auditor must fire on seeded overlaps, not on clean crosses -
def test_overlap_auditor_catches_horizontal_overlap():
    d = wb.new_drawing()
    d += e.Line().at((0, 0)).to((4, 0))
    d += e.Line().at((2, 0)).to((6, 0))   # collinear, overlapping in x
    assert wb.audit_overlaps(d)


def test_overlap_auditor_catches_vertical_overlap():
    d = wb.new_drawing()
    d += e.Line().at((0, 0)).to((0, -4))
    d += e.Line().at((0, -2)).to((0, -6))  # collinear, overlapping in y
    assert wb.audit_overlaps(d)


def test_overlap_auditor_allows_perpendicular_cross():
    d = wb.new_drawing()
    d += e.Line().at((0, 0)).to((4, 0))
    d += e.Line().at((2, 2)).to((2, -2))   # right-angle crossing at a point
    assert wb.audit_overlaps(d) == []


# --- the label auditor must catch a LITERAL escape but allow a real newline -
def test_label_auditor_catches_literal_backslash_n():
    d = wb.new_drawing()
    d += e.Label().at((0, 0)).label("WB-MDM3" + chr(92) + "nDimmer 2ch")
    assert wb.audit_labels(d)


def test_label_auditor_allows_real_newline():
    d = wb.new_drawing()
    d += e.Label().at((0, 0)).label("WB-MDM3\nDimmer")
    assert wb.audit_labels(d) == []


# --- 2D schematic renderer (wb_schematic): every scenario renders clean -----
def _all_clean(d):
    return (wb.audit_drawing(d) == [] and wb.audit_overlaps(d) == []
            and wb.audit_dots(d) == [] and wb.audit_labels(d) == [])


@pytest.mark.parametrize("scenario", [
    nl.scenario_relay_load,
    nl.scenario_contactor_motor,
    nl.scenario_dimming_0_10v,
    nl.scenario_inputs,
])
def test_schematic_scenarios_are_clean(scenario):
    nl.new_circuit()
    scenario()
    assert nl.validate() == []
    assert _all_clean(ws.render())


def test_schematic_multimodule_combos_are_clean():
    # the combos the tool must keep readable for a WB support ticket
    combos = [
        lambda: nl.bus(nl.wb("WB7"), *[nl.wb("WB-MDM3") for _ in range(3)]),
        lambda: nl.bus(nl.wb("WB7"), nl.wb("WB-MR6C v.2"),
                       nl.wb("WB-MR6C v.2"), nl.wb("WB-MCM8")),
        lambda: nl.bus(nl.wb("WB7"), *[nl.wb("WB-MDM3") for _ in range(3)],
                       nl.wb("WB-MR6C v.2"), nl.wb("WB-MCM8"), nl.wb("WB-MAP3ET")),
    ]
    for build in combos:
        nl.new_circuit()
        build()
        assert nl.validate() == []
        assert _all_clean(ws.render())


# --- dots: only at real electrical junctions, never on corners/crossings ----
def test_dot_auditor_flags_corner_dot():
    d = wb.new_drawing()
    d += e.Line().at((0, 0)).to((2, 0)).color("red")
    d += e.Line().at((2, 0)).to((2, -2)).color("red")
    d += e.Dot().at((2, 0)).color("red")           # L-corner: 2 arms, no junction
    assert wb.audit_dots(d)


def test_dot_auditor_allows_t_junction():
    d = wb.new_drawing()
    d += e.Line().at((0, 0)).to((4, 0)).color("red")
    d += e.Line().at((2, 0)).to((2, -2)).color("red")
    d += e.Dot().at((2, 0)).color("red")           # T: 3 arms of one net
    assert wb.audit_dots(d) == []


def test_dot_auditor_flags_crossing_of_different_nets():
    d = wb.new_drawing()
    d += e.Line().at((0, 0)).to((4, 0)).color("red")
    d += e.Line().at((2, 2)).to((2, -2)).color("blue")
    d += e.Dot().at((2, 0)).color("red")           # crossing, not a connection
    assert wb.audit_dots(d)

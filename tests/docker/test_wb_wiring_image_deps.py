"""Image build-check for the wb-wiring skill's runtime dependencies.

The deploy overlay (deploy/multi-agent/Dockerfile) adds ``skidl`` next to the
existing ``schemdraw`` so the wb-wiring skill can both draw (schemdraw) and
electrically validate (SKiDL ERC). This test proves those deps install cleanly
in a fresh container and that BOTH skill layers actually run there — the failure
we want to catch is "added a dep that doesn't resolve / import in the image".

It installs into a stock ``python:3.12-slim`` rather than rebuilding the heavy
overlay (which needs the multi-GB ``hermes-agent:base``); the dependency
resolution + import is what the overlay's pip layer risks, and that is exactly
what this exercises. Skipped automatically when Docker is unavailable (see
tests/docker/conftest.py).
"""
from __future__ import annotations

import os
import subprocess

_REFS = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..",
    "deploy", "multi-agent", "base", "skills", "wb-wiring", "references"))

# same third-party deps the overlay's pip layer installs for wb-wiring
_DEPS = "skidl schemdraw matplotlib"

_PROBE = """
import sys; sys.path.insert(0, "/refs")
import wb_netlist as nl, wb_blocks as wb, wb_schematic as ws
# electrical layer: a clean bus must validate to []
nl.new_circuit()
nl.bus(nl.wb("WB7"), nl.wb("WB-MR6C v.2"), nl.wb("WB-MCM8"))
assert nl.validate() == [], nl.validate()
# a typical scenario, drawn FROM its netlist, passes every geometric audit
nl.new_circuit(); nl.scenario_contactor_motor()
assert nl.validate() == [], nl.validate()
d = ws.render()
assert wb.audit_drawing(d) == []
assert wb.audit_overlaps(d) == []
assert wb.audit_labels(d) == []
print("WB_WIRING_IMAGE_DEPS_OK")
"""


def test_wb_wiring_deps_install_and_run_in_container():
    cmd = (
        f"pip install --no-cache-dir {_DEPS} >/tmp/pip.log 2>&1 "
        f"|| {{ echo PIP_FAILED; tail -30 /tmp/pip.log; exit 1; }}; "
        f"python -c '{_PROBE}'"
    )
    result = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{_REFS}:/refs:ro",
         "python:3.12-slim", "bash", "-c", cmd],
        capture_output=True, text=True, timeout=600,
    )
    assert result.returncode == 0, (
        f"container build-check failed:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}")
    assert "WB_WIRING_IMAGE_DEPS_OK" in result.stdout, result.stdout

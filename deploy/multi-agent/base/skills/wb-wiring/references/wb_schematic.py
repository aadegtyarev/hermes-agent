"""2D schematic renderer from the netlist — the readable, scalable layout.

Draws the SAME SKiDL circuit ERC validates, laid out by VOLTAGE (panel/safety
convention): HIGH-voltage field devices (mains L/N/PE, lamps, dimmer phase-cut
outputs, relay contacts, contactors, motors) on TOP; LOW-voltage devices (24V
power, RS-485, dry-contact inputs/switches, 0-10V, DC LED, sensors, PSU) on the
BOTTOM; modules in the middle (controller first, refs U1.., Ethernet -> LAN via
bus(add_lan=True)). The WB bus is ONE thick trunk carrying V+/GND/A/B — GND is
the shared reference, so RS-485 is 3-wire A/B/GND (a 2-wire A/B bus is the
deliberate exception on 230V-local devices). Segmented trunk (taps are shared
endpoints -> no floating) with labelled 90° breakouts. Scales to the ~15 modules
a WB support ticket needs while staying readable.

Clean by construction + resolver:
- every net owns a UNIQUE lane y in its bank -> horizontals never overlap;
- risers drop at each pin's x; the only way two risers collide is a top/PSU-row
  pin sharing a module pin's x, which render() clears by nudging those rows a
  small phase until `audit_overlaps() == [] and audit_dots() == []`;
- dots only at genuine interior T-junctions (a real riser meeting a lane between
  its ends), never on corners/crossings.

Every canonical scenario + multi-module combo renders with all audits empty
(see tests/skills/test_wb_render_skill.py). History/rationale of getting here:
`DEVNOTES-schematic-tool.md`.

Schematic-style renderer from the netlist — bus-bar spine layout.

Draws the SAME SKiDL circuit ERC validates, but in a readable 2D schematic shape
that scales to ~15 modules (WB support tickets, not ASICs):

    L / L1..L3 / N / PE  ── rails across the top ───────────────────────────
         loads / switches sit in each module's column, above it
    ┌ U1 ┐   ┌ U2 ┐   ┌ U3 ┐  … module row (controller first), ref-designated
    └─┬──┘   └─┬──┘   └─┬──┘
    ══╪════════╪════════╪════  RS-485 + 24V bus trunk (one line) + 90° breakouts
      G1 PSU feeds the bus at the left

Rules kept: orthogonal only, no trace overlaps, dots only at real junctions.
The bus trunk is a visual bundle (one thick line + labelled breakouts); the
electrical connectivity of V+/GND/A/B is what ERC guarantees, not the picture.

    import wb_netlist as nl, wb_schematic as ws, wb_blocks as wb
    nl.new_circuit(); nl.scenario_dimming_0_10v()
    assert nl.validate() == []
    d = ws.render()
    assert wb.audit_overlaps(d) == [] and wb.audit_dots(d) == []
    d.save("/tmp/wb.png", dpi=wb.STYLE["dpi"])
"""
import builtins
import re

import schemdraw.elements as e

import wb_blocks as wb
import wb_netlist as nl

F = nl.F
STYLE = wb.STYLE

_BUS_PINS = ("V+", "GND", "A", "B")


def _circuit():
    return builtins.default_circuit


def _conn_pins(part):
    """(name, func, netname) for pins actually on a net."""
    out = []
    for p in part.pins:
        net = getattr(p, "net", None)
        if net is not None and getattr(net, "name", None):
            out.append((p.name, p.func, net.name))
    return out


def _role(part):
    pins = _conn_pins(part)
    if not pins:
        return "empty"
    funcs = {f for _, f, _ in pins}
    names = {n for n, _, _ in pins}
    if funcs <= {F.PWROUT}:
        return "mains" if ("L" in names or "L1" in names) else "psu"
    if "A" in names and "B" in names:
        return "module"
    return "load"


def _classify(circuit):
    by = {"mains": [], "psu": [], "module": [], "load": [], "empty": []}
    for part in circuit.parts:
        by[_role(part)].append(part)
    # controller(s) first among modules — detected by catalog kind, not pin count
    def _is_ctrl(p):
        kind = nl.CATALOG.get(p.name, ("", 0))[0]
        return kind == "controller"
    by["module"].sort(key=lambda p: (0 if _is_ctrl(p) else 1, p.name))
    return by


def designators(circuit=None):
    """Assign positional reference designators. Returns {part: 'U1'/'G1'/...}."""
    circuit = circuit or _circuit()
    by = _classify(circuit)
    refs = {}
    for i, p in enumerate(by["module"], 1):
        refs[p] = f"U{i}"
    for i, p in enumerate(by["psu"], 1):
        refs[p] = f"G{i}"
    for i, p in enumerate(by["mains"], 1):
        refs[p] = "AC"
    for i, p in enumerate(by["load"], 1):
        refs[p] = f"{p.name}"
    return refs, by


# net groups that travel together as a labelled trunk (power is SEPARATE from
# the RS-485 data bus — different physical runs on Wiren Board).
_POWER_NETS = ("V+", "GND")
_BUS_NETS = ("RS485_A", "RS485_B")


def _net_color(name):
    key = {"V+": "V+", "GND": "GND", "RS485_A": "A", "RS485_B": "B",
           "L": "L", "L1": "L", "L2": "L2", "L3": "L3", "N": "N", "PE": "PE"}
    if name not in key:                # dedicated feeds like V+_ctrl / GND_ctrl
        if name.startswith("V+"):
            return STYLE["V+"]
        if name.startswith("GND") or name.endswith("_GND") or name == "iGND":
            return STYLE["GND"]
    return STYLE.get(key.get(name, "load"), STYLE["load"])


_AUTONET = re.compile(r"^N\$\d+$")     # SKiDL's auto name for an unnamed net


def _net_label(name, pins_by_net):
    """Readable lane label. An unnamed net (SKiDL 'N$7') is shown by a distinctive
    terminal on it (an output/named pin like O1/DIM+/K1) instead of the raw N$7."""
    if not _AUTONET.match(name):
        return name
    for (_part, pin) in pins_by_net.get(name, ()):
        if pin not in ("1", "2"):      # skip generic load terminals
            return pin
    return name


def _pins_by_net(circuit):
    """net name -> [(part, pinname), ...] over all connected pins."""
    m = {}
    for p in circuit.parts:
        for (nm, _, net) in _conn_pins(p):
            m.setdefault(net, []).append((p, nm))
    return m


# --- voltage domain: schematics are laid out HIGH-voltage on top, LOW on bottom
_HV_NAMES = {"L", "L1", "L2", "L3", "N", "PE", "U", "V", "W", "A1", "A2"}


def _pin_hv(part, name):
    """Is this pin on the mains (high-voltage) side? Per DEVICE, not just name —
    a dimmer's O* is 230V (HV) while WB-MAO4/LED O* is 0-10V/low-voltage (LV).
    Devices may override via a ``_hv_pins`` attribute (e.g. blind_drive, whose
    Up/Down control is HV or LV depending on the drive — from its datasheet)."""
    override = getattr(part, "_hv_pins", None)
    if override is not None:
        return name in override
    if name in _HV_NAMES:
        return True
    if name.startswith("COM") or (name[:1] in ("K", "T") and name[1:].isdigit()):
        return True                       # relay/contactor mains contacts & poles
    if name[:1] == "O":                   # O* is HV only on a mains phase-cut dimmer
        return nl.CATALOG.get(part.name, ("", 0))[0] == "dimmer"
    return False


def _net_hv(net, pins_by_net):
    """A net is high-voltage if any pin on it is on the mains side."""
    return any(_pin_hv(p, nm) for (p, nm) in pins_by_net.get(net, ()))


def _analyze_sides(part, centers, pins_by_net, band):
    """Assign each pin to top vs bottom by ANALYSIS, not by a hardcoded name list.

    For a pin, look at the other pins on its net and their parts' rough vertical
    position. If the net's neighbours sit mostly ABOVE this block -> put the pin
    on top; mostly below -> bottom. A net whose neighbours are on the SAME row
    (module<->module: the RS-485 bus, inter-module power) has no clear up/down
    pull -> it belongs to the shared spine below. So the spine (power+bus) falls
    out of the analysis instead of being named explicitly, and it generalises to
    any device (analog_in, MAP, contactor) without per-model rules.
    """
    cy = centers[part][1]
    top, bottom = [], []
    for (nm, fn, net) in _conn_pins(part):
        others = [centers[q][1] for (q, _) in pins_by_net[net] if q is not part]
        dy = (sum(others) / len(others) - cy) if others else 0.0
        if abs(dy) < band * 0.5:        # same-row / no vertical pull -> spine
            bottom.append((nm, fn, net))
        elif dy > 0:
            top.append((nm, fn, net))
        else:
            bottom.append((nm, fn, net))
    return top, bottom


def _draw_part(d, part, ref, x, y, top, bottom):
    """Draw a part and return ({pinname: (x, y)}, width).

    A part tagged with ``_symbol`` (lamp/fan/heater/switch/valve...) is drawn with
    its real schematic symbol — a 2-terminal element laid horizontally, its two
    terminals exposed as pins "1"/"2". Everything else is a named-terminal box (Ic).
    """
    sym = getattr(part, "_symbol", None)
    if sym is not None:
        el = d.add(getattr(e, sym)().right().at((x, y)).color(STYLE["load"]))
        d += e.Label().at((el.center[0], y + 0.9)).label(
            ref or part.name, fontsize=8, color=STYLE["load"])
        s, en = el.absanchors["start"], el.absanchors["end"]
        anchors = {"1": (float(s[0]), float(s[1])), "2": (float(en[0]), float(en[1]))}
        return anchors, max(float(en[0]) - float(s[0]), 1.5)
    w = max(2.4, 0.7 * max(len(top), len(bottom), 1))
    pins = []
    for i, c in enumerate(top):
        pins.append(e.IcPin(name=c[0], side="top", slot=f"{i + 1}/{len(top)}",
                            rotation=90))
    for i, c in enumerate(bottom):
        pins.append(e.IcPin(name=c[0], side="bottom", slot=f"{i + 1}/{len(bottom)}",
                            rotation=90))
    h = 1.7
    blk = d.add(e.Ic(pins=pins, w=w, h=h, plblsize=8, color=STYLE["block"]).at((x, y)))
    lbl = f"{ref} · {part.name}" if ref and ref != part.name else part.name
    # Title: centred inside the box, EXCEPT when a top-side pin has a long name
    # (>=4 chars, e.g. COM1/COM2 on a relay). Rotated 90 such a label descends from
    # the top edge through the vertical centre and would collide with the title, so
    # lift the title above the box. Short top labels (V+c/L/N/O1) don't reach — a
    # PSU/dimmer keeps its tidy centred title.
    long_top = any(len(c[0]) >= 4 for c in top)
    ty = blk.center[1] + h / 2 + 0.35 if long_top else blk.center[1]
    d += e.Label().at((blk.center[0], ty)).label(lbl, fontsize=8, color=STYLE["block"])
    anchors = {c[0]: (float(blk.absanchors[c[0]][0]), float(blk.absanchors[c[0]][1]))
               for c in top + bottom}
    return anchors, w


def _render_once(circuit, offsets, nudges=None, slot=3.2, row_gap=6.5):
    """One layout attempt. `offsets` shifts whole rows; `nudges` (id(part)->dx)
    shifts individual blocks — both feed render()'s collision resolver. Returns
    (drawing, placed) where placed maps id(part) -> (row_tag, part, [pin_x...])."""
    nudges = nudges or {}
    refs, by = designators(circuit)
    pins_by_net = _pins_by_net(circuit)
    d = wb.new_drawing()

    pin_xy = {}
    order = []
    def _note(net, px, py):
        pin_xy.setdefault(net, [])
        if net not in order:
            order.append(net)
        pin_xy[net].append((float(px), float(py)))

    # Layout by VOLTAGE (safety/panel convention): HIGH-voltage field devices on
    # top, LOW-voltage field devices + PSU on the bottom, modules in the middle.
    # A MIXED-voltage device (e.g. a mains LED driver with 0-10V control) straddles
    # both domains, so it joins the middle band like a module: its HV pins face up
    # to the HV bank, its LV pins face down to the LV bank — both risers short,
    # instead of the whole block sitting on one side with long crossing risers.
    def _dom(p):
        nets = [net for (_, _, net) in _conn_pins(p)]
        hv = any(_net_hv(n, pins_by_net) for n in nets)
        lv = any(not _net_hv(n, pins_by_net) for n in nets)
        return "mixed" if (hv and lv) else ("hv" if hv else "lv")
    field = by["mains"] + by["load"]
    hv_field = [p for p in field if _dom(p) == "hv"]
    lv_field = [p for p in field if _dom(p) == "lv"] + by["psu"]
    mixed = [p for p in field if _dom(p) == "mixed"]

    # ADAPTIVE row height: each bank stacks one lane per net (step 0.7). Push each
    # field row clear ABOVE/BELOW all its bank's lanes, else lanes climb into the
    # row and a pin coincides with a lane (zero-length riser -> floating end).
    allnets = list(pins_by_net)
    n_hv = sum(1 for n in allnets if _net_hv(n, pins_by_net))
    n_lvlane = sum(1 for n in allnets if not _net_hv(n, pins_by_net)
                   and n not in _POWER_NETS and n not in _BUS_NETS)
    top_y = max(row_gap, 3.0 + 0.7 * n_hv + 2.2)
    bot_y = -max(row_gap, 3.3 + 0.7 * n_lvlane + 2.2)
    rows = [("top", hv_field, top_y),
            ("mid", by["module"] + mixed, 0.0),
            ("bot", lv_field, bot_y)]

    def _sides(part, tag):
        top, bottom = [], []
        for (nm, fn, net) in _conn_pins(part):
            if tag == "mid":                       # module: pin faces its net's bank
                (top if _net_hv(net, pins_by_net) else bottom).append((nm, fn, net))
            elif tag == "top":                     # HV field row -> pins point down
                bottom.append((nm, fn, net))
            else:                                  # LV field row -> pins point up
                top.append((nm, fn, net))
        return top, bottom

    centre = {}
    placed = {}                        # id(part) -> (tag, [pin_x, ...]) for the resolver
    def build_row(parts, y, x0, tag):
        x = x0
        for part in parts:
            x += nudges.get(id(part), 0.0)     # per-block collision nudge
            top, bottom = _sides(part, tag)
            anchors, w = _draw_part(d, part, refs.get(part), x, y, top, bottom)
            centre[id(part)] = x + w / 2
            pxs = []
            for (pname, _, net) in top + bottom:
                px, py = anchors[pname]
                _note(net, px, py)
                pxs.append(px)
            placed[id(part)] = (tag, part, pxs)
            x += w + slot

    # place MODULES first so we know their x, then order field devices near the
    # module they connect to (shorter lanes) instead of raw creation order.
    modset = set(id(p) for p in by["module"])
    build_row(rows[1][1], 0.0, offsets.get("mid", 0.0), "mid")

    def _near_x(part):
        """Mean x of the modules this field part connects to (1e9 if none)."""
        xs = []
        for (_, _, net) in _conn_pins(part):
            for (q, _pn) in pins_by_net.get(net, []):
                if id(q) in modset and id(q) in centre:
                    xs.append(centre[id(q)])
        return sum(xs) / len(xs) if xs else 1e9
    for tag, parts, y in (rows[0], rows[2]):
        sp = sorted(parts, key=lambda p: (_near_x(p), p.name))  # deterministic order
        # start the row ALIGNED under its parts' modules (not at x=0) so loads sit
        # near their driver -> short lanes instead of long cross-sheet runs
        near = [_near_x(p) for p in sp if _near_x(p) < 1e8]
        x0 = (min(near) - 1.5 if near else 0.0) + offsets.get(tag, 0.0)
        build_row(sp, y, x0, tag)

    # banks by VOLTAGE: HV nets above the modules, LV nets below
    top_nets = [n for n in order if _net_hv(n, pins_by_net)]
    bot_nets = [n for n in order if not _net_hv(n, pins_by_net)]

    def _bank(nets, y0, step):
        y = y0
        for net in nets:
            pts = pin_xy[net]
            col = _net_color(net)
            lx, rx = min(p[0] for p in pts), max(p[0] for p in pts)
            # label once at the lane's left corner (attached to the conductor),
            # not floating in a detached far-left column
            d.add(e.Label().at((lx - 0.35, y + 0.32)).label(_net_label(net, pins_by_net),
                                                            color=col, fontsize=7.5))
            if rx - lx > 1e-6:
                d.add(e.Line().at((lx, y)).to((rx, y)).color(col))
            for (px, py) in pts:
                riser = abs(py - y) > 1e-6
                if riser:
                    d.add(e.Line().at((px, py)).to((px, y)).color(col))
                # dot only for a genuine interior T: a real riser (3rd arm) meets
                # the lane strictly between its ends (>= audit epsilon from the ends)
                if riser and lx + 0.2 < px < rx - 0.2:
                    d.add(e.Dot(radius=0.06).at((px, y)).color(col))
            y += step
        return y

    def _trunk_bank(nets, y, tag):
        """Draw a group of nets as ONE thick bus trunk + labelled 90° breakouts.

        The trunk is split into segments between taps so every tap point is a
        shared endpoint (never a floating end); breakouts carry a per-net label
        and NO dot (a bus entry is not a net junction). It's a multi-conductor
        bundle — ERC guarantees the actual V+/GND/A/B connectivity, the trunk is
        the readable shorthand."""
        taps = [(px, py, net) for net in nets for (px, py) in pin_xy[net]]
        if not taps:
            return
        xs = sorted({round(px, 3) for (px, _, _) in taps})
        # tag BELOW the trunk, clear of the breakout stubs (which are above it)
        d.add(e.Label().at((xs[0] + 1.6, y - 0.6)).label(tag, fontsize=7,
                                                         color=STYLE["block"]))
        for a, b in zip(xs, xs[1:]):     # segmented trunk -> shared endpoints
            d.add(e.Line().at((a, y)).to((b, y)).color(STYLE["block"]).linewidth(3.2))
        for (px, py, net) in taps:       # coloured breakout; the block pin above
            d.add(e.Line().at((px, py)).to((px, y)).color(_net_color(net)))  # names it

    # bottom (low voltage): the WB bus V+/GND/A/B as ONE thick trunk (GND is the
    # common reference shared by 24V and RS-485 -> RS-485 is A/B/GND, not two
    # isolated buses); other LV nets (inputs, 0-10V, DC, Ethernet) as lanes below.
    bus = [n for n in bot_nets if n in _POWER_NETS or n in _BUS_NETS]
    rest = [n for n in bot_nets if n not in _POWER_NETS and n not in _BUS_NETS]
    _trunk_bank(bus, -1.7, "24V + RS-485 (A/B/GND)")
    _bank(rest, -3.3, -0.7)
    _bank(top_nets, 3.0, 0.7)
    return d, placed


def _violation_xs(d):
    """x-coordinates where audits found a collision (vertical overlaps / bad dots)."""
    xs = []
    for entry in wb.audit_overlaps(d):     # ((x,y),(x,y),...) rounded points
        xs += [p[0] for p in entry if isinstance(p, tuple)]
    for (x, _y, _c) in wb.audit_dots(d):
        xs.append(x)
    return xs


def render(circuit=None, slot=3.2, row_gap=6.5, max_iter=40):
    """Clean 2D schematic (sources top / modules middle / PSU bottom).

    Every net owns a unique lane y (no horizontal overlap); the only collisions
    are risers from different blocks landing on the same x. A PER-BLOCK resolver
    nudges the specific field block sitting at a colliding x by a small step and
    re-renders until `audit_overlaps()==[] and audit_dots()==[]`. Deterministic
    (stable ordering, no hash/id-dependent placement). Returns the clean drawing,
    or the least-bad attempt if it can't converge in max_iter.
    """
    circuit = circuit or _circuit()
    nudges = {}
    best, best_bad = None, 10 ** 9
    for _ in range(max_iter):
        d, placed = _render_once(circuit, {}, nudges, slot, row_gap)
        ov, dt = wb.audit_overlaps(d), wb.audit_dots(d)
        bad = len(ov) + len(dt)
        if bad == 0:
            return d
        if bad < best_bad:
            best, best_bad = d, bad
        # nudge a movable (non-module) block sitting at the first collision x
        xs = _violation_xs(d)
        moved = False
        for cx in xs:
            for pid, (tag, part, pxs) in placed.items():
                if tag in ("top", "bot") and any(abs(px - cx) < 0.25 for px in pxs):
                    nudges[pid] = nudges.get(pid, 0.0) + 0.45
                    moved = True
                    break
            if moved:
                break
        if not moved:                    # nothing movable at the collision -> give up
            break
    return best


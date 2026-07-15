"""Reusable schemdraw building blocks for Wiren Board wiring diagrams.

Grounded in wirenboard.com docs (see ../wb-devices.md for citations):
- module terminal block order is ``V+ GND A B``; power-minus == bus-common == GND,
  so a single GND rail gives you a common ground BY CONSTRUCTION;
- Modbus RTU default is 9600 8N2; each device's address is printed on its label;
- terminate the RS-485 bus with 120 ohm (WB-T120) at BOTH ends of a long/fast bus;
- discrete inputs are switched to ``iGND`` (never to V+);
- WB-MR6C v.2 relays are DRY (potential-free) NO contacts, grouped COM1/COM2.

Design rules (these are exactly the mistakes LLMs make — see ../checklist.md):
- a device is a BLOCK with NAMED terminals, never a bare rectangle;
- the bus is SEPARATE conductors A / B / GND, not one fat line;
- devices are DAISY-CHAINED; every wire ENDS on a terminal or a junction DOT;
- the device outline never reuses a wire colour;
- labels use ASCII (the deploy image's font may not have Cyrillic glyphs).

Verify what you draw with ``audit_drawing()`` (floating ends) and
``validate_devices()`` (bus/power/address invariants) before you send it.
"""
import re

import schemdraw

# --- house STYLE: standard (non-pastel) wire colours -----------------------
# Fixed by convention:
#   V+ = red, GND = black, RS-485 A = yellow, RS-485 B = green.
# Mains follows IEC 60446: L1 brown, L2 black, L3 grey, N blue, PE green-yellow
# (single-phase L = brown). DC/bus colours (V+/GND/A/B) are the WB-side choice.
STYLE = {
    'V+': '#D50000',     # supply +       RED
    'GND': '#000000',    # common ground  BLACK
    'A': '#EAB308',      # RS-485 A / D+  YELLOW (golden, readable on white)
    'B': '#188038',      # RS-485 B / D-  GREEN
    'L': '#7A4B2A',      # mains L / L1   BROWN (IEC)
    'L2': '#000000',     # mains L2       BLACK (IEC)
    'L3': '#8A8A8A',     # mains L3       GREY (IEC)
    'N': '#1565C0',      # neutral        BLUE (IEC)
    'PE': '#7CB342',     # protective earth  GREEN-YELLOW (bicolor in reality)
    'load': '#8E24AA',   # load           purple
    'block': '#37474F',  # device outline dark slate (never a wire colour)
    'ctrl': '#1A237E',   # controller outline  navy
    'paper': '#FFFFFF',  # plain white background
    'lw': 2, 'fontsize': 11, 'dpi': 130,
}
RAIL_ORDER = ['V+', 'GND', 'A', 'B']   # top->bottom of the rail bundle below blocks


def new_drawing():
    """A Drawing with the house style + white background applied."""
    import matplotlib as mpl
    mpl.rcParams['savefig.facecolor'] = STYLE['paper']
    mpl.rcParams['figure.facecolor'] = STYLE['paper']
    d = schemdraw.Drawing(show=False)
    d.config(fontsize=STYLE['fontsize'], lw=STYLE['lw'], unit=2.0)
    return d


# --- verification: floating ends + spec invariants -------------------------
_INTERNAL = {'center', 'xy', 'istart', 'iend'}


def _close(a, b, eps=0.15):
    return abs(a[0] - b[0]) < eps and abs(a[1] - b[1]) < eps


def audit_drawing(d, eps=0.15):
    """Return floating wire endpoints (potential broken lines / wires-to-nowhere).

    A wire endpoint is OK when it coincides with a junction Dot, a device pin,
    another wire endpoint, or lands on the interior of another wire (a T-tap into
    a lane — a valid connection). Anything else is a dangling end.
    Returns a list of (x, y, element_class); empty list == clean.
    """
    nodes, endpoints, segs = [], [], []
    for el in d.elements:
        anc = getattr(el, 'absanchors', None) or {}
        cls = type(el).__name__
        if cls in ('Dot', 'Ground'):
            if 'center' in anc:
                nodes.append(anc['center'])
        elif cls == 'Ic':
            for k, v in anc.items():
                if k in _INTERNAL or re.match(r'^in[BLRT]\d+$', k):
                    continue
                nodes.append(v)
        elif 'start' in anc and 'end' in anc and not _close(anc['start'], anc['end'], 1e-6):
            endpoints.append((anc['start'], cls))
            endpoints.append((anc['end'], cls))
            segs.append((anc['start'], anc['end']))
    floating = []
    for i, (p, cls) in enumerate(endpoints):
        on_node = any(_close(p, node, eps) for node in nodes)
        on_wire = any(j != i and _close(p, q, eps) for j, (q, _) in enumerate(endpoints))
        # a T-tap: this end lands on the interior of another wire (e.g. a riser
        # meeting a lane between its ends) -> connected, not dangling
        on_tap = not (on_node or on_wire) and any(
            _on_interior(p, a, b, eps) for (a, b) in segs)
        if not (on_node or on_wire or on_tap):
            floating.append((round(float(p[0]), 2), round(float(p[1]), 2), cls))
    return floating


# a leak in a diagram label = shipping infrastructure into a public image
_LEAK_RE = re.compile(
    r'(\b\d{1,3}(?:\.\d{1,3}){3}\b)'          # IPv4
    r'|(\b[\w.-]+\.(?:local|internal|lan)\b)'  # internal hostnames
)


def validate_devices(devices, terminators=0):
    """Spec-level invariants. `devices` = list of dicts:
        {name, address, powered(bool), on_bus(bool), label(str)}
    Returns a list of problem strings; empty == valid.

    terminators: 120R terminators are only needed on long/fast buses (WB: >100 m)
    and then go at BOTH ends. So 0 (short in-cabinet bus) or 2 are both valid;
    1 (one end only) or >2 is wrong.
    """
    problems = []
    on_bus = [x for x in devices if x.get('on_bus', True)]
    if terminators not in (0, 2):
        problems.append(f'terminate at BOTH ends or not at all (got {terminators}); '
                        f'120R only needed on long/fast buses (>100 m)')
    addrs = [x.get('address') for x in on_bus if x.get('address') is not None]
    dupes = {a for a in addrs if addrs.count(a) > 1}
    if dupes:
        problems.append(f'duplicate device addresses: {sorted(dupes)}')
    for x in devices:
        if not x.get('powered', True):
            problems.append(f'{x.get("name", "?")}: V+/GND not connected')
        lbl = str(x.get('label', ''))
        leak = _LEAK_RE.search(lbl)
        if leak:
            problems.append(f'{x.get("name", "?")}: infra leak in label -> {leak.group(0)!r}')
    return problems


def check_caption(text):
    """Scan a free-text caption/title for leaked infrastructure (IP / internal
    hostname) — those must never ship in a diagram. Returns problem strings.

    NOTE: protocol/format is NOT policed. RS-485 is only the physical bus;
    non-Modbus / third-party devices (a curtain motor speaking a raw protocol
    at 8N1, etc.) are legitimate and common. Take protocol/baud/format from the
    device's datasheet — do not force Modbus 9600 8N2.
    """
    m = _LEAK_RE.search(str(text))
    return [f'caption: infra leak (IP/hostname) -> {m.group(0)!r}'] if m else []


# --- verification: trace overlaps (no wires drawn on top of each other) -----
def _wire_segments(d):
    """Axis-aligned wire segments as (p1, p2). Only `Line` elements — device
    symbols (Lamp/Ic/Motor...) are not traces."""
    segs = []
    for el in d.elements:
        if type(el).__name__ != 'Line':
            continue
        anc = getattr(el, 'absanchors', None) or {}
        if 'start' in anc and 'end' in anc and not _close(anc['start'], anc['end'], 1e-6):
            p1 = (float(anc['start'][0]), float(anc['start'][1]))
            p2 = (float(anc['end'][0]), float(anc['end'][1]))
            segs.append((p1, p2))
    return segs


def _orient(p, q, eps=1e-6):
    if abs(p[1] - q[1]) < eps:
        return 'H'
    if abs(p[0] - q[0]) < eps:
        return 'V'
    return 'D'                     # diagonal — not used in WB wiring


def audit_overlaps(d, eps=0.08, min_overlap=0.12):
    """Return pairs of wire traces that lie ON TOP of each other.

    The rule: traces never overlap (run collinearly along the same line for a
    stretch); wires may only *cross* — and only at a right angle, in a single
    point. Two collinear same-axis segments whose extents overlap by more than
    `min_overlap` are a violation (unreadable pile-up / hidden short).
    Also flags any diagonal trace (a crossing that wouldn't be a right angle).
    Returns [((x1,y1),(x2,y2)), ...] pairs; [] == clean.
    """
    segs = _wire_segments(d)
    bad = []
    for i, (a1, a2) in enumerate(segs):
        oa = _orient(a1, a2)
        if oa == 'D':
            bad.append((_r(a1), _r(a2)))          # no diagonal traces allowed
            continue
        for j in range(i + 1, len(segs)):
            b1, b2 = segs[j]
            if _orient(b1, b2) != oa:
                continue
            if oa == 'H' and abs(a1[1] - b1[1]) < eps:
                lo = max(min(a1[0], a2[0]), min(b1[0], b2[0]))
                hi = min(max(a1[0], a2[0]), max(b1[0], b2[0]))
            elif oa == 'V' and abs(a1[0] - b1[0]) < eps:
                lo = max(min(a1[1], a2[1]), min(b1[1], b2[1]))
                hi = min(max(a1[1], a2[1]), max(b1[1], b2[1]))
            else:
                continue
            if hi - lo > min_overlap:
                bad.append((_r(a1), _r(a2), _r(b1), _r(b2)))
    return bad


def _r(p):
    return (round(p[0], 2), round(p[1], 2))


# --- verification: label text sanity (literal escapes, control chars) -------
def audit_labels(d):
    """Return labels containing a LITERAL escape (a backslash-n typed as text,
    `\\t`, ...) or a control char — the classic 'WB-MDM3\\nDimmer' render bug.
    A real multi-line label uses an actual newline and is fine. []==clean."""
    bad = []
    for el in d.elements:
        for seg in getattr(el, 'segments', []) or []:
            txt = getattr(seg, 'text', None) or getattr(seg, 'label', None)
            if not isinstance(txt, str):
                continue
            if '\\n' in txt or '\\t' in txt or '\\r' in txt:
                bad.append(f'literal escape in label: {txt!r}')
            elif any(ord(c) < 32 and c not in '\n\r' for c in txt):
                bad.append(f'control char in label: {txt!r}')
    return bad


# --- verification: junction dots (dot == electrical connection only) --------
def _colored_segments(d):
    segs = []
    for el in d.elements:
        if type(el).__name__ != 'Line':
            continue
        anc = getattr(el, 'absanchors', None) or {}
        if 'start' in anc and 'end' in anc and not _close(anc['start'], anc['end'], 1e-6):
            col = getattr(el, '_userparams', {}).get('color')
            segs.append(((float(anc['start'][0]), float(anc['start'][1])),
                         (float(anc['end'][0]), float(anc['end'][1])), col))
    return segs


def _on_interior(p, a, b, eps=0.08):
    """True if p lies strictly inside segment a-b (collinear, between ends)."""
    if _close(p, a, eps) or _close(p, b, eps):
        return False
    cross = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
    if abs(cross) > eps * max(1.0, abs(b[0] - a[0]) + abs(b[1] - a[1])):
        return False
    dot = (p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])
    return 0 <= dot <= (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2


def audit_dots(d, eps=0.12):
    """Return dots that are NOT on a real electrical junction.

    Rule: a dot marks an electrical connection — 3+ arms of the SAME net (colour)
    must meet there (a T or a +). A bend/corner (2 arms) never gets a dot, and a
    plain crossing of two different nets is just a crossing (no dot). An endpoint
    touching the point counts as 1 arm; a wire passing through counts as 2.
    Returns [(x, y, colour), ...] for offending dots; [] == clean.
    """
    from collections import defaultdict
    segs = _colored_segments(d)
    bad = []
    for el in d.elements:
        if type(el).__name__ != 'Dot':
            continue
        anc = getattr(el, 'absanchors', {}) or {}
        c = anc.get('center') or anc.get('start')
        if c is None:
            continue
        p = (float(c[0]), float(c[1]))
        arms = defaultdict(int)
        for (a, b, col) in segs:
            if _close(p, a, eps) or _close(p, b, eps):
                arms[col] += 1
            elif _on_interior(p, a, b, eps):
                arms[col] += 2
        if max(arms.values(), default=0) < 3:      # no single net forms a junction
            bad.append((round(p[0], 2), round(p[1], 2),
                        getattr(el, '_userparams', {}).get('color')))
    return bad


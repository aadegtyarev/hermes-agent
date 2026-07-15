"""Electrical validation model for wb-wiring, built on SKiDL.

schemdraw draws the *picture*; this module is the *electrical twin* that an ERC
(Electrical Rules Check) actually verifies. You author a circuit as generic
cubes + nets, call ``validate()``, and a well-formed circuit returns ``[]`` —
every returned line is a real electrical fault: an unconnected pin, an unpowered
device, a shorted/swapped RS-485 bus, an output fighting a supply, a load with
no return path.

Why a separate model at all: schemdraw hands us geometry (lines at coordinates),
not a circuit — so it cannot be "checked" electrically. Every EDA validates the
*netlist*, then draws. So we describe the circuit here as data, ERC gates it, and
schemdraw still renders the human-facing PNG.

The cubes are generic, not WB-bound (``psu``, ``mains``, ``rs485_device``,
``relay_outputs``, ``discrete_inputs``, ``dimmer_outputs``, ``load``). WB models
are a thin catalog on top (``wb()``) that pins channel counts / terminal names,
so you *cannot* fabricate a 4th channel on a 3-channel part. To add your own cube
see ``references/writing-cubes.md``.

    import wb_netlist as nl
    nl.new_circuit()
    ctrl  = nl.wb("WB7")                 # controller
    relay = nl.wb("WB-MR6C v.2")         # exactly 6 channels, enforced
    nl.bus(ctrl, relay)                  # wires V+/GND + A/B, silences spares
    issues = nl.validate()               # [] == electrically sound
    assert not issues, issues
"""
import builtins
import logging
import os

os.environ.setdefault("KICAD_SYMBOL_DIR", "")  # ad-hoc parts, not KiCad libraries

from skidl import (Part, Pin, Net, ERC, SKIDL, TEMPLATE, reset, erc_logger,  # noqa: E402
                   POWER)

# SKiDL otherwise drops <script>.erc / <script>.log into the cwd on import+ERC —
# unacceptable in the agent's working dir. We capture ERC output ourselves.
try:
    from skidl.logger import stop_log_file_output as _stop_log_files
    _stop_log_files()
except Exception:  # pragma: no cover - older/newer SKiDL without the helper
    pass

F = Pin.funcs


def _circuit():
    """Current SKiDL circuit (SKiDL injects ``default_circuit`` into builtins)."""
    return builtins.default_circuit


# --- generic cubes ---------------------------------------------------------
def _cube(name, pins):
    t = Part(name=name, tool=SKIDL, dest=TEMPLATE)
    t.ref_prefix = "U"
    for num, (pname, func) in enumerate(pins, start=1):
        t += Pin(num=num, name=pname, func=func)
    return t()


def psu(name="PSU", ctrl_pair=False):
    """DC supply: V+/GND as power *outputs* (they drive the rails).

    ``ctrl_pair=True`` adds a dedicated positive terminal V+c — a 24V PSU has
    several +24V terminals, and the WB controller is best fed +24V from its OWN
    terminal (not off the module bus) so a module-bus fault can't brown it out.
    GND stays common: RS-485 needs a shared reference, so the controller's GND is
    the same as the bus (A/B/GND all reference one ground)."""
    # order V+c, GND, V+ so the dedicated controller feed (V+c) sits on the left
    # (toward the controller) and the module-bus V+ on the right — shorter feeds.
    if ctrl_pair:
        return _cube(name, [("V+c", F.PWROUT), ("GND", F.PWROUT), ("V+", F.PWROUT)])
    return _cube(name, [("V+", F.PWROUT), ("GND", F.PWROUT)])


def mains(name="MAINS", phases=1, pe=True):
    """AC source: L (1~) or L1/L2/L3 (3~) + N, optional PE — all power outputs."""
    pins = ([("L", F.PWROUT)] if phases == 1
            else [(f"L{i}", F.PWROUT) for i in range(1, 4)])
    pins.append(("N", F.PWROUT))
    if pe:
        pins.append(("PE", F.PWROUT))
    return _cube(name, pins)


def rs485_device(name, extra=()):
    """Generic Modbus/RS-485 module: V+ GND (power in) + A B (bus, bidir).

    ``extra`` = list of ``(pin_name, Pin.funcs.*)`` for device-specific terminals.
    """
    return _cube(name, [("V+", F.PWRIN), ("GND", F.PWRIN),
                        ("A", F.BIDIR), ("B", F.BIDIR)] + list(extra))


def relay_outputs(name, n=6):
    """RS-485 cube + n dry NO contacts (Ki / COMi, passive)."""
    extra = []
    for i in range(1, n + 1):
        extra += [(f"K{i}", F.PASSIVE), (f"COM{i}", F.PASSIVE)]
    return rs485_device(name, extra)


def discrete_inputs(name, n=8):
    """RS-485 cube + n dry-contact inputs, all referenced to iGND."""
    extra = [(f"In{i}", F.INPUT) for i in range(1, n + 1)] + [("iGND", F.PWRIN)]
    return rs485_device(name, extra)


def dimmer_outputs(name, n=3, inputs=6):
    """RS-485 cube + mains L/N in + n dimmed outputs O1..On + `inputs` dry inputs.

    For mains phase-cut dimmers (WB-MDM3: 3 channels, 6 dry-contact inputs on iGND).
    Low-voltage LED PWM dimmers use ``led_pwm`` (no mains L/N)."""
    extra = [("L", F.PWRIN), ("N", F.PWRIN)]
    extra += [(f"O{i}", F.PWROUT) for i in range(1, n + 1)]
    extra += [(f"In{i}", F.INPUT) for i in range(1, inputs + 1)]
    if inputs:
        extra.append(("iGND", F.PWRIN))
    return rs485_device(name, extra)


def led_pwm(name, n=4):
    """RS-485 cube + n low-voltage PWM channels O1..On (WB-MRGBW-D, WB-LED).

    The strip sits between the module V+ common and each Oi (constant-voltage)."""
    return rs485_device(name, [(f"O{i}", F.PWROUT) for i in range(1, n + 1)])


def analog_out(name, n=4):
    """RS-485 cube + n analog 0-10V outputs O1..On + their GND (WB-MAO4)."""
    extra = [(f"O{i}", F.OUTPUT) for i in range(1, n + 1)] + [("AGND", F.PWROUT)]
    return rs485_device(name, extra)


def load(name="LOAD"):
    """Two-terminal passive load (generic box; use lamp/fan/heater/... for a symbol)."""
    return _cube(name, [("1", F.PASSIVE), ("2", F.PASSIVE)])


def _element(name, symbol):
    """Two-terminal field element drawn with a real schematic symbol (see
    wb_schematic). ``symbol`` is a schemdraw element class name."""
    p = _cube(name, [("1", F.PASSIVE), ("2", F.PASSIVE)])
    p._symbol = symbol
    return p


def lamp(name="LAMP"):
    """Lamp / luminaire."""
    return _element(name, "Lamp")


def fan(name="FAN"):
    """Fan (a motor-driven load)."""
    return _element(name, "Motor")


def pump(name="PUMP"):
    """Pump (a motor-driven load)."""
    return _element(name, "Motor")


def heater(name="HEATER"):
    """Resistive heater."""
    return _element(name, "ResistorIEC")


def switch(name="SW"):
    """Wall switch / dry-contact / limit switch / button."""
    return _element(name, "Switch")


def valve(name="VALVE"):
    """Solenoid valve (a coil load)."""
    return _element(name, "Inductor2")


def motor(name="M", phases=3, pe=True):
    """Motor load: U/V/W (3~) or L/N (1~) + optional PE (all passive)."""
    pins = ([("U", F.PASSIVE), ("V", F.PASSIVE), ("W", F.PASSIVE)] if phases == 3
            else [("L", F.PASSIVE), ("N", F.PASSIVE)])
    if pe:
        pins.append(("PE", F.PASSIVE))
    return _cube(name, pins)


def contactor(name="KM", poles=3):
    """IEC contactor: coil A1/A2 (power in) + `poles` power poles Li/Ti (passive).

    A WB relay's dry contact energises the coil; the poles switch the heavy load —
    the standard way to drive motors/big loads a relay can't switch directly."""
    pins = [("A1", F.PWRIN), ("A2", F.PWRIN)]
    for i in range(1, poles + 1):
        pins += [(f"L{i}", F.PASSIVE), (f"T{i}", F.PASSIVE)]
    return _cube(name, pins)


def led_driver_0_10v(name="LED-DRV"):
    """Dimmable LED driver: mains L/N in, 0-10V dim input, DC LED output.

    DIM+ takes the 0-10V control signal (e.g. from analog_out); LED+/LED- feed
    the fixture. Pairs with analog_out() for the classic 0-10V dimming loop."""
    return _cube(name, [("L", F.PWRIN), ("N", F.PWRIN),
                        ("DIM+", F.INPUT), ("DIM-", F.PWRIN),
                        ("LED+", F.PWROUT), ("LED-", F.PWROUT)])


def analog_in(name, n=6):
    """RS-485 cube + n differential analog inputs INiP/INiN + sensor GND (WB-MAI6/11)."""
    extra = []
    for i in range(1, n + 1):
        extra += [(f"IN{i}P", F.INPUT), (f"IN{i}N", F.INPUT)]
    extra.append(("AGND", F.PWRIN))
    return rs485_device(name, extra)


def energy_meter(name, phases=3, cts=3):
    """RS-485 cube + voltage taps (L1..L3/N/PE or L/N/PE) + `cts` CT pairs (k/l).

    Voltage terminals sense the line; each current transformer wires to a CTik/CTil
    pair. cts=0 makes a pure voltmeter (WB-MAP3EV)."""
    extra = ([("L1", F.PWRIN), ("L2", F.PWRIN), ("L3", F.PWRIN)] if phases == 3
             else [("L", F.PWRIN)])
    extra += [("N", F.PWRIN), ("PE", F.PWRIN)]
    for i in range(1, cts + 1):
        extra += [(f"CT{i}k", F.INPUT), (f"CT{i}l", F.INPUT)]
    return rs485_device(name, extra)


def water_leak(name, n=5):
    """RS-485 cube + n leak-sensor inputs F1..Fn + iGND + valve relay K1/K2/COM (WB-MWAC)."""
    extra = [(f"F{i}", F.INPUT) for i in range(1, n + 1)] + [("iGND", F.PWRIN)]
    extra += [("K1", F.PASSIVE), ("K2", F.PASSIVE), ("COM", F.PASSIVE)]
    return rs485_device(name, extra)


def blind_drive(name="BLIND", bus=False, control="hv"):
    """Universal curtain/blind drive.

    Default is a mains actuator: N + Up + Down (two WB relay channels drive the
    directions). ``bus=True`` for an RS-485 drive (often a non-Modbus protocol —
    take format from its datasheet), modelled as a plain V+/GND/A/B device.

    ``control`` = "hv" or "lv": the Up/Down control inputs are mains (230V) OR
    low-voltage depending on the drive — TAKE IT FROM THE DATASHEET, don't assume.
    Sets ``_hv_pins`` so the renderer places the drive on the correct voltage side.
    """
    if bus:
        p = rs485_device(name)
        p._hv_pins = set()
        return p
    p = _cube(name, [("Up", F.PASSIVE), ("Down", F.PASSIVE), ("N", F.PASSIVE)])
    p._hv_pins = {"Up", "Down", "N"} if control == "hv" else {"N"}
    return p


def controller(name, eth=True):
    """WB controller: RS-485 master (V+/GND/A/B) + an Ethernet port (WB7/WB8)."""
    return rs485_device(name, [("ETH", F.BIDIR)] if eth else [])


def lan(name="LAN"):
    """Network the controller's Ethernet plugs into (switch/router), 1 port."""
    return _cube(name, [("ETH", F.BIDIR)])


# --- WB catalog: fixed channel counts (anti-fabrication) -------------------
# kind -> builder; catalog -> (kind, channels). Channel counts are load-bearing:
# asking wb() for a different count than the datasheet raises, so the model can't
# invent "WB-MDM3, 2 channels". Extend from references/wb-devices.md, never guess.
_KIND = {
    "controller": lambda name, n: controller(name),
    "relay": relay_outputs,
    "inputs": discrete_inputs,
    "dimmer": dimmer_outputs,
    "led_pwm": led_pwm,
    "analog_out": analog_out,
    "analog_in": analog_in,
    "water_leak": water_leak,
    "energy1": lambda name, n: energy_meter(name, phases=1, cts=n),
    "energy3": lambda name, n: energy_meter(name, phases=3, cts=n),
    "energyV": lambda name, n: energy_meter(name, phases=3, cts=0),
    "rs485": lambda name, n: rs485_device(name),
}
# Channel counts are grounded in wb_blocks.DEVICES / references/wb-devices.md —
# never guess. "rs485" == bus-only device whose load-side terminals (CTs, valves,
# sensors) are wired case-by-case, not fixed by a channel count.
CATALOG = {
    "WB7":            ("controller", 0),
    "WB8":            ("controller", 0),
    # relays
    "WB-MR6C v.2":    ("relay", 6),
    "WB-MR6C v.3":    ("relay", 6),
    "WB-MR6CU v.2":   ("relay", 6),
    "WB-MRPS6":       ("relay", 6),
    "WB-MRM2-mini":   ("relay", 2),
    "WB-MRWL3":       ("relay", 3),
    "WB-MR6LV/S":     ("relay", 6),
    # inputs
    "WB-MCM8":        ("inputs", 8),
    # dimmers (mains phase-cut) / LED (low-voltage PWM)
    "WB-MDM3":        ("dimmer", 3),
    "WB-MRGBW-D":     ("led_pwm", 4),
    "WB-LED v.1":     ("led_pwm", 4),
    # analog out (0-10V) / analog in (differential)
    "WB-MAO4":        ("analog_out", 4),
    "WB-MAI6":        ("analog_in", 6),
    "WB-MAI11":       ("analog_in", 11),
    # energy metering (voltage taps + CT pairs); MAP3EV is a pure voltmeter
    "WB-MAP6S":       ("energy1", 6),
    "WB-MAP3ET":      ("energy3", 3),
    "WB-MAP12E":      ("energy3", 12),
    "WB-MAP3EV":      ("energyV", 0),
    # water / leak (valve relay + leak inputs)
    "WB-MWAC v.2":    ("water_leak", 5),
    # bus-only: sensors, gateways (load-side terminals wired case-by-case)
    "WB-MSW v.4":     ("rs485", 0),
    "WB-MS v.2":      ("rs485", 0),
    "WB-M1W2 v.3":    ("rs485", 0),
}


def wb(model, channels=None):
    """Build a catalogued WB device. Enforces channel count against the catalog.

    Unknown models fall back to a generic rs485_device (nothing to enforce).
    Passing a ``channels`` that disagrees with the catalog raises — this is the
    guard against fabricated channel counts.
    """
    if model not in CATALOG:
        return rs485_device(model, ()) if channels is None else \
            relay_outputs(model, channels)  # best-effort generic
    kind, cat_n = CATALOG[model]
    if channels is not None and channels != cat_n and cat_n:
        raise ValueError(
            f"{model} has {cat_n} channels per catalog, not {channels} "
            f"(don't fabricate — check references/wb-devices.md)")
    return _KIND[kind](model, cat_n)


# --- bus / power helpers ---------------------------------------------------
def power_rails():
    """Return (V+, GND) nets, marked POWER so devices need not 'drive' them."""
    vp, gnd = Net("V+"), Net("GND")
    vp.drive = gnd.drive = POWER
    return vp, gnd


def rs485_pair():
    """Return (A, B) nets. Multi-master bus -> marked POWER (no single driver)."""
    a, b = Net("RS485_A"), Net("RS485_B")
    a.drive = b.drive = POWER
    return a, b


def unused(*pins):
    """Mark intentionally-unused terminals so ERC won't flag them as dangling."""
    for p in pins:
        p.do_erc = False


def bus(controller, *devices, psu_src=None, add_lan=False):
    """Wire controller + devices onto shared V+/GND rails and the A/B bus.

    Powers everything from ``psu_src`` (a fresh psu() if None), connects A/B, and
    silences any spare relay/input/dimmer channels so a clean bus validates to [].
    ``add_lan=True`` plugs the controller's Ethernet into a LAN node.
    Returns dict(vp, gnd, a, b, psu).
    """
    ps = psu_src or psu(ctrl_pair=True)
    vp, gnd = power_rails()
    a, b = rs485_pair()
    vp += ps["V+"]
    gnd += ps["GND"]
    if add_lan and "ETH" in {p.name for p in controller.pins}:
        eth = Net("LAN")
        eth.drive = POWER
        eth.connect(controller["ETH"], lan()["ETH"])
    # controller: dedicated +24V feed from the PSU, but GND + A/B stay on the bus
    # (RS-485 = A/B/GND needs the COMMON ground reference — don't isolate it).
    if "V+c" in {p.name for p in ps.pins}:
        vpc = Net("V+_ctrl")
        vpc.drive = POWER
        vpc += ps["V+c"], controller["V+"]
        gnd += controller["GND"]       # common ground (RS-485 reference)
        a += controller["A"]
        b += controller["B"]
        bus_devices = devices
    else:                              # shared-rail fallback (custom psu_src)
        bus_devices = (controller, *devices)
    for dev in bus_devices:
        vp += dev["V+"]
        gnd += dev["GND"]
        a += dev["A"]
        b += dev["B"]
        # bus() vouches only for the backbone; any still-unconnected IO terminal
        # (spare relay/input/dimmer channel, iGND) is optional -> silence it.
        # V+/GND/A/B left unconnected are still flagged (backbone must be intact).
        for pin in dev.pins:
            if pin.name not in ("V+", "GND", "A", "B") and not pin.net:
                pin.do_erc = False
    for pin in controller.pins:        # controller too (e.g. ETH when no add_lan)
        if pin.name not in ("V+", "GND", "A", "B") and not pin.net:
            pin.do_erc = False
    return {"vp": vp, "gnd": gnd, "a": a, "b": b, "psu": ps}


# --- WB-domain rules (things generic ERC treats as legal) ------------------
def _wb_domain_issues():
    """WB wiring rules that plain ERC can't know.

    ERC accepts two BIDIR pins on one net, so a shorted/swapped A-B bus passes it
    — but on RS-485 that is a wiring fault. Add further WB rules here.
    """
    issues = []
    for part in _circuit().parts:
        by_name = {p.name: p for p in part.pins}
        if "A" in by_name and "B" in by_name:
            na, nb = by_name["A"].net, by_name["B"].net
            if na is not None and na is nb:
                issues.append(
                    f"RS-485 A and B tied to one net on {part.ref} ({part.name})")
    return issues


# --- validation ------------------------------------------------------------
def validate():
    """Run ERC + WB rules on the current circuit. Return issue strings; []==clean."""
    captured = []

    class _Cap(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING:
                captured.append(record.getMessage())

    h = _Cap()
    erc_logger.addHandler(h)
    try:
        ERC()
    finally:
        erc_logger.removeHandler(h)
    return captured + _wb_domain_issues()


def new_circuit():
    """Clear the SKiDL circuit so a fresh diagram can be built."""
    reset()


# --- typical connection scenarios (reusable, all validate to []) -----------
# Each builds into the current circuit and returns its key parts. They double as
# worked examples and as the fixtures the autotests assert clean.
def scenario_relay_load(relay_model="WB-MR6C v.2"):
    """WB relay dry contact switches a mains load: L -> COM1, K1 -> lamp -> N."""
    ctrl, relay = wb("WB7"), wb(relay_model)
    bus(ctrl, relay)
    ac = mains(phases=1, pe=False)
    lmp = lamp("LAMP")
    L, N, sw = Net("L"), Net("N"), Net("relay_out")
    L += ac["L"], relay["COM1"]
    sw += relay["K1"], lmp["1"]
    N += ac["N"], lmp["2"]
    return {"ctrl": ctrl, "relay": relay, "lamp": lmp}


def scenario_contactor_motor(relay_model="WB-MR6C v.2"):
    """WB relay energises a contactor coil; the contactor feeds a 3~ motor.

    The canonical 'relay can't switch the motor directly' pattern."""
    ctrl, relay = wb("WB7"), wb(relay_model)
    bus(ctrl, relay)
    ac = mains(phases=3, pe=True)
    km, mot = contactor("KM1", poles=3), motor("M1", phases=3, pe=True)
    L1, L2, L3, N, PE = (Net("L1"), Net("L2"), Net("L3"), Net("N"), Net("PE"))
    # coil control: L1 -> relay COM1 -> K1 -> coil A1; A2 -> N
    L1 += ac["L1"], relay["COM1"]
    coil_live = Net("KM1_coil")        # switched live after the dry contact
    coil_live.drive = POWER
    relay["K1"] += coil_live
    km["A1"] += coil_live
    N += ac["N"], km["A2"]
    # power poles: mains -> contactor -> motor
    for src, pole in ((L1, 1), (L2, 2), (L3, 3)):
        src += km[f"L{pole}"]
    L2 += ac["L2"]
    L3 += ac["L3"]
    for pole, term in ((1, "U"), (2, "V"), (3, "W")):
        phase = Net(term)
        phase += km[f"T{pole}"], mot[term]
    PE += ac["PE"], mot["PE"]
    return {"ctrl": ctrl, "relay": relay, "contactor": km, "motor": mot}


def scenario_dimming_0_10v(ao_model="WB-MAO4"):
    """WB analog-out 0-10V drives a dimmable LED driver feeding a strip."""
    ctrl, ao = wb("WB7"), wb(ao_model)
    bus(ctrl, ao)
    ac = mains(phases=1, pe=False)
    drv, strip = led_driver_0_10v("DRV1"), load("STRIP")
    L, N = Net("L"), Net("N")
    L += ac["L"], drv["L"]
    N += ac["N"], drv["N"]
    Net("DIM") .connect(ao["O1"], drv["DIM+"])     # 0-10V control signal
    Net("DIM_GND").connect(ao["AGND"], drv["DIM-"])  # its reference
    Net("LED+").connect(drv["LED+"], strip["1"])
    Net("LED-").connect(drv["LED-"], strip["2"])
    return {"ctrl": ctrl, "ao": ao, "driver": drv, "strip": strip}


def scenario_inputs(n=3, model="WB-MCM8"):
    """Dry-contact switches: each SWi bridges Ini to the shared iGND."""
    ctrl, inp = wb("WB7"), wb(model)
    bus(ctrl, inp)
    ig = Net("iGND")                   # input common / reference rail
    ig.drive = POWER
    ig += inp["iGND"]
    switches = []
    for i in range(1, n + 1):
        sw = switch(f"SW{i}")
        Net(f"in{i}").connect(inp[f"In{i}"], sw["1"])
        ig += sw["2"]
        switches.append(sw)
    return {"ctrl": ctrl, "inputs": inp, "switches": switches}

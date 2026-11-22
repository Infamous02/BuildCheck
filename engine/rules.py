"""The 17 BuildCheck compatibility rules.

Each rule is a plain function registered with @rule("Rnn", "short name"). It takes a
Build and returns (status, message). The registry keeps rules explicit, ordered and
countable: engine/checker.py runs RULES in order.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable

from engine.models import Build

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"

# PSU sizing (R16).
# BASE_SYSTEM_W: rough draw for board, RAM, drives, fans and USB on top of CPU TDP + GPU TBP.
# PSU_HEADROOM: we want 30% spare capacity over that estimate for transient spikes and
# so the PSU runs in its efficient range.
BASE_SYSTEM_W = 100
PSU_HEADROOM = 1.3


@dataclass
class Result:
    rule_id: str        # "R01"
    name: str           # short human name
    status: str         # PASS | WARN | FAIL | SKIP
    message: str        # specific, with the actual values involved


@dataclass
class Rule:
    rule_id: str
    name: str
    fn: Callable[[Build], tuple[str, str]]

    def run(self, build: Build) -> Result:
        try:
            status, message = self.fn(build)
        except TypeError:
            # A spec value the rule needs is NULL (e.g. an approved record with gaps).
            status, message = WARN, "Missing part data for this rule; verify manually"
        return Result(self.rule_id, self.name, status, message)


RULES: list[Rule] = []


def rule(rule_id: str, name: str):
    def register(fn):
        RULES.append(Rule(rule_id, name, fn))
        return fn
    return register


# ---------------------------------------------------------------- helpers

_BIOS_RE = re.compile(r"^([A-Za-z]*)(\d+(?:\.\d+)*)([A-Za-z]?)$")


def compare_bios(a: str | None, b: str | None) -> int | None:
    """Compare two BIOS versions: -1 if a < b, 0 if equal, 1 if a > b, None if not comparable.

    Handles the common vendor forms when both sides use the same one:
    'F5' / 'F10' (Gigabyte), '1.20' / '2.10' (MSI, ASRock), '1203' / '1601' (ASUS).
    Anything else (different prefixes, dotted vs plain, odd strings) returns None so
    the caller can ask for a manual check instead of guessing.
    """
    if not a or not b:
        return None
    ma, mb = _BIOS_RE.match(a.strip()), _BIOS_RE.match(b.strip())
    if not ma or not mb:
        return None
    if ma.group(1).upper() != mb.group(1).upper():
        return None
    if ("." in ma.group(2)) != ("." in mb.group(2)):
        return None
    ka = tuple(int(x) for x in ma.group(2).split(".")) + (ma.group(3).lower(),)
    kb = tuple(int(x) for x in mb.group(2).split(".")) + (mb.group(3).lower(),)
    return (ka > kb) - (ka < kb)


def effective_mem_speed(b: Build) -> int:
    return min(b.kit.speed, b.board.max_mem_speed, b.cpu.max_mem_speed)


def estimated_draw_w(b: Build) -> int:
    return b.cpu.tdp_w + (b.gpu.tbp_w if b.gpu else 0) + BASE_SYSTEM_W


def recommended_psu_w(b: Build) -> int:
    return math.ceil(estimated_draw_w(b) * PSU_HEADROOM)


# ---------------------------------------------------------------- CPU / board

@rule("R01", "CPU socket matches board")
def r01_socket(b: Build):
    if b.cpu.socket == b.board.socket:
        return PASS, f"CPU and board are both {b.cpu.socket}"
    return FAIL, f"CPU socket {b.cpu.socket} does not match board socket {b.board.socket}"


@rule("R02", "CPU on board support list")
def r02_support_list(b: Build):
    if b.supported_cpu is not None:
        return PASS, f"{b.cpu.model} is on the {b.board.model} CPU support list"
    return FAIL, f"{b.cpu.model} is not on the {b.board.model} CPU support list"


@rule("R03", "BIOS version requirement")
def r03_bios(b: Build):
    if b.supported_cpu is None:
        return SKIP, "Skipped: CPU is not on the board support list (see R02)"
    need, shipped = b.supported_cpu.min_bios, b.board.shipped_bios
    if not need:
        return PASS, "CPU works on the shipped BIOS"
    cmp = compare_bios(shipped, need)
    if cmp is None:
        return WARN, (f"CPU needs BIOS {need} or later; shipped BIOS is {shipped or 'unknown'}. "
                      "Verify BIOS version manually")
    if cmp >= 0:
        return PASS, f"Shipped BIOS {shipped} meets the required {need}"
    if b.board.bios_flashback:
        return WARN, (f"CPU needs BIOS {need} but board ships with {shipped}; "
                      "update with BIOS Flashback before installing the CPU")
    return FAIL, (f"CPU needs BIOS {need} but board ships with {shipped}; "
                  "needs BIOS update before CPU install; no flashback")


# ---------------------------------------------------------------- memory

@rule("R04", "RAM type matches board")
def r04_ram_board(b: Build):
    if b.kit.mem_type == b.board.mem_type:
        return PASS, f"Kit and board are both {b.kit.mem_type}"
    return FAIL, f"{b.kit.mem_type} kit does not fit the {b.board.mem_type} board"


@rule("R05", "RAM type supported by CPU")
def r05_ram_cpu(b: Build):
    if b.kit.mem_type == b.cpu.mem_type:
        return PASS, f"CPU supports {b.kit.mem_type}"
    return FAIL, f"CPU supports {b.cpu.mem_type}, not {b.kit.mem_type}"


@rule("R06", "Module count fits slots")
def r06_slots(b: Build):
    n = b.kit.modules * b.kit_qty
    if n <= b.board.mem_slots:
        return PASS, f"{n} modules fit in {b.board.mem_slots} slots"
    return FAIL, f"{n} modules ({b.kit_qty} kit(s) x {b.kit.modules}) but the board has only {b.board.mem_slots} slots"


@rule("R07", "Total capacity within board max")
def r07_capacity(b: Build):
    total = b.kit.modules * b.kit_qty * b.kit.gb_per_module
    if total <= b.board.max_mem_gb:
        return PASS, f"{total} GB is within the board max of {b.board.max_mem_gb} GB"
    return FAIL, f"{total} GB exceeds the board max of {b.board.max_mem_gb} GB"


@rule("R08", "RAM kit on board QVL")
def r08_qvl(b: Build):
    if b.kit.id in b.qvl_kit_ids:
        return PASS, f"{b.kit.model} is on the {b.board.model} QVL"
    return WARN, f"{b.kit.model} is not on QVL; may not train at rated speed"


@rule("R09", "RAM speed within supported max")
def r09_speed(b: Build):
    limit = min(b.board.max_mem_speed, b.cpu.max_mem_speed)
    if b.kit.speed <= limit:
        return PASS, f"{b.kit.speed} MT/s is within the supported max of {limit} MT/s"
    return WARN, (f"Kit is rated {b.kit.speed} MT/s but board max is {b.board.max_mem_speed} and "
                  f"CPU max is {b.cpu.max_mem_speed}; will run at a lower speed ({limit} MT/s)")


# ---------------------------------------------------------------- case fit

@rule("R10", "Board form factor fits case")
def r10_form_factor(b: Build):
    supported = ", ".join(b.case.supported_form_factors)
    if b.board.form_factor in b.case.supported_form_factors:
        return PASS, f"{b.board.form_factor} board fits the case (supports {supported})"
    return FAIL, f"{b.board.form_factor} board does not fit the case (supports {supported})"


@rule("R11", "GPU length fits case")
def r11_gpu_length(b: Build):
    if b.gpu is None:
        return SKIP, "No discrete GPU"
    if b.gpu.length_mm <= b.case.max_gpu_mm:
        return PASS, f"GPU length {b.gpu.length_mm} mm is within case max {b.case.max_gpu_mm} mm"
    return FAIL, f"GPU length {b.gpu.length_mm} mm exceeds case max {b.case.max_gpu_mm} mm"


@rule("R12", "Display output available")
def r12_display(b: Build):
    if b.gpu is not None:
        return PASS, "Discrete GPU provides display output"
    if b.cpu.has_igpu:
        return PASS, "No discrete GPU; CPU integrated graphics provides display output"
    return FAIL, f"No discrete GPU and {b.cpu.model} has no integrated graphics; no display output"


# ---------------------------------------------------------------- cooling

@rule("R13", "Cooler supports CPU socket")
def r13_cooler_socket(b: Build):
    if b.cooler is None:
        return SKIP, "No aftermarket cooler (stock cooler)"
    if b.cpu.socket in b.cooler.sockets:
        return PASS, f"Cooler supports {b.cpu.socket}"
    return FAIL, f"Cooler does not support {b.cpu.socket} (supports {', '.join(b.cooler.sockets)})"


@rule("R14", "Air cooler height fits case")
def r14_air_height(b: Build):
    if b.cooler is None or b.cooler.cooler_type != "air":
        return SKIP, "No air cooler"
    if b.cooler.height_mm <= b.case.max_cooler_mm:
        return PASS, f"Cooler height {b.cooler.height_mm} mm is within case max {b.case.max_cooler_mm} mm"
    return FAIL, f"Cooler height {b.cooler.height_mm} mm exceeds case max {b.case.max_cooler_mm} mm"


@rule("R15", "AIO radiator fits case")
def r15_radiator(b: Build):
    if b.cooler is None or b.cooler.cooler_type != "aio":
        return SKIP, "No AIO cooler"
    if b.cooler.radiator_mm <= b.case.max_radiator_mm:
        return PASS, f"{b.cooler.radiator_mm} mm radiator fits (case max {b.case.max_radiator_mm} mm)"
    return FAIL, f"{b.cooler.radiator_mm} mm radiator does not fit (case max {b.case.max_radiator_mm} mm)"


# ---------------------------------------------------------------- power

@rule("R16", "PSU wattage headroom")
def r16_psu_watts(b: Build):
    draw, target, watts = estimated_draw_w(b), recommended_psu_w(b), b.psu.watts
    if watts < draw:
        return FAIL, f"PSU is {watts} W but estimated draw is {draw} W"
    if watts < target:
        return WARN, f"PSU {watts} W covers the {draw} W estimate but is below the recommended {target} W"
    return PASS, f"PSU {watts} W meets the recommended {target} W (estimated draw {draw} W)"


@rule("R17", "PSU connectors and form factor")
def r17_psu_fit(b: Build):
    problems, notes = [], []
    if b.gpu is not None:
        if b.gpu.requires_12v_2x6 and not b.psu.has_12v_2x6:
            problems.append("GPU needs a 12V-2x6 connector but the PSU has none")
        if b.gpu.pcie_8pin_required > b.psu.pcie_8pin:
            problems.append(f"GPU needs {b.gpu.pcie_8pin_required} PCIe 8-pin but the PSU has {b.psu.pcie_8pin}")
    if b.psu.form_factor != b.case.psu_form_factor:
        if b.psu.form_factor == "SFX" and b.case.psu_form_factor == "ATX":
            notes.append("SFX PSU in an ATX case (fits; may need an SFX-to-ATX bracket)")
        else:
            problems.append(f"{b.psu.form_factor} PSU does not fit a case that takes {b.case.psu_form_factor}")
    if problems:
        return FAIL, "; ".join(problems)
    return PASS, "; ".join(["PSU connectors and form factor OK"] + notes)

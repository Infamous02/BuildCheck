"""Plain-text technician checklist built from rule results and build data.

Week 2 will grow this into the customer quote + checklist output.
"""
from __future__ import annotations

from engine.models import Build
from engine.rules import FAIL, WARN, Result, effective_mem_speed


def checklist(build: Build, results: list[Result]) -> list[str]:
    by_id = {r.rule_id: r for r in results}
    status = lambda rule_id: by_id[rule_id].status if rule_id in by_id else None
    steps: list[str] = []

    if any(r.status == FAIL for r in results):
        failed = ", ".join(r.rule_id for r in results if r.status == FAIL)
        steps.append(f"STOP: build has failing rules ({failed}); fix the part list before assembly")

    # Steps driven by warnings
    if status("R03") == WARN:
        need = build.supported_cpu.min_bios if build.supported_cpu else None
        if build.board.bios_flashback and need:
            steps.append(f"Update BIOS to {need} or later via BIOS Flashback before installing the CPU")
        else:
            steps.append(f"Verify the board BIOS version supports the {build.cpu.model} before installing the CPU")
    if status("R08") == WARN:
        steps.append("RAM kit is not on QVL: test with memory training/stability check before delivery")
    if status("R09") == WARN:
        steps.append(f"Enable XMP/EXPO; expect RAM to run at {effective_mem_speed(build)} MT/s")
    else:
        steps.append(f"Enable XMP/EXPO in BIOS so RAM runs at its rated {build.kit.speed} MT/s")
    if status("R16") == WARN:
        steps.append("PSU has little headroom: run a combined CPU+GPU load test and watch for shutdowns")
    if status("R17") == "PASS" and "SFX PSU in an ATX case" in by_id["R17"].message:
        steps.append("Mount the SFX PSU with an SFX-to-ATX adapter bracket")

    # Basics for every build
    modules = build.kit.modules * build.kit_qty
    steps.append(f"Install RAM in the board's recommended slots for {modules} modules "
                 "(see the motherboard manual's memory population table)")
    if build.gpu is not None:
        steps.append("Connect all GPU power connectors; fully seat any 12V-2x6 / 8-pin plugs")
    else:
        steps.append("Connect the display to the motherboard video outputs (no discrete GPU)")
    if build.cooler is None:
        steps.append("Install the stock cooler and confirm thermal paste is applied")
    steps.append("Verify POST, then update BIOS to the latest stable version")
    return steps

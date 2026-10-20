"""BuildCheck - PC build compatibility rules engine (Module 2 prototype).

Each rule inspects a build and returns a RuleResult with a status
(PASS / WARN / FAIL) and a plain-English reason. A build is only
compatible if no rule FAILs; WARNs become technician checklist items.

Usage:
    python buildcheck.py --cpu r7-9800x3d --board asus-tuf-b650 \
        --ram corsair-6000 --gpu rtx5070 --psu rm750e
    python buildcheck.py --list        # show available part IDs
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
BASE_SYSTEM_W = 100      # board, drives, fans, RAM, USB
PSU_HEADROOM = 1.20      # want 20% headroom over estimated peak draw


@dataclass
class RuleResult:
    rule: str
    status: str
    message: str


@dataclass
class Build:
    cpu: dict
    board: dict
    ram: dict
    gpu: dict
    psu: dict


def load_parts(path: str | Path = Path(__file__).with_name("parts.json")) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def make_build(parts: dict, cpu: str, board: str, ram: str, gpu: str, psu: str) -> Build:
    try:
        return Build(parts["cpus"][cpu], parts["motherboards"][board],
                     parts["ram"][ram], parts["gpus"][gpu], parts["psus"][psu])
    except KeyError as e:
        raise SystemExit(f"Unknown part ID: {e}. Run with --list to see valid IDs.")


# ---------------------------------------------------------------- rules
def rule_socket(b: Build, parts: dict) -> RuleResult:
    if b.cpu["socket"] == b.board["socket"]:
        return RuleResult("CPU socket", PASS, f"{b.cpu['socket']} CPU fits {b.board['socket']} board.")
    return RuleResult("CPU socket", FAIL,
                      f"{b.cpu['name']} uses {b.cpu['socket']}, but {b.board['name']} is {b.board['socket']}.")


def rule_chipset(b: Build, parts: dict) -> RuleResult:
    supported = parts["chipsets"].get(b.board["chipset"], [])
    if b.cpu["family"] in supported:
        return RuleResult("Chipset support", PASS, f"{b.board['chipset']} supports {b.cpu['family']} CPUs.")
    return RuleResult("Chipset support", FAIL,
                      f"{b.board['chipset']} chipset does not support {b.cpu['family']} CPUs "
                      f"(supports: {', '.join(supported) or 'unknown'}).")


def rule_bios(b: Build, parts: dict) -> RuleResult:
    note = b.board.get("bios_notes", {}).get(b.cpu["family"])
    if note:
        return RuleResult("BIOS revision", WARN, f"{b.cpu['family']} on {b.board['name']}: {note}")
    return RuleResult("BIOS revision", PASS, "No BIOS update required for this CPU family.")


def rule_cpu_memory_type(b: Build, parts: dict) -> RuleResult:
    if b.ram["type"] in b.cpu["memory_types"]:
        return RuleResult("CPU memory support", PASS, f"{b.cpu['name']} supports {b.ram['type']}.")
    return RuleResult("CPU memory support", FAIL,
                      f"{b.cpu['name']} only supports {'/'.join(b.cpu['memory_types'])}, not {b.ram['type']}.")


def rule_board_memory_type(b: Build, parts: dict) -> RuleResult:
    if b.ram["type"] == b.board["memory_type"]:
        return RuleResult("Board memory type", PASS, f"Board and kit are both {b.ram['type']}.")
    return RuleResult("Board memory type", FAIL,
                      f"{b.ram['type']} kit will not fit the {b.board['memory_type']}-only {b.board['name']}.")


def rule_ram_slots(b: Build, parts: dict) -> RuleResult:
    if b.ram["modules"] <= b.board["ram_slots"]:
        return RuleResult("RAM slots", PASS, f"{b.ram['modules']} modules fit in {b.board['ram_slots']} slots.")
    return RuleResult("RAM slots", FAIL,
                      f"Kit has {b.ram['modules']} modules but the board has only {b.board['ram_slots']} slots.")


def rule_ram_speed(b: Build, parts: dict) -> RuleResult:
    if b.ram["speed_mts"] <= b.board["max_ram_speed_mts"]:
        return RuleResult("RAM speed", PASS,
                          f"{b.ram['speed_mts']} MT/s is within the board max of {b.board['max_ram_speed_mts']}.")
    return RuleResult("RAM speed", WARN,
                      f"Kit is rated {b.ram['speed_mts']} MT/s but the board tops out at "
                      f"{b.board['max_ram_speed_mts']}; it will run slower than advertised.")


def rule_qvl(b: Build, parts: dict) -> RuleResult:
    if b.ram["sku"] in b.board.get("qvl", []):
        return RuleResult("Memory QVL", PASS, f"{b.ram['sku']} is on the board's QVL.")
    return RuleResult("Memory QVL", WARN,
                      f"{b.ram['sku']} is not on the {b.board['name']} QVL; memory training/stability is not "
                      "guaranteed. Test with a memory stress test after build.")


def estimate_draw(b: Build) -> int:
    return b.cpu["max_power_w"] + b.gpu["power_w"] + BASE_SYSTEM_W


def rule_psu_capacity(b: Build, parts: dict) -> RuleResult:
    draw = estimate_draw(b)
    target = round(draw * PSU_HEADROOM)
    watts = b.psu["wattage"]
    if watts < draw:
        return RuleResult("PSU capacity", FAIL,
                          f"Estimated peak draw is {draw}W but the PSU is only {watts}W.")
    if watts < target:
        return RuleResult("PSU capacity", WARN,
                          f"{watts}W covers the {draw}W estimate but is below the {target}W target (20% headroom).")
    return RuleResult("PSU capacity", PASS, f"{watts}W covers the {draw}W estimate with headroom.")


def rule_gpu_psu(b: Build, parts: dict) -> RuleResult:
    rec = b.gpu["recommended_psu_w"]
    if b.psu["wattage"] >= rec:
        return RuleResult("GPU PSU recommendation", PASS, f"Meets the {rec}W recommendation for {b.gpu['name']}.")
    return RuleResult("GPU PSU recommendation", WARN,
                      f"{b.gpu['name']} vendor recommends at least {rec}W; this PSU is {b.psu['wattage']}W.")


RULES = [rule_socket, rule_chipset, rule_bios, rule_cpu_memory_type, rule_board_memory_type,
         rule_ram_slots, rule_ram_speed, rule_qvl, rule_psu_capacity, rule_gpu_psu]


def check_build(b: Build, parts: dict) -> tuple[str, list[RuleResult]]:
    results = [rule(b, parts) for rule in RULES]
    statuses = {r.status for r in results}
    overall = FAIL if FAIL in statuses else WARN if WARN in statuses else PASS
    return overall, results


# ---------------------------------------------------------------- CLI
ICONS = {PASS: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]"}


def print_report(b: Build, overall: str, results: list[RuleResult]) -> None:
    print("=" * 72)
    print("BuildCheck report")
    for label, part in (("CPU", b.cpu), ("Board", b.board), ("RAM", b.ram), ("GPU", b.gpu), ("PSU", b.psu)):
        print(f"  {label:<6}{part['name']}")
    print("-" * 72)
    for r in results:
        print(f"{ICONS[r.status]} {r.rule}: {r.message}")
    print("-" * 72)
    verdict = {PASS: "COMPATIBLE", WARN: "COMPATIBLE WITH WARNINGS", FAIL: "NOT COMPATIBLE"}[overall]
    print(f"Overall: {verdict}")
    checklist = [r for r in results if r.status == WARN]
    if checklist and overall != FAIL:
        print("\nTechnician checklist:")
        for i, r in enumerate(checklist, 1):
            print(f"  {i}. {r.rule}: {r.message}")
    print("=" * 72)


def main() -> None:
    p = argparse.ArgumentParser(description="Check a PC build for compatibility.")
    p.add_argument("--cpu"); p.add_argument("--board"); p.add_argument("--ram")
    p.add_argument("--gpu"); p.add_argument("--psu")
    p.add_argument("--list", action="store_true", help="list available part IDs")
    args = p.parse_args()
    parts = load_parts()

    if args.list:
        for cat in ("cpus", "motherboards", "ram", "gpus", "psus"):
            print(f"{cat}:")
            for pid, part in parts[cat].items():
                print(f"  {pid:<20}{part['name']}")
        return
    if not all([args.cpu, args.board, args.ram, args.gpu, args.psu]):
        p.error("--cpu, --board, --ram, --gpu and --psu are all required (or use --list)")

    b = make_build(parts, args.cpu, args.board, args.ram, args.gpu, args.psu)
    overall, results = check_build(b, parts)
    print_report(b, overall, results)


if __name__ == "__main__":
    main()

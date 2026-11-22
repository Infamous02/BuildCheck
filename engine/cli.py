"""BuildCheck command line.

    py -m engine.cli check --cpu 1 --board 1 --kit 1 --kit-qty 1 --gpu 1 --case 1 --psu 2 --cooler 1
    py -m engine.cli check --build 1
    py -m engine.cli list motherboards
    py -m engine.cli review --table cpus --id 9 --approve      (or --reject, --needs-review)
"""
from __future__ import annotations

import argparse
import sys

from db.connection import get_conn
from engine.checker import check, summary
from engine.checklist import checklist
from engine.models import Build
from engine.repository import PART_TABLES, PartNotApproved, list_parts, load_build, load_saved_build, set_review_status
from engine.rules import Result

VERDICT = {"PASS": "COMPATIBLE", "WARN": "COMPATIBLE WITH WARNINGS", "FAIL": "NOT COMPATIBLE"}


def _name(part) -> str:
    return f"{part.brand} {part.model}" if part else "(none)"


def print_report(build: Build, results: list[Result]) -> None:
    print("=" * 100)
    for label, part in (("CPU", build.cpu), ("Board", build.board), ("RAM", build.kit), ("GPU", build.gpu),
                        ("Case", build.case), ("PSU", build.psu), ("Cooler", build.cooler)):
        extra = f"  x{build.kit_qty}" if label == "RAM" and build.kit_qty != 1 else ""
        print(f"  {label:<8}{_name(part)}{extra}")
    print("-" * 100)
    print(f"{'ID':<5}{'Rule':<34}{'Status':<8}Message")
    for r in results:
        print(f"{r.rule_id:<5}{r.name:<34}{r.status:<8}{r.message}")
    print("-" * 100)
    overall = summary(results)
    print(f"Overall: {overall} ({VERDICT[overall]})")
    print("\nTechnician checklist:")
    for i, step in enumerate(checklist(build, results), 1):
        print(f"  {i}. {step}")
    print("=" * 100)


def cmd_check(conn, args) -> int:
    if args.build is not None:
        build = load_saved_build(conn, args.build)
    else:
        missing = [n for n in ("cpu", "board", "kit", "case", "psu") if getattr(args, n) is None]
        if missing:
            raise SystemExit(f"check needs --build, or all of --cpu --board --kit --case --psu "
                             f"(missing: {', '.join('--' + m for m in missing)})")
        build = load_build(conn, cpu=args.cpu, board=args.board, kit=args.kit, kit_qty=args.kit_qty,
                           gpu=args.gpu, case=args.case, psu=args.psu, cooler=args.cooler)
    results = check(build)
    print_report(build, results)
    return 1 if summary(results) == "FAIL" else 0


def cmd_list(conn, args) -> int:
    print(f"{'id':<5}{'review_status':<15}brand / model")
    for pid, brand, model, status in list_parts(conn, args.table):
        print(f"{pid:<5}{status:<15}{brand} {model}")
    return 0


def cmd_review(conn, args) -> int:
    row = set_review_status(conn, args.table, args.id, args.status)
    if row is None:
        raise SystemExit(f"{args.table} id {args.id} not found")
    print(f"{args.table} id {row[0]} ({row[1]} {row[2]}) -> {args.status}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="py -m engine.cli", description="BuildCheck compatibility checker")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("check", help="run all rules against a build")
    c.add_argument("--build", type=int, help="saved build id (instead of part ids)")
    for name in ("cpu", "board", "kit", "gpu", "case", "psu", "cooler"):
        c.add_argument(f"--{name}", type=int)
    c.add_argument("--kit-qty", type=int, default=1)
    c.set_defaults(func=cmd_check)

    ls = sub.add_parser("list", help="list parts in a table")
    ls.add_argument("table", choices=sorted(PART_TABLES))
    ls.set_defaults(func=cmd_list)

    r = sub.add_parser("review", help="approve or reject a part")
    r.add_argument("--table", required=True, choices=sorted(PART_TABLES))
    r.add_argument("--id", type=int, required=True)
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--approve", dest="status", action="store_const", const="approved")
    g.add_argument("--reject", dest="status", action="store_const", const="rejected")
    g.add_argument("--needs-review", dest="status", action="store_const", const="needs_review")
    r.set_defaults(func=cmd_review)
    return p


def main(argv: list[str] | None = None) -> None:
    sys.stdout.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    with get_conn() as conn:
        try:
            code = args.func(conn, args)
        except PartNotApproved as e:
            raise SystemExit(f"Error: {e}")
    sys.exit(code)


if __name__ == "__main__":
    main()

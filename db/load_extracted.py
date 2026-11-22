"""Load Module 4 extraction output (data/staged/*.json) into Postgres as needs_review.

Everything that came from extraction waits for a human. Approve or reject with:
    py -m engine.cli review --table motherboards --id 7 --approve

Usage:
    py -m db.load_extracted                          # every file in data/staged/
    py -m db.load_extracted data/staged/z890.json    # specific files

The extractor currently only produces motherboard records (ingest/extract.py).
Fields the extractor doesn't read (revision, shipped_bios, bios_flashback) are left
NULL for the reviewer to fill in.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from db.connection import get_conn
from engine.normalize import normalize_form_factor, normalize_mem_type, normalize_socket, parse_speed

STAGED_DIR = Path("data") / "staged"
LOW_CONFIDENCE = 0.8          # same threshold as ExtractedRecord.low_confidence_fields()

# extractor field -> (motherboards column, normalizer or None)
BOARD_FIELDS = {
    "manufacturer": ("brand", None),
    "model": ("model", None),
    "socket": ("socket", normalize_socket),
    "chipset": ("chipset", None),
    "form_factor": ("form_factor", normalize_form_factor),
    "memory_type": ("mem_type", normalize_mem_type),
    "memory_slots": ("mem_slots", None),
    "max_memory_gb": ("max_mem_gb", None),
    "max_memory_speed_mts": ("max_mem_speed", parse_speed),
}


def map_board(staged: dict) -> tuple[dict, list[str]]:
    """Turn one staged extraction JSON into a motherboards row plus review notes."""
    record = staged["record"]
    data = record["data"]
    row: dict = {}
    notes: list[str] = list(staged.get("problems", []))
    for field, (col, fn) in BOARD_FIELDS.items():
        item = data.get(field) or {}
        value = item.get("value")
        if value is None:
            if not any(n.startswith(field + ":") for n in notes):   # extractor may have said why
                notes.append(f"{field}: missing")
        elif fn:
            try:
                value = fn(value)
            except ValueError as e:
                notes.append(f"{field}: {e}")
                value = None
        if value is not None and item.get("confidence", 1.0) < LOW_CONFIDENCE:
            notes.append(f"{field}: low confidence ({item['confidence']:.2f}), page {item.get('page')}")
        row[col] = value
    row["source_url"] = record["source_file"]
    row["review_status"] = "needs_review"        # always: a human approves extracted data
    return row, notes


def insert_board(conn, row: dict) -> tuple[int, bool]:
    """Insert unless the same brand/model/source is already loaded. Returns (id, inserted)."""
    existing = conn.execute(
        "SELECT id FROM motherboards WHERE brand IS NOT DISTINCT FROM %s AND model IS NOT DISTINCT FROM %s "
        "AND source_url = %s", (row["brand"], row["model"], row["source_url"])).fetchone()
    if existing:
        return existing[0], False
    if row["brand"] is None or row["model"] is None:
        raise ValueError("brand and model are required; fix the staged record first")
    cols = list(row)
    placeholders = ", ".join(["%s"] * len(cols))
    new_id = conn.execute(f"INSERT INTO motherboards ({', '.join(cols)}) VALUES ({placeholders}) RETURNING id",
                          list(row.values())).fetchone()[0]
    return new_id, True


def load_file(conn, path: Path) -> None:
    staged = json.loads(path.read_text(encoding="utf-8"))
    part_type = (staged.get("record") or {}).get("data", {}).get("part_type")
    if part_type != "motherboard":
        # TODO: map CPU / CPU-support / QVL records once the extractor produces them.
        print(f"{path}: skipped (part_type {part_type!r} not supported yet)")
        return
    row, notes = map_board(staged)
    try:
        board_id, inserted = insert_board(conn, row)
    except ValueError as e:
        print(f"{path}: skipped ({e})")
        return
    verb = "loaded as" if inserted else "already loaded as"
    print(f"{path}: {verb} motherboards id {board_id} ({row['brand']} {row['model']}), needs_review")
    for n in notes:
        print(f"    check: {n}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Load staged extraction output as needs_review records.")
    ap.add_argument("files", type=Path, nargs="*", help=f"staged JSON files (default: {STAGED_DIR}/*.json)")
    args = ap.parse_args()
    files = args.files or sorted(STAGED_DIR.glob("*.json"))
    if not files:
        raise SystemExit(f"No staged files found in {STAGED_DIR}")
    with get_conn() as conn:
        for path in files:
            load_file(conn, path)
        conn.commit()


if __name__ == "__main__":
    main()

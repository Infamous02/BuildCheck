"""Rebuild the database from schema.sql and the CSVs in db/seed/.

WARNING: this drops and recreates every table, so any data not in the CSVs is lost.

Usage:
    py -m db.load_seed                     # uses DATABASE_URL
    py -m db.load_seed --seed-dir db/seed  # load a different CSV folder
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from psycopg import sql

from db.connection import get_conn
from engine.normalize import (normalize_form_factor, normalize_mem_type, normalize_psu_form_factor,
                              normalize_socket, parse_pg_array, parse_speed)

DB_DIR = Path(__file__).resolve().parent
SCHEMA = DB_DIR / "schema.sql"
SEED_DIR = DB_DIR / "seed"

# Load order matters: parts first, then join tables, then builds.
TABLES = ["cpus", "motherboards", "memory_kits", "gpus", "cases", "psus", "coolers",
          "board_cpu_support", "board_qvl", "builds"]

# Tables whose id comes from the CSV; their SERIAL sequences are reset afterwards.
ID_TABLES = ["cpus", "motherboards", "memory_kits", "gpus", "cases", "psus", "coolers", "builds"]

ARRAY_COLUMNS = {"supported_form_factors", "sockets"}


def _list(fn):
    return lambda raw: [fn(x) for x in parse_pg_array(raw)]


# (table, column) -> normalizer. Values that are already clean pass through unchanged.
NORMALIZERS = {
    ("cpus", "socket"): normalize_socket,
    ("cpus", "mem_type"): normalize_mem_type,
    ("cpus", "max_mem_speed"): parse_speed,
    ("motherboards", "socket"): normalize_socket,
    ("motherboards", "form_factor"): normalize_form_factor,
    ("motherboards", "mem_type"): normalize_mem_type,
    ("motherboards", "max_mem_speed"): parse_speed,
    ("memory_kits", "mem_type"): normalize_mem_type,
    ("memory_kits", "speed"): parse_speed,
    ("cases", "supported_form_factors"): _list(normalize_form_factor),
    ("cases", "psu_form_factor"): normalize_psu_form_factor,
    ("psus", "form_factor"): normalize_psu_form_factor,
    ("coolers", "sockets"): _list(normalize_socket),
}


def clean_row(table: str, row: dict[str, str]) -> list:
    """Empty cells -> NULL, arrays -> Python lists, then apply the column's normalizer."""
    out = []
    for col, raw in row.items():
        raw = raw.strip() if raw is not None else ""
        if raw == "":
            out.append(None)
            continue
        fn = NORMALIZERS.get((table, col))
        if fn:
            try:
                value = fn(raw)
            except ValueError as e:
                raise SystemExit(f"{table}.csv, column {col}: {e}")
        elif col in ARRAY_COLUMNS:
            value = parse_pg_array(raw)
        else:
            value = raw
        out.append(value)
    return out


def load_table(conn, table: str, path: Path) -> None:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        cols = reader.fieldnames or []
        rows = [clean_row(table, r) for r in reader]
    if not rows:
        return
    stmt = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, cols)))
    with conn.cursor() as cur, cur.copy(stmt) as copy:
        for r in rows:
            copy.write_row(r)


def load_seed(conn, seed_dir: Path = SEED_DIR) -> dict[str, int]:
    conn.execute(SCHEMA.read_text(encoding="utf-8"))
    for table in TABLES:
        path = seed_dir / f"{table}.csv"
        if path.exists():
            load_table(conn, table, path)
    # CSV rows set their own ids, so move each SERIAL past the highest one.
    for table in ID_TABLES:
        conn.execute(sql.SQL("SELECT setval(pg_get_serial_sequence({t}, 'id'), "
                             "COALESCE((SELECT MAX(id) FROM {i}), 0) + 1, false)")
                     .format(t=sql.Literal(table), i=sql.Identifier(table)))
    conn.commit()
    return {t: conn.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(t))).fetchone()[0]
            for t in TABLES}


def main() -> None:
    ap = argparse.ArgumentParser(description="Recreate the BuildCheck schema and load seed CSVs.")
    ap.add_argument("--seed-dir", type=Path, default=SEED_DIR)
    args = ap.parse_args()
    with get_conn() as conn:
        counts = load_seed(conn, args.seed_dir)
    print("Loaded seed data:")
    for table, n in counts.items():
        print(f"  {table:<20}{n:>5} rows")


if __name__ == "__main__":
    main()

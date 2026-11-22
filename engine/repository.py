"""SQL for loading builds and managing review status. Only approved rows are used in checks."""
from __future__ import annotations

from dataclasses import fields

from psycopg import sql
from psycopg.rows import dict_row

from engine.models import (BoardCpuSupport, Build, Case, Cooler, Cpu, Gpu, MemoryKit, Motherboard,
                           Psu)

PART_TABLES = {
    "cpus": Cpu, "motherboards": Motherboard, "memory_kits": MemoryKit, "gpus": Gpu,
    "cases": Case, "psus": Psu, "coolers": Cooler,
}
REVIEW_STATUSES = ("approved", "needs_review", "rejected")


class PartNotApproved(Exception):
    pass


def _to_dataclass(cls, row: dict):
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in row.items() if k in names})


def get_part(conn, table: str, part_id: int):
    """Fetch one approved part as a dataclass. Raises PartNotApproved if missing or not approved."""
    cls = PART_TABLES[table]
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute(sql.SQL("SELECT * FROM {} WHERE id = %s").format(sql.Identifier(table)),
                          (part_id,)).fetchone()
    if row is None:
        raise PartNotApproved(f"{table} id {part_id} does not exist")
    if row["review_status"] != "approved":
        raise PartNotApproved(f"{table} id {part_id} ({row['brand']} {row['model']}) is "
                              f"'{row['review_status']}', not approved; approve it with "
                              f"`py -m engine.cli review --table {table} --id {part_id} --approve`")
    return _to_dataclass(cls, row)


def load_build(conn, *, cpu: int, board: int, kit: int, case: int, psu: int,
               kit_qty: int = 1, gpu: int | None = None, cooler: int | None = None) -> Build:
    cpu_p = get_part(conn, "cpus", cpu)
    board_p = get_part(conn, "motherboards", board)
    with conn.cursor(row_factory=dict_row) as cur:
        support = cur.execute(
            "SELECT * FROM board_cpu_support WHERE board_id = %s AND cpu_id = %s AND review_status = 'approved'",
            (board, cpu)).fetchone()
        qvl = cur.execute("SELECT kit_id FROM board_qvl WHERE board_id = %s AND review_status = 'approved'",
                          (board,)).fetchall()
    return Build(
        cpu=cpu_p,
        board=board_p,
        kit=get_part(conn, "memory_kits", kit),
        kit_qty=kit_qty,
        case=get_part(conn, "cases", case),
        psu=get_part(conn, "psus", psu),
        gpu=get_part(conn, "gpus", gpu) if gpu is not None else None,
        cooler=get_part(conn, "coolers", cooler) if cooler is not None else None,
        supported_cpu=_to_dataclass(BoardCpuSupport, support) if support else None,
        qvl_kit_ids={r["kit_id"] for r in qvl},
    )


def load_saved_build(conn, build_id: int) -> Build:
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute("SELECT * FROM builds WHERE id = %s", (build_id,)).fetchone()
    if row is None:
        raise PartNotApproved(f"builds id {build_id} does not exist")
    return load_build(conn, cpu=row["cpu_id"], board=row["board_id"], kit=row["kit_id"],
                      kit_qty=row["kit_qty"] or 1, gpu=row["gpu_id"], case=row["case_id"],
                      psu=row["psu_id"], cooler=row["cooler_id"])


def list_parts(conn, table: str) -> list[tuple]:
    if table not in PART_TABLES:
        raise ValueError(f"unknown table {table!r}")
    return conn.execute(sql.SQL("SELECT id, brand, model, review_status FROM {} ORDER BY id")
                        .format(sql.Identifier(table))).fetchall()


def set_review_status(conn, table: str, part_id: int, status: str) -> tuple | None:
    """Set review_status on one part. Returns (id, brand, model) or None if no such row."""
    if table not in PART_TABLES:
        raise ValueError(f"unknown table {table!r}")
    if status not in REVIEW_STATUSES:
        raise ValueError(f"unknown status {status!r}")
    row = conn.execute(sql.SQL("UPDATE {} SET review_status = %s WHERE id = %s RETURNING id, brand, model")
                       .format(sql.Identifier(table)), (status, part_id)).fetchone()
    conn.commit()
    return row

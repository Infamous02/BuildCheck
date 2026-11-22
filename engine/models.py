"""Dataclasses mirroring the Postgres part tables (db/schema.sql), plus Build."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Cpu:
    id: int
    brand: str
    model: str
    socket: str
    tdp_w: int
    has_igpu: bool
    mem_type: str
    max_mem_speed: int
    source_url: str | None = None
    review_status: str = "approved"


@dataclass
class Motherboard:
    id: int
    brand: str
    model: str
    socket: str
    chipset: str
    form_factor: str
    mem_type: str
    mem_slots: int
    max_mem_gb: int
    max_mem_speed: int
    shipped_bios: str | None = None
    bios_flashback: bool = False
    revision: str | None = None
    source_url: str | None = None
    review_status: str = "approved"


@dataclass
class MemoryKit:
    id: int
    brand: str
    model: str
    mem_type: str
    speed: int
    modules: int
    gb_per_module: int
    form_factor: str = "DIMM"
    source_url: str | None = None
    review_status: str = "approved"


@dataclass
class Gpu:
    id: int
    brand: str
    model: str
    length_mm: int
    tbp_w: int
    pcie_8pin_required: int = 0
    requires_12v_2x6: bool = False
    source_url: str | None = None
    review_status: str = "approved"


@dataclass
class Case:
    id: int
    brand: str
    model: str
    supported_form_factors: list[str]
    max_gpu_mm: int
    max_cooler_mm: int
    psu_form_factor: str
    max_radiator_mm: int = 0
    source_url: str | None = None
    review_status: str = "approved"


@dataclass
class Psu:
    id: int
    brand: str
    model: str
    watts: int
    form_factor: str
    pcie_8pin: int
    has_12v_2x6: bool
    source_url: str | None = None
    review_status: str = "approved"


@dataclass
class Cooler:
    id: int
    brand: str
    model: str
    cooler_type: str                  # "air" or "aio"
    sockets: list[str]
    height_mm: int | None = None      # air only
    radiator_mm: int | None = None    # AIO only
    source_url: str | None = None
    review_status: str = "approved"


@dataclass
class BoardCpuSupport:
    board_id: int
    cpu_id: int
    min_bios: str | None = None       # None = works on the shipped BIOS
    source_url: str | None = None
    review_status: str = "approved"


@dataclass
class Build:
    cpu: Cpu
    board: Motherboard
    kit: MemoryKit
    kit_qty: int
    case: Case
    psu: Psu
    gpu: Gpu | None
    cooler: Cooler | None
    supported_cpu: BoardCpuSupport | None     # board_cpu_support row for this cpu/board, if any
    qvl_kit_ids: set[int] = field(default_factory=set)   # kit ids on this board's QVL

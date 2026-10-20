"""
BuildCheck extraction schemas.

These models are the contract between document ingestion and the rules engine.
Every value pulled from a manufacturer document is wrapped in `Sourced[...]`,
which carries the evidence needed for human review: the page it came from,
the exact text it was read from, and a confidence score.

Nothing produced here goes straight into the catalog. Records start as
`pending` and are promoted only after a reviewer approves them.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Generic, Literal, Optional, TypeVar, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

T = TypeVar("T")

MemoryType = Literal["DDR4", "DDR5"]
FormFactor = Literal["E-ATX", "ATX", "Micro-ATX", "Mini-ITX"]
ExtractionMethod = Literal["deterministic", "llm", "manual"]
ReviewStatus = Literal["pending", "approved", "rejected"]


# ---------------------------------------------------------------------------
# Evidence wrapper
# ---------------------------------------------------------------------------

class Sourced(BaseModel, Generic[T]):
    """A single extracted value plus the evidence a reviewer needs to verify it."""

    model_config = ConfigDict(extra="forbid")

    value: Optional[T] = Field(
        description="The extracted value, or None if the document doesn't state it."
    )
    page: Optional[int] = Field(
        default=None, ge=1, description="1-based page number in the source document."
    )
    evidence: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Exact text from the source the value was read from. "
        "Checked against the page text later to catch hallucinations.",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="1.0 for deterministic table parses; the model's own estimate for LLM output.",
    )

    @property
    def is_missing(self) -> bool:
        return self.value is None


# ---------------------------------------------------------------------------
# Normalizers
# Manufacturers write the same thing many ways. Normalize once, here, so the
# rules engine only ever compares clean values.
# ---------------------------------------------------------------------------

_SOCKET_RE = re.compile(r"^\s*(LGA|AM|FCLGA)\s*-?\s*(\d{1,4})\s*$", re.IGNORECASE)


def normalize_socket(raw: str) -> str:
    """'LGA1851', 'lga 1851', 'FCLGA1851' -> 'LGA 1851'; 'am5' -> 'AM5'."""
    m = _SOCKET_RE.match(raw)
    if not m:
        raise ValueError(f"Unrecognized socket format: {raw!r}")
    family, number = m.group(1).upper(), m.group(2)
    if family in ("LGA", "FCLGA"):
        return f"LGA {number}"
    return f"AM{number}"


def normalize_memory_type(raw: str) -> str:
    """'ddr5', 'DDR5 SDRAM', 'DDR-5' -> 'DDR5'."""
    m = re.search(r"DDR\s*-?\s*([45])", raw, re.IGNORECASE)
    if not m:
        raise ValueError(f"Unrecognized memory type: {raw!r}")
    return f"DDR{m.group(1)}"


def parse_speed_mts(raw: str | int) -> int:
    """'DDR5-6400', '6400 MT/s', '6400MHz', 6400 -> 6400."""
    if isinstance(raw, int):
        return raw
    m = re.search(r"(\d{4,5})", str(raw))
    if not m:
        raise ValueError(f"No memory speed found in {raw!r}")
    return int(m.group(1))


# ---------------------------------------------------------------------------
# Part records
# Field names line up with what the rules engine (buildcheck.py) checks.
# ---------------------------------------------------------------------------

class _PartBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manufacturer: Sourced[str]
    model: Sourced[str]


class MotherboardRecord(_PartBase):
    part_type: Literal["motherboard"] = "motherboard"
    socket: Sourced[str]
    chipset: Sourced[str]
    form_factor: Sourced[FormFactor]
    memory_type: Sourced[MemoryType]
    memory_slots: Sourced[int]
    max_memory_gb: Sourced[int]
    max_memory_speed_mts: Sourced[int]

    @field_validator("socket")
    @classmethod
    def _norm_socket(cls, v: Sourced[str]) -> Sourced[str]:
        if v.value is not None:
            v.value = normalize_socket(v.value)
        return v

    @field_validator("memory_type", mode="before")
    @classmethod
    def _norm_mem(cls, v):
        if isinstance(v, dict) and v.get("value"):
            v = {**v, "value": normalize_memory_type(v["value"])}
        return v

    @field_validator("memory_slots")
    @classmethod
    def _slots_range(cls, v: Sourced[int]) -> Sourced[int]:
        if v.value is not None and v.value not in (2, 4, 8):
            raise ValueError(f"memory_slots={v.value} is not a plausible desktop value")
        return v

    @field_validator("max_memory_speed_mts")
    @classmethod
    def _speed_range(cls, v: Sourced[int]) -> Sourced[int]:
        if v.value is not None and not 1600 <= v.value <= 12000:
            raise ValueError(f"max_memory_speed_mts={v.value} is outside 1600-12000")
        return v


class CPURecord(_PartBase):
    part_type: Literal["cpu"] = "cpu"
    socket: Sourced[str]
    cores: Sourced[int]
    threads: Sourced[int]
    tdp_w: Sourced[int]
    memory_type: Sourced[MemoryType]
    max_memory_speed_mts: Sourced[int]

    @field_validator("socket")
    @classmethod
    def _norm_socket(cls, v: Sourced[str]) -> Sourced[str]:
        if v.value is not None:
            v.value = normalize_socket(v.value)
        return v

    @field_validator("memory_type", mode="before")
    @classmethod
    def _norm_mem(cls, v):
        if isinstance(v, dict) and v.get("value"):
            v = {**v, "value": normalize_memory_type(v["value"])}
        return v

    @field_validator("tdp_w")
    @classmethod
    def _tdp_range(cls, v: Sourced[int]) -> Sourced[int]:
        if v.value is not None and not 15 <= v.value <= 500:
            raise ValueError(f"tdp_w={v.value} is outside 15-500 W")
        return v


class CPUSupportEntry(BaseModel):
    """One row of a motherboard's CPU support list."""

    model_config = ConfigDict(extra="forbid")

    part_type: Literal["cpu_support"] = "cpu_support"
    motherboard_model: Sourced[str]
    cpu_model: Sourced[str]
    min_bios_version: Sourced[str]


class QVLEntry(BaseModel):
    """One row of a motherboard's memory Qualified Vendor List."""

    model_config = ConfigDict(extra="forbid")

    part_type: Literal["qvl"] = "qvl"
    motherboard_model: Sourced[str]
    vendor: Sourced[str]
    part_number: Sourced[str]
    capacity_gb: Sourced[int]
    speed_mts: Sourced[int]
    dimms_tested: Sourced[int] = Field(
        description="How many DIMMs were populated in the validated config (1, 2, or 4)."
    )

    @field_validator("speed_mts", mode="before")
    @classmethod
    def _parse_speed(cls, v):
        if isinstance(v, dict) and v.get("value") is not None:
            v = {**v, "value": parse_speed_mts(v["value"])}
        return v


PartData = Union[MotherboardRecord, CPURecord, CPUSupportEntry, QVLEntry]


# ---------------------------------------------------------------------------
# Envelope: what gets written to the staging table
# ---------------------------------------------------------------------------

class ExtractedRecord(BaseModel):
    """A staged record awaiting review. Maps 1:1 to a row in staged_records."""

    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    doc_id: str = Field(description="SHA-256 of the source file (from parse.py).")
    source_file: str
    method: ExtractionMethod
    status: ReviewStatus = "pending"
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    data: PartData = Field(discriminator="part_type")

    def low_confidence_fields(self, threshold: float = 0.8) -> list[str]:
        """Field names a reviewer should look at first."""
        flagged = []
        for name, val in self.data:
            if isinstance(val, Sourced) and (val.is_missing or val.confidence < threshold):
                flagged.append(name)
        return flagged

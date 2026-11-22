"""Tests for ingest.extract. Run from the repo root with: py -m pytest -v

No LLM is needed: FakeClient returns canned JSON so the validation logic can be
tested on its own. The last test runs on the real parsed Z890 manual if present.
"""
from pathlib import Path

import pytest

from ingest.extract import (FIELDS, check_field, extract_deterministic, extract_motherboard,
                            FieldValue)
from ingest.llm import coerce_json
from ingest.parse import ParsedDocument, ParsedPage

COVER = "Z890 EAGLE WIFI7 PLUS\n(Z890 EAGLE WIFI7 P)\nUser's Manual\nRev. 1002"
SPECS = """2-2 Product Specifications
CPU • LGA1851 socket: Support for Intel® Core™ Ultra Processors
(Go to GIGABYTE's website for the latest CPU support list.)
Chipset • Intel® Z890 Express Chipset
Memory • Support for DDR5 7200/6400/5600 MT/s memory modules
• 4 x DDR5 DIMM sockets supporting up to 256 GB (64 GB single DIMM capacity)
of system memory"""
FORM = "Operating System • Support for Windows 11 64-bit\nForm Factor • ATX Form Factor; 30.5cm x 24.4cm"


def make_doc(*texts: str) -> ParsedDocument:
    pages = [ParsedPage(page=i, text=t) for i, t in enumerate(texts, start=1)]
    return ParsedDocument(doc_id="test", source_file="test.pdf", page_count=len(pages), pages=pages)


def llm_field(value, page, evidence, confidence=0.9):
    return {"value": value, "page": page, "evidence": evidence, "confidence": confidence}


class FakeClient:
    name = "fake"

    def __init__(self, response: dict):
        self.response = response
        self.asked_for: list[str] = []

    def extract_json(self, system, user, schema):
        self.asked_for = list(schema.model_fields)
        # Fill any field the test didn't specify with "not stated".
        return {n: self.response.get(n, llm_field(None, None, None, 0)) for n in self.asked_for}


# ------------------------------------------------------------ deterministic
def test_deterministic_reads_all_spec_fields():
    doc = make_doc(COVER, SPECS, FORM)
    res = extract_motherboard(doc, "deterministic", pages=[2, 3])
    b = res.record.data
    assert b.manufacturer.value == "Gigabyte"
    assert b.model.value == "Z890 EAGLE WIFI7 PLUS"
    assert b.socket.value == "LGA 1851"           # normalized by schemas.py
    assert b.chipset.value == "Z890"
    assert b.form_factor.value == "ATX"
    assert b.memory_type.value == "DDR5"
    assert b.memory_slots.value == 4
    assert b.max_memory_gb.value == 256
    assert b.max_memory_speed_mts.value == 7200   # highest of 7200/6400/5600
    assert res.record.method == "deterministic"


def test_deterministic_evidence_is_on_the_cited_page():
    doc = make_doc(COVER, SPECS, FORM)
    for name, fv in extract_deterministic(doc, [2, 3]).items():
        assert check_field(doc, name, fv) is None, name


def test_missing_field_is_reported_not_guessed():
    doc = make_doc(COVER, SPECS)                  # no form factor page
    res = extract_motherboard(doc, "deterministic", pages=[2])
    assert res.record.data.form_factor.value is None
    assert any(p.startswith("form_factor:") for p in res.problems)
    assert "form_factor" in res.record.low_confidence_fields()


# ------------------------------------------------------------ hybrid / LLM
def test_hybrid_only_asks_llm_for_missing_fields():
    doc = make_doc(COVER, SPECS, FORM)
    client = FakeClient({"form_factor": llm_field("ATX", 3, "ATX Form Factor")})
    res = extract_motherboard(doc, "hybrid", client=client, pages=[2])   # page 3 not searched by regex
    assert client.asked_for == ["form_factor"]
    assert res.fields["form_factor"].method == "llm"
    assert res.record.data.form_factor.value == "ATX"
    assert res.record.method == "llm"


def test_hallucinated_evidence_is_rejected():
    doc = make_doc(COVER, SPECS, FORM)
    client = FakeClient({"form_factor": llm_field("Micro-ATX", 3, "Micro-ATX Form Factor")})
    res = extract_motherboard(doc, "hybrid", client=client, pages=[2])
    assert res.record.data.form_factor.value is None
    assert any("not found on page 3" in p for p in res.problems)


def test_value_must_appear_in_its_own_evidence():
    doc = make_doc(COVER, SPECS, FORM)
    # Real quote, wrong number: the classic LLM slip.
    client = FakeClient({"max_memory_gb": llm_field("128", 2, "supporting up to 256 GB")})
    res = extract_motherboard(doc, "llm", client=client, pages=[2, 3])
    assert res.record.data.max_memory_gb.value is None
    assert any("does not appear in its evidence" in p for p in res.problems)


def test_schema_validator_drops_implausible_value():
    odd = SPECS.replace("4 x DDR5 DIMM", "3 x DDR5 DIMM")
    doc = make_doc(COVER, odd, FORM)
    res = extract_motherboard(doc, "deterministic", pages=[2, 3])
    assert res.record.data.memory_slots.value is None       # 3 slots fails _slots_range
    assert any("memory_slots: failed schema check" in p for p in res.problems)
    assert res.record.data.memory_type.value == "DDR5"      # rest of the record survives


def test_llm_failure_keeps_deterministic_results():
    class Broken:
        name = "broken"
        def extract_json(self, *a):
            raise ConnectionError("LM Studio server not running")
    doc = make_doc(COVER, SPECS)
    res = extract_motherboard(doc, "hybrid", client=Broken(), pages=[2])
    assert res.record.data.socket.value == "LGA 1851"
    assert any("LLM step failed" in p for p in res.problems)


def test_llm_speed_list_takes_highest():
    doc = make_doc(COVER, SPECS, FORM)
    client = FakeClient({"max_memory_speed_mts": llm_field("7200/6400/5600", 2, "DDR5 7200/6400/5600 MT/s")})
    res = extract_motherboard(doc, "llm", client=client, pages=[2, 3])
    assert res.record.data.max_memory_speed_mts.value == 7200


# ------------------------------------------------------------ coerce_json
def test_coerce_json_handles_reasoning_text():
    raw = '* Task: extract {thinking about braces}\nAnswer:\n{"memory_type": "DDR5", "slots": 4}'
    assert coerce_json(raw) == {"memory_type": "DDR5", "slots": 4}
    assert coerce_json({"a": 1}) == {"a": 1}
    with pytest.raises(ValueError):
        coerce_json("no json here")


def test_coerce_json_returns_outer_object_for_nested_answer():
    # What gemma via LM Studio actually returns: reasoning text, then the nested answer.
    raw = ('Reasoning: {"draft": 1} looks wrong.\nEND__{\n "chipset": {"value": "B650", "page": 8, '
           '"evidence": "AMD B650", "confidence": 1},\n "socket": {"value": "AM5", "page": 8, '
           '"evidence": "AM5", "confidence": 1}\n}')
    out = coerce_json(raw)
    assert set(out) == {"chipset", "socket"}
    assert out["chipset"]["value"] == "B650"


# ------------------------------------------------------------ real manual
REAL = Path("data/parsed/z890.json")


@pytest.mark.skipif(not REAL.exists(), reason="run ingest.parse on the Z890 manual first")
def test_real_z890_manual_deterministic():
    doc = ParsedDocument.from_json(REAL)
    res = extract_motherboard(doc, "deterministic")
    b = res.record.data
    assert b.socket.value == "LGA 1851"
    assert b.chipset.value == "Z890"
    assert b.form_factor.value == "ATX"
    assert b.memory_type.value == "DDR5"
    assert b.memory_slots.value == 4
    assert b.max_memory_gb.value == 256
    assert b.max_memory_speed_mts.value == 7200
    assert "Z890 EAGLE" in b.model.value
    assert all(res.fields[n] is not None for n in FIELDS), res.problems


# ------------------------------------------------------------ normalization
@pytest.mark.parametrize("raw, expected", [
    ("GIGABYTE", "Gigabyte"), ("gigabyte", "Gigabyte"), ("Gigabyte Technology", "Gigabyte"),
    ("asus", "ASUS"), ("ASRock", "ASRock"), ("SomeNewBrand", "SomeNewBrand"),
])
def test_normalize_manufacturer(raw, expected):
    from ingest.schemas import normalize_manufacturer
    assert normalize_manufacturer(raw) == expected


def test_llm_and_regex_manufacturer_agree():
    doc = make_doc(COVER, SPECS, FORM)
    client = FakeClient({"manufacturer": llm_field("GIGABYTE", 2, "GIGABYTE")})
    res = extract_motherboard(doc, "llm", client=client, pages=[2, 3])
    assert res.record.data.manufacturer.value == "Gigabyte"


# ------------------------------------------------------------ MSI wording
MSI_COVER = "X870E GAMING PLUS WIFI\nMotherboard\nUser Guide"
MSI_SPECS = """Specifications
CPU ● Processor socket AM5
Chipset AMD X870E Chipset
● 4x DDR5 memory slots*, supporting up to 256GB**
● Supports AMD POR Speed and JEDEC Speed
Memory ● Supports CUDIMM
Continued on next column"""
MSI_TAIL = "Continued from previous column\n● ATX Form Factor\nForm Factor\n● 9.6 in. x 12 in."


def test_msi_wording_and_spec_page_span():
    doc = make_doc(MSI_COVER, MSI_SPECS, "filler", MSI_TAIL)
    res = extract_motherboard(doc, "deterministic")         # auto page choice must reach page 4
    b = res.record.data
    assert b.model.value == "X870E GAMING PLUS WIFI"
    assert b.socket.value == "AM5"
    assert b.chipset.value == "X870E"
    assert b.memory_type.value == "DDR5"
    assert b.memory_slots.value == 4
    assert b.max_memory_gb.value == 256
    assert b.form_factor.value == "ATX"
    assert b.max_memory_speed_mts.value is None             # MSI's manual doesn't state one


def test_llm_cannot_invent_unstated_speed():
    doc = make_doc(MSI_COVER, MSI_SPECS, "filler", MSI_TAIL)
    # A model "remembering" a spec from training data instead of reading the page.
    client = FakeClient({"max_memory_speed_mts": llm_field("8400", 2, "Supports DDR5 up to 8400 MT/s")})
    res = extract_motherboard(doc, "hybrid", client=client)
    assert res.record.data.max_memory_speed_mts.value is None
    assert any("not found on page 2" in p for p in res.problems)


MSI_REAL = Path("data/parsed/msi_board.json")


@pytest.mark.skipif(not MSI_REAL.exists(), reason="run ingest.parse on the MSI X870E manual first")
def test_real_msi_x870e_manual_deterministic():
    res = extract_motherboard(ParsedDocument.from_json(MSI_REAL), "deterministic")
    b = res.record.data
    assert b.manufacturer.value == "MSI"
    assert b.model.value == "X870E GAMING PLUS WIFI"
    assert (b.socket.value, b.chipset.value, b.form_factor.value) == ("AM5", "X870E", "ATX")
    assert (b.memory_type.value, b.memory_slots.value, b.max_memory_gb.value) == ("DDR5", 4, 256)
    assert b.max_memory_speed_mts.value is None

"""Automated tests for BuildCheck document ingestion. Run from the repo root with: py -m pytest -v"""
import pytest

pytest.importorskip("pydantic")
pytest.importorskip("pdfplumber")

from pydantic import ValidationError

from ingest.parse import (ParsedDocument, ParsedPage, evidence_in_page, find_relevant_pages,
                          parse_pdf, score_page, sha256_file)
from ingest.schemas import (CPURecord, ExtractedRecord, MotherboardRecord, QVLEntry,
                            normalize_memory_type, normalize_socket, parse_speed_mts)


def src(value, confidence=1.0):
    return {"value": value, "page": 1, "evidence": str(value), "confidence": confidence}


def board(**overrides):
    fields = dict(manufacturer=src("Gigabyte"), model=src("Z890 EAGLE WIFI7"), socket=src("LGA1851"),
                  chipset=src("Z890"), form_factor=src("ATX"), memory_type=src("DDR5"),
                  memory_slots=src(4), max_memory_gb=src(256), max_memory_speed_mts=src(8800))
    fields.update(overrides)
    return MotherboardRecord(**fields)


def make_pdf(path, pages):
    """Write a minimal PDF with one line of Helvetica text per page."""
    n = len(pages)
    objs = ["<< /Type /Catalog /Pages 2 0 R >>",
            f"<< /Type /Pages /Kids [{' '.join(f'{4 + 2 * i} 0 R' for i in range(n))}] /Count {n} >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    for i, text in enumerate(pages):
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>")
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    out, offsets = b"%PDF-1.4\n", []
    for num, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{num} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(out)
    return path


# ---------------------------------------------------------------- normalizers
@pytest.mark.parametrize("raw, expected", [
    ("LGA1851", "LGA 1851"), ("lga 1851", "LGA 1851"), ("FCLGA1700", "LGA 1700"),
    ("LGA-1700", "LGA 1700"), ("am5", "AM5"), (" AM4 ", "AM4"),
])
def test_normalize_socket(raw, expected):
    assert normalize_socket(raw) == expected


def test_normalize_socket_rejects_garbage():
    with pytest.raises(ValueError):
        normalize_socket("Socket 7 maybe")


@pytest.mark.parametrize("raw", ["ddr5", "DDR5 SDRAM", "DDR-5", "ddr 5"])
def test_normalize_memory_type(raw):
    assert normalize_memory_type(raw) == "DDR5"


def test_normalize_memory_type_rejects_garbage():
    with pytest.raises(ValueError):
        normalize_memory_type("LPDDR")


@pytest.mark.parametrize("raw", ["DDR5-6400", "6400 MT/s", "6400MHz", 6400])
def test_parse_speed_mts(raw):
    assert parse_speed_mts(raw) == 6400


def test_parse_speed_mts_rejects_missing_number():
    with pytest.raises(ValueError):
        parse_speed_mts("fast")


# ---------------------------------------------------------------- schemas
def test_motherboard_record_normalizes_values():
    b = board(socket=src("lga1851"), memory_type=src("ddr5 sdram"))
    assert b.socket.value == "LGA 1851"
    assert b.memory_type.value == "DDR5"


def test_missing_values_are_allowed_and_flagged():
    b = board(max_memory_gb={"value": None})
    assert b.max_memory_gb.is_missing
    assert b.max_memory_gb.page is None


@pytest.mark.parametrize("field, bad", [
    ("memory_slots", 3), ("max_memory_speed_mts", 50000), ("form_factor", "Mega-ATX"),
])
def test_motherboard_rejects_implausible_values(field, bad):
    with pytest.raises(ValidationError):
        board(**{field: src(bad)})


def test_sourced_rejects_bad_evidence_fields():
    with pytest.raises(ValidationError):
        board(chipset={"value": "Z890", "page": 0})                  # pages are 1-based
    with pytest.raises(ValidationError):
        board(chipset={"value": "Z890", "confidence": 1.5})
    with pytest.raises(ValidationError):
        board(chipset={"value": "Z890", "source": "guess"})          # extra keys forbidden


def test_cpu_record_tdp_range():
    fields = dict(manufacturer=src("AMD"), model=src("Ryzen 7 9800X3D"), socket=src("am5"),
                  cores=src(8), threads=src(16), tdp_w=src(120), memory_type=src("DDR5"),
                  max_memory_speed_mts=src(5600))
    assert CPURecord(**fields).socket.value == "AM5"
    with pytest.raises(ValidationError):
        CPURecord(**{**fields, "tdp_w": src(5)})


def test_qvl_entry_parses_speed_string():
    q = QVLEntry(motherboard_model=src("Z890 EAGLE WIFI7"), vendor=src("Corsair"),
                 part_number=src("CMK32GX5M2B6000C30"), capacity_gb=src(32),
                 speed_mts=src("DDR5-6000"), dimms_tested=src(2))
    assert q.speed_mts.value == 6000


def test_extracted_record_defaults_and_discriminator():
    raw = {"doc_id": "abc", "source_file": "z890.pdf", "method": "llm",
           "data": board().model_dump()}
    rec = ExtractedRecord.model_validate(raw)
    assert isinstance(rec.data, MotherboardRecord)
    assert rec.status == "pending"
    assert rec.record_id and rec.extracted_at.tzinfo is not None
    assert ExtractedRecord.model_validate_json(rec.model_dump_json()) == rec


def test_low_confidence_fields():
    rec = ExtractedRecord(doc_id="abc", source_file="z890.pdf", method="llm",
                          data=board(chipset=src("Z890", confidence=0.5),
                                     max_memory_gb={"value": None}))
    assert set(rec.low_confidence_fields()) == {"chipset", "max_memory_gb"}
    assert rec.low_confidence_fields(threshold=0.4) == ["max_memory_gb"]


# ---------------------------------------------------------------- parser
@pytest.fixture
def doc():
    return ParsedDocument(doc_id="abc", source_file="manual.pdf", page_count=3, pages=[
        ParsedPage(1, "Welcome to your new motherboard."),
        ParsedPage(2, "Specifications\nSocket: LGA 1851\nChipset: Intel Z890"),
        ParsedPage(3, "Memory QVL", tables=[[["Vendor", "Part No."], ["Corsair", "CMK32GX5M2B6000C30"]]]),
    ])


def test_score_page_weights_keywords_and_tables(doc):
    assert score_page(doc.pages[0], {"socket": 3}) == 0
    assert score_page(doc.pages[1], {"socket": 3}) == 3
    assert score_page(doc.pages[2], {"socket": 3}) == 3            # one table = +3


def test_find_relevant_pages_ranks_and_filters(doc):
    ranked = find_relevant_pages(doc)
    assert [p for p, _ in ranked] == [2, 3]                       # page 1 is below min_score
    assert ranked[0][1] >= ranked[1][1]
    assert len(find_relevant_pages(doc, top_n=1)) == 1


def test_evidence_in_page(doc):
    assert evidence_in_page(doc, 2, "socket:   lga 1851")         # whitespace/case-insensitive
    assert evidence_in_page(doc, 3, "CMK32GX5M2B6000C30")         # found in table cells
    assert not evidence_in_page(doc, 2, "CMK32GX5M2B6000C30")     # wrong page
    assert not evidence_in_page(doc, 9, "LGA 1851")               # out of range


def test_parsed_document_json_round_trip(doc, tmp_path):
    out = tmp_path / "nested" / "doc.json"
    doc.to_json(out)
    assert ParsedDocument.from_json(out) == doc


def test_parse_pdf_extracts_pages_and_hash(tmp_path):
    pdf = make_pdf(tmp_path / "manual.pdf", ["Specifications Socket LGA1851", "Memory DDR5 support"])
    parsed = parse_pdf(pdf)
    assert parsed.page_count == 2
    assert parsed.source_file == "manual.pdf"
    assert parsed.doc_id == sha256_file(pdf) and len(parsed.doc_id) == 64
    assert "LGA1851" in parsed.page_text(1)
    assert evidence_in_page(parsed, 2, "DDR5 support")

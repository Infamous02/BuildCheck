"""
BuildCheck document parser.

Turns a manufacturer PDF into page-level text and tables, keyed by a content
hash so every downstream record can point back to the exact file it came from.

Usage:
    py -m ingest.parse path/to/manual.pdf
    py -m ingest.parse path/to/manual.pdf --out parsed/z890.json
    py -m ingest.parse path/to/manual.pdf --find memory qvl socket
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pdfplumber

# Keywords that mark pages worth sending to extraction. Weighted so a page
# headed "Specifications" outranks one that mentions DDR5 once in passing.
DEFAULT_KEYWORDS: dict[str, int] = {
    "specifications": 5,
    "qualified vendor list": 5,
    "qvl": 4,
    "cpu support": 5,
    "memory": 2,
    "ddr5": 2,
    "ddr4": 2,
    "socket": 3,
    "chipset": 3,
    "form factor": 3,
    "bios": 1,
}


@dataclass
class ParsedPage:
    page: int                         # 1-based, matches what a reviewer sees in a PDF viewer
    text: str
    tables: list[list[list[str]]] = field(default_factory=list)


@dataclass
class ParsedDocument:
    doc_id: str                       # SHA-256 of the file bytes
    source_file: str
    page_count: int
    pages: list[ParsedPage]

    def page_text(self, page: int) -> str:
        return self.pages[page - 1].text

    def to_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def from_json(cls, path: Path) -> "ParsedDocument":
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["pages"] = [ParsedPage(**p) for p in raw["pages"]]
        return cls(**raw)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# Bullet glyphs from symbol fonts that pdfplumber maps to control codes
# (seen in Gigabyte manuals). Normalized so extracted evidence can match.
_GLYPH_FIXES = str.maketrans({"\x1b": "•", "\x84": "•", "\x8a": "•"})


def _clean_text(text: str) -> str:
    return text.translate(_GLYPH_FIXES)


def _clean_cell(cell) -> str:
    if cell is None:
        return ""
    return re.sub(r"\s+", " ", _clean_text(str(cell))).strip()


def parse_pdf(path: str | Path) -> ParsedDocument:
    """Extract text and tables from every page of a PDF."""
    path = Path(path)
    pages: list[ParsedPage] = []

    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = _clean_text(page.extract_text() or "")
            tables = []
            for table in page.extract_tables():
                rows = [[_clean_cell(c) for c in row] for row in table]
                rows = [r for r in rows if any(r)]          # drop fully empty rows
                if rows:
                    tables.append(rows)
            pages.append(ParsedPage(page=i, text=text, tables=tables))

    return ParsedDocument(
        doc_id=sha256_file(path),
        source_file=path.name,
        page_count=len(pages),
        pages=pages,
    )


def score_page(page: ParsedPage, keywords: dict[str, int]) -> int:
    text = page.text.lower()
    score = sum(weight * text.count(kw) for kw, weight in keywords.items())
    # Pages with tables are much more likely to hold specs or QVL data.
    if page.tables:
        score += 3 * len(page.tables)
    return score


def find_relevant_pages(
    doc: ParsedDocument,
    keywords: dict[str, int] | None = None,
    top_n: int = 10,
    min_score: int = 5,
) -> list[tuple[int, int]]:
    """Return (page_number, score) for the pages most worth extracting from."""
    keywords = keywords or DEFAULT_KEYWORDS
    scored = [(p.page, score_page(p, keywords)) for p in doc.pages]
    scored = [s for s in scored if s[1] >= min_score]
    scored.sort(key=lambda s: s[1], reverse=True)
    return scored[:top_n]


def evidence_in_page(doc: ParsedDocument, page: int, evidence: str) -> bool:
    """
    True if `evidence` appears on `page`, ignoring whitespace and case.
    Used by validation to reject extracted values whose quoted evidence
    doesn't actually exist in the source.
    """
    if not 1 <= page <= doc.page_count:
        return False
    squash = lambda s: re.sub(r"\s+", "", s).lower()
    page_blob = squash(doc.page_text(page))
    for table in doc.pages[page - 1].tables:
        page_blob += squash(" ".join(" ".join(r) for r in table))
    return squash(evidence) in page_blob


def main() -> None:
    ap = argparse.ArgumentParser(description="Parse a manufacturer PDF for BuildCheck.")
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--out", type=Path, help="Write parsed JSON here (default: parsed/<name>.json)")
    ap.add_argument("--find", nargs="*", help="Override keywords for page ranking")
    args = ap.parse_args()

    # Windows consoles default to cp1252; don't crash on characters it can't show.
    sys.stdout.reconfigure(errors="replace")

    doc = parse_pdf(args.pdf)
    out = args.out or Path("parsed") / f"{args.pdf.stem}.json"
    doc.to_json(out)

    total_tables = sum(len(p.tables) for p in doc.pages)
    print(f"Parsed {doc.source_file}: {doc.page_count} pages, {total_tables} tables")
    print(f"doc_id: {doc.doc_id[:16]}...")
    print(f"Saved:  {out}")

    kw = {k.lower(): 3 for k in args.find} if args.find else None
    print("\nMost relevant pages:")
    for page, score in find_relevant_pages(doc, kw):
        p = doc.pages[page - 1]
        first_line = (p.text.strip().splitlines() or [""])[0][:70]
        print(f"  p.{page:<4} score {score:<4} tables {len(p.tables)}  {first_line}")


if __name__ == "__main__":
    main()

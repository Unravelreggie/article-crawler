"""Extract numbered references from text PDFs. Scans require a separate OCR worker."""
import re

import pymupdf

from .sources import clean_doi

HEADING = re.compile(r"^\s*(?:\d+\.?\s*)?(references|bibliography|literature cited|参考文献)\s*$", re.I)
START = re.compile(r"^\s*(?:\[\d+\]|\d{1,4}[.)])\s+")
STOP = re.compile(r"^\s*(appendix|supplementary (?:material|information)|acknowledg(?:e)?ments)\s*$", re.I)


def extract_references(pdf: bytes) -> list[tuple[str, str | None]]:
    if len(pdf) < 5 or not pdf.startswith(b"%PDF"):
        raise ValueError("Invalid PDF")
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        if doc.is_encrypted:
            raise ValueError("Encrypted PDF")
        text = "\n".join(page.get_text(sort=True) for page in doc)
    if len(text.strip()) < 100:
        raise ValueError("No selectable text; OCR required")
    lines = text.splitlines()
    headings = [i for i, line in enumerate(lines) if HEADING.match(line)]
    if not headings:
        raise ValueError("References section not found")
    entries, current = [], []
    for line in lines[headings[-1] + 1:]:
        line = line.strip()
        if STOP.match(line):
            break
        if not line:
            continue
        if START.match(line):
            if current:
                entries.append(" ".join(current))
            current = [START.sub("", line, count=1)]
        elif current:
            current.append(line)
    if current:
        entries.append(" ".join(current))
    cleaned = [re.sub(r"\s+", " ", value).strip() for value in entries]
    entries = [(value, clean_doi(value)) for value in cleaned if len(value) >= 20]
    if len(entries) > 500:
        raise ValueError("More than 500 numbered references; split the PDF")
    return entries

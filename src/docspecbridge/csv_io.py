from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .canonical import new_document, validate_document


def _read_csv_text(path: Path) -> tuple[str, str]:
    """Read common enterprise CSV encodings without adding a charset dependency."""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def _detect_dialect(text: str) -> tuple[csv.Dialect, str]:
    sample = text[:65536]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        return dialect, str(dialect.delimiter)
    except csv.Error:
        # French/European CSV commonly uses semicolons; otherwise comma is the safest fallback.
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
        class _Fallback(csv.excel):
            pass
        _Fallback.delimiter = delimiter
        return _Fallback(), delimiter


def canonical_from_csv(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    text, encoding = _read_csv_text(path)
    dialect, delimiter = _detect_dialect(text)
    rows = list(csv.reader(text.splitlines(), dialect=dialect))

    doc = new_document(
        title=path.stem,
        source={
            "type": "csv",
            "original_path": str(path),
            "extension": ".csv",
            "encoding": encoding,
            "delimiter": delimiter,
        },
    )

    if rows:
        width = max(len(row) for row in rows)
        table_rows: list[dict[str, Any]] = []
        for ridx, row in enumerate(rows):
            padded = list(row) + [""] * (width - len(row))
            cells = []
            for value in padded:
                cells.append({
                    "type": "table_cell",
                    "header": ridx == 0,
                    "colspan": 1,
                    "rowspan": 1,
                    "style": {},
                    "blocks": [{"type": "paragraph", "inlines": [{"type": "text", "text": str(value), "marks": []}]}],
                })
            table_rows.append({"type": "table_row", "cells": cells})
        doc["blocks"] = [{"type": "table", "rows": table_rows}]
    else:
        doc["blocks"] = []

    validate_document(doc)
    return doc, {"encoding": encoding, "delimiter": delimiter, "rows": len(rows), "columns": max((len(r) for r in rows), default=0)}

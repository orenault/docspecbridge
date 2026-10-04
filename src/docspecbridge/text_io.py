from __future__ import annotations

from pathlib import Path
from typing import Any

from .canonical import new_document, validate_document


def _read_text(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def canonical_from_plain_text(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    text, encoding = _read_text(path)
    doc = new_document(
        title=path.stem,
        source={"type": "text", "original_path": str(path), "extension": path.suffix.lower(), "encoding": encoding},
    )
    blocks: list[dict[str, Any]] = []
    paragraph: list[str] = []

    def flush() -> None:
        nonlocal paragraph
        if paragraph:
            joined = "\n".join(paragraph).strip("\n")
            inlines: list[dict[str, Any]] = []
            for idx, line in enumerate(joined.splitlines()):
                if idx:
                    inlines.append({"type": "hard_break"})
                inlines.append({"type": "text", "text": line, "marks": []})
            if inlines:
                blocks.append({"type": "paragraph", "inlines": inlines})
            paragraph = []

    for line in text.splitlines():
        if line.strip():
            paragraph.append(line)
        else:
            flush()
    flush()
    doc["blocks"] = blocks
    validate_document(doc)
    return doc, {"encoding": encoding, "lines": len(text.splitlines())}

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<target>[^)]+)\)")


def _remove_image_targets(markdown: str, targets: set[str]) -> str:
    if not targets:
        return markdown

    def repl(match: re.Match[str]) -> str:
        target = match.group("target").strip().replace("\\", "/")
        return "" if target in targets else match.group(0)

    return IMAGE_RE.sub(repl, markdown)


def _collapse_repeated_images(markdown: str) -> str:
    seen: set[str] = set()

    def repl(match: re.Match[str]) -> str:
        target = match.group("target").strip().replace("\\", "/")
        if target in seen:
            return ""
        seen.add(target)
        return match.group(0)

    return IMAGE_RE.sub(repl, markdown)


def build_rag_markdown(
    markdown: str,
    asset_meta: list[dict[str, Any]],
    profile: dict[str, Any],
) -> str:
    """Build a conservative, token-efficient Markdown view for retrieval.

    Layout geometry never enters this text. It remains in manifest/document JSON.
    The cleanup removes only explicitly configured layout artifacts and repeated image
    references; it does not paraphrase or reduce domain text.
    """
    result = markdown
    remove_targets: set[str] = set()
    roles_by_asset: dict[str, set[str]] = {}
    for item in asset_meta:
        rel = str(item.get("file") or "")
        if not rel:
            continue
        roles_by_asset.setdefault(rel, set()).add(str(item.get("role") or "body"))
    for rel, roles in roles_by_asset.items():
        if roles <= {"header"} and not profile.get("include_header_images", False):
            remove_targets.add(rel)
        if roles <= {"footer"} and not profile.get("include_footer_images", False):
            remove_targets.add(rel)
    result = _remove_image_targets(result, remove_targets)

    if not profile.get("keep_image_references", True):
        result = IMAGE_RE.sub("", result)
    elif profile.get("collapse_repeated_images", True):
        result = _collapse_repeated_images(result)

    # Remove mechanically empty formatting artifacts while keeping all substantive text.
    result = re.sub(r"(?m)^\s*(?:\*\*|__|\*|_)\s*$", "", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip() + "\n"


def chunk_markdown(markdown: str, max_characters: int = 1600, overlap: int = 150) -> list[dict[str, Any]]:
    """Simple heading-aware chunker for the MVP.

    It intentionally preserves Markdown tables and list lines as textual content and
    attaches the current heading path as metadata instead of injecting layout metadata
    into the chunk text.
    """
    max_characters = max(300, int(max_characters))
    overlap = max(0, min(int(overlap), max_characters // 3))

    heading_stack: list[str] = []
    sections: list[tuple[list[str], list[str]]] = []
    current_lines: list[str] = []
    current_path: list[str] = []

    def flush_section() -> None:
        nonlocal current_lines
        if current_lines:
            sections.append((list(current_path), current_lines))
            current_lines = []

    for line in markdown.splitlines():
        match = HEADING_RE.match(line)
        if match:
            flush_section()
            level = len(match.group(1))
            title = match.group(2).strip()
            heading_stack[:] = heading_stack[: level - 1]
            while len(heading_stack) < level - 1:
                heading_stack.append("")
            if len(heading_stack) == level - 1:
                heading_stack.append(title)
            else:
                heading_stack[level - 1] = title
            current_path = [item for item in heading_stack if item]
            current_lines.append(line)
        else:
            current_lines.append(line)
    flush_section()

    chunks: list[dict[str, Any]] = []
    chunk_id = 0
    for path, lines in sections:
        text = "\n".join(lines).strip()
        if not text:
            continue
        if len(text) <= max_characters:
            chunk_id += 1
            chunks.append({"id": chunk_id, "heading_path": path, "content": text})
            continue

        paragraphs = re.split(r"\n\s*\n", text)
        buffer = ""
        for paragraph in paragraphs:
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            candidate = paragraph if not buffer else buffer + "\n\n" + paragraph
            if len(candidate) <= max_characters:
                buffer = candidate
                continue
            if buffer:
                chunk_id += 1
                chunks.append({"id": chunk_id, "heading_path": path, "content": buffer})
                tail = buffer[-overlap:] if overlap else ""
                buffer = (tail + "\n\n" + paragraph).strip() if tail else paragraph
            else:
                # Very large paragraph/table: hard split as last resort.
                start = 0
                while start < len(paragraph):
                    end = min(len(paragraph), start + max_characters)
                    part = paragraph[start:end].strip()
                    if part:
                        chunk_id += 1
                        chunks.append({"id": chunk_id, "heading_path": path, "content": part})
                    if end >= len(paragraph):
                        break
                    start = max(0, end - overlap)
                buffer = ""
        if buffer:
            chunk_id += 1
            chunks.append({"id": chunk_id, "heading_path": path, "content": buffer})
    return chunks


def write_chunks_jsonl(path: Path, chunks: list[dict[str, Any]], source_metadata: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            row = {
                "id": chunk["id"],
                "heading_path": chunk.get("heading_path") or [],
                "content": chunk["content"],
                "source": source_metadata,
            }
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

from __future__ import annotations

import copy
import html as html_std
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from lxml import etree, html


SCHEMA_VERSION = "1.0"


def new_document(*, title: str, source: dict[str, Any] | None = None, assets: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "title": title,
        "metadata": {"title": title, "title_source": "provided"},
        "source": source or {},
        "blocks": [],
        "assets": assets or [],
        "diagnostics": {"warnings": []},
    }


def _tag(node: etree._Element) -> str:
    if not isinstance(node.tag, str):
        return ""
    return etree.QName(node).localname.lower()


def _style_map(style: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in str(style or "").split(";"):
        if ":" not in item:
            continue
        key, value = item.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key and value:
            out[key] = value
    return out


def _marks_for(node: etree._Element, inherited: list[dict[str, Any]]) -> list[dict[str, Any]]:
    marks = [copy.deepcopy(m) for m in inherited]
    tag = _tag(node)
    simple = {
        "strong": "strong", "b": "strong", "em": "em", "i": "em",
        "u": "underline", "s": "strike", "del": "strike", "code": "code",
    }
    if tag in simple and not any(m.get("type") == simple[tag] for m in marks):
        marks.append({"type": simple[tag]})
    if tag == "a" and node.get("href"):
        marks.append({"type": "link", "href": str(node.get("href"))})
    style = _style_map(node.get("style"))
    if style.get("color"):
        marks.append({"type": "color", "value": style["color"]})
    if style.get("background-color"):
        marks.append({"type": "background", "value": style["background-color"]})
    return marks


def _push_text(out: list[dict[str, Any]], text: str | None, marks: list[dict[str, Any]]) -> None:
    if text is None or text == "":
        return
    if out and out[-1].get("type") == "text" and out[-1].get("marks") == marks:
        out[-1]["text"] = str(out[-1].get("text") or "") + text
    else:
        out.append({"type": "text", "text": text, "marks": copy.deepcopy(marks)})


def _parse_inlines(node: etree._Element, inherited: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    inherited = inherited or []
    out: list[dict[str, Any]] = []
    marks = _marks_for(node, inherited)
    _push_text(out, node.text, marks)
    for child in node:
        tag = _tag(child)
        if tag == "br":
            out.append({"type": "hard_break"})
        elif tag == "img":
            image: dict[str, Any] = {
                "type": "image",
                "src": str(child.get("src") or ""),
                "alt": str(child.get("alt") or ""),
            }
            for key in ("width", "height"):
                raw = child.get(key)
                if raw:
                    try:
                        image[key] = int(float(raw))
                    except ValueError:
                        image[key] = raw
            out.append(image)
        else:
            out.extend(_parse_inlines(child, marks))
        _push_text(out, child.tail, marks)
    return out


def _plain_inline(inlines: Iterable[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in inlines:
        kind = item.get("type")
        if kind == "text":
            parts.append(str(item.get("text") or ""))
        elif kind == "hard_break":
            parts.append("\n")
        elif kind == "image":
            parts.append(str(item.get("alt") or ""))
    return "".join(parts).strip()


def _parse_list(node: etree._Element) -> dict[str, Any]:
    ordered = _tag(node) == "ol"
    try:
        start = int(node.get("start") or 1)
    except ValueError:
        start = 1
    items: list[dict[str, Any]] = []
    for li in [c for c in node if _tag(c) == "li"]:
        blocks: list[dict[str, Any]] = []
        # A list item can contain direct text plus block children. Preserve direct text
        # as a paragraph without flattening nested lists.
        if (li.text or "").strip():
            blocks.append({"type": "paragraph", "inlines": [{"type": "text", "text": li.text, "marks": []}]})
        for child in li:
            tag = _tag(child)
            if tag in {"ul", "ol"}:
                blocks.append(_parse_list(child))
            elif tag in {"p", "div"}:
                blocks.append({"type": "paragraph", "inlines": _parse_inlines(child)})
            elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                blocks.append({"type": "heading", "level": int(tag[1]), "inlines": _parse_inlines(child)})
            elif tag == "table":
                blocks.append(_parse_table(child))
            elif tag == "img":
                blocks.append({"type": "image", "src": str(child.get("src") or ""), "alt": str(child.get("alt") or "")})
            elif "".join(child.itertext()).strip():
                blocks.append({"type": "paragraph", "inlines": _parse_inlines(child)})
            if child.tail and child.tail.strip():
                blocks.append({"type": "paragraph", "inlines": [{"type": "text", "text": child.tail, "marks": []}]})
        items.append({"blocks": blocks})
    return {"type": "list", "ordered": ordered, "start": start, "items": items}


def _parse_table(node: etree._Element) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    row_nodes = node.xpath("./tr | ./thead/tr | ./tbody/tr | ./tfoot/tr")
    for row in row_nodes:
        cells: list[dict[str, Any]] = []
        for cell in row.xpath("./th | ./td"):
            tag = _tag(cell)
            try:
                colspan = max(1, int(cell.get("colspan") or 1))
            except ValueError:
                colspan = 1
            try:
                rowspan = max(1, int(cell.get("rowspan") or 1))
            except ValueError:
                rowspan = 1
            blocks = _parse_container_blocks(cell)
            if not blocks:
                blocks = [{"type": "paragraph", "inlines": []}]
            cells.append({
                "type": "table_cell",
                "header": tag == "th",
                "colspan": colspan,
                "rowspan": rowspan,
                "style": _style_map(cell.get("style")),
                "blocks": blocks,
            })
        rows.append({"type": "table_row", "cells": cells})
    table = {"type": "table", "rows": rows}
    table["logical_columns"] = table_logical_width(table)
    return table


def _parse_container_blocks(container: etree._Element) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if container.text and container.text.strip():
        blocks.append({"type": "paragraph", "inlines": [{"type": "text", "text": container.text, "marks": []}]})
    for child in container:
        tag = _tag(child)
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            blocks.append({"type": "heading", "level": int(tag[1]), "inlines": _parse_inlines(child), "anchor": child.get("id")})
        elif tag == "p":
            blocks.append({"type": "paragraph", "inlines": _parse_inlines(child)})
        elif tag in {"ul", "ol"}:
            blocks.append(_parse_list(child))
        elif tag == "table":
            blocks.append(_parse_table(child))
        elif tag == "blockquote":
            blocks.append({"type": "blockquote", "blocks": _parse_container_blocks(child)})
        elif tag == "pre":
            blocks.append({"type": "code_block", "text": "".join(child.itertext()), "language": ""})
        elif tag == "hr":
            blocks.append({"type": "rule"})
        elif tag == "docspecbridge-toc":
            blocks.append({"type": "toc"})
        elif tag == "img":
            image = {"type": "image", "src": str(child.get("src") or ""), "alt": str(child.get("alt") or "")}
            for key in ("width", "height"):
                if child.get(key):
                    try:
                        image[key] = int(float(child.get(key)))
                    except ValueError:
                        pass
            blocks.append(image)
        elif tag in {"div", "section", "article", "main", "body", "header", "footer", "span"}:
            nested = _parse_container_blocks(child)
            if nested:
                blocks.extend(nested)
            elif "".join(child.itertext()).strip():
                blocks.append({"type": "paragraph", "inlines": _parse_inlines(child)})
        else:
            text = "".join(child.itertext()).strip()
            if text:
                blocks.append({"type": "paragraph", "inlines": _parse_inlines(child)})
        if child.tail and child.tail.strip():
            blocks.append({"type": "paragraph", "inlines": [{"type": "text", "text": child.tail, "marks": []}]})
    return blocks


def canonical_from_xhtml(
    fragment: str,
    *,
    title: str,
    source: dict[str, Any] | None = None,
    assets: list[dict[str, Any]] | None = None,
    header_assets_once: bool = False,
) -> dict[str, Any]:
    # DocSpecBridge's Confluence TOC marker is not HTML; turn it into a private node
    # before parsing so the canonical representation never contains obsolete page refs.
    fragment = str(fragment or "").replace("[[_TOC_]]", "<docspecbridge-toc></docspecbridge-toc>")
    root = html.fragment_fromstring(fragment, create_parent="div")
    doc = new_document(title=title, source=source, assets=copy.deepcopy(assets or []))
    doc["blocks"] = _parse_container_blocks(root)

    if header_assets_once:
        existing = {str(b.get("src") or "") for b in doc["blocks"] if b.get("type") == "image"}
        headers: list[dict[str, Any]] = []
        for asset in doc["assets"]:
            if str(asset.get("role") or "") != "header":
                continue
            rel = str(asset.get("file") or "")
            if rel and rel not in existing:
                headers.append({"type": "image", "src": rel, "alt": "", "role": "header"})
        if headers:
            doc["blocks"] = headers + doc["blocks"]
    validate_document(doc)
    return doc


def canonical_from_html_document(
    text: str,
    *,
    title: str | None = None,
    source: dict[str, Any] | None = None,
    assets: list[dict[str, Any]] | None = None,
    prefer_main: bool = True,
) -> dict[str, Any]:
    tree = html.fromstring(text or "<html><body></body></html>")
    # Never turn executable/style payloads from a web page into document prose.
    # HTML is a content source here, not a browser snapshot.
    for noisy in tree.xpath("//script | //style | //noscript | //template"):
        parent = noisy.getparent()
        if parent is not None:
            parent.remove(noisy)
    detected_title_source = "provided"
    if title is None:
        # For documentary HTML, the visible H1 is usually a better publication title
        # than the browser/tab title. Fall back to <title>, then to a generic value.
        h1_nodes = tree.xpath("//main//h1 | //article//h1 | //body//h1")
        title_nodes = tree.xpath("//title")
        if h1_nodes:
            title = _plain_inline(_parse_inlines(h1_nodes[0]))
            detected_title_source = "document_heading"
        elif title_nodes:
            title = _plain_inline(_parse_inlines(title_nodes[0]))
            detected_title_source = "html_title"
        else:
            title = "document"
            detected_title_source = "fallback"
    container = None
    if prefer_main:
        for xpath in ("//main", "//article"):
            nodes = tree.xpath(xpath)
            if nodes:
                container = nodes[0]
                break
    if container is None:
        bodies = tree.xpath("//body")
        container = bodies[0] if bodies else tree
    doc = new_document(title=title or "document", source=source, assets=copy.deepcopy(assets or []))
    doc.setdefault("metadata", {})["title_source"] = detected_title_source
    doc["blocks"] = _parse_container_blocks(container)
    validate_document(doc)
    return doc


def table_logical_width(table: dict[str, Any]) -> int:
    rows = table.get("rows") or []
    occupied: dict[int, int] = {}
    width = 0
    for row_idx, row in enumerate(rows):
        col = 0
        while occupied.get(col, 0) > row_idx:
            col += 1
        for cell in row.get("cells") or []:
            while occupied.get(col, 0) > row_idx:
                col += 1
            colspan = max(1, int(cell.get("colspan") or 1))
            rowspan = max(1, int(cell.get("rowspan") or 1))
            for c in range(col, col + colspan):
                if rowspan > 1:
                    occupied[c] = max(occupied.get(c, 0), row_idx + rowspan)
            col += colspan
        width = max(width, col)
    return width


def table_grid(table: dict[str, Any]) -> list[list[dict[str, Any] | None]]:
    """Expand cell positions without duplicating cell content.

    The owner cell is placed only at its top-left coordinate. Coordinates covered by
    colspan/rowspan contain None. This is the invariant used by human Markdown and RAG.
    """
    rows = table.get("rows") or []
    width = max(1, int(table.get("logical_columns") or table_logical_width(table) or 1))
    grid: list[list[dict[str, Any] | None]] = [[None for _ in range(width)] for _ in range(len(rows))]
    covered: set[tuple[int, int]] = set()
    for r_idx, row in enumerate(rows):
        c_idx = 0
        for cell in row.get("cells") or []:
            while (r_idx, c_idx) in covered:
                c_idx += 1
            colspan = max(1, int(cell.get("colspan") or 1))
            rowspan = max(1, int(cell.get("rowspan") or 1))
            while c_idx >= width:
                width += 1
                for g in grid:
                    g.append(None)
            grid[r_idx][c_idx] = cell
            for rr in range(r_idx, min(len(rows), r_idx + rowspan)):
                for cc in range(c_idx, c_idx + colspan):
                    while cc >= len(grid[rr]):
                        grid[rr].append(None)
                    if rr != r_idx or cc != c_idx:
                        covered.add((rr, cc))
            c_idx += colspan
    return grid


def _walk_blocks(blocks: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for block in blocks:
        yield block
        if block.get("type") == "list":
            for item in block.get("items") or []:
                yield from _walk_blocks(item.get("blocks") or [])
        elif block.get("type") == "blockquote":
            yield from _walk_blocks(block.get("blocks") or [])
        elif block.get("type") == "table":
            for row in block.get("rows") or []:
                for cell in row.get("cells") or []:
                    yield from _walk_blocks(cell.get("blocks") or [])


def validate_document(doc: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    for index, block in enumerate(_walk_blocks(doc.get("blocks") or []), 1):
        if block.get("type") != "table":
            continue
        table = block
        grid = table_grid(table)
        width = max((len(row) for row in grid), default=0)
        # Count contents only on owner cells. If a converter had already duplicated a
        # merged value into physical cells, this does not silently infer a merge; source
        # adapters must provide rowspan/colspan explicitly.
        for r_idx, row in enumerate(table.get("rows") or []):
            for cell in row.get("cells") or []:
                if int(cell.get("rowspan") or 1) < 1 or int(cell.get("colspan") or 1) < 1:
                    warnings.append(f"table {index}: invalid span at row {r_idx + 1}")
        table["logical_columns"] = width
        table["validation"] = {"status": "ok" if not warnings else "warning", "logical_columns": width}
    doc.setdefault("diagnostics", {}).setdefault("warnings", []).extend(warnings)
    return warnings


def inline_plain_text(inlines: Iterable[dict[str, Any]]) -> str:
    return _plain_inline(inlines)


def block_plain_text(block: dict[str, Any]) -> str:
    kind = block.get("type")
    if kind in {"paragraph", "heading"}:
        return inline_plain_text(block.get("inlines") or [])
    if kind == "code_block":
        return str(block.get("text") or "")
    if kind == "image":
        return str(block.get("alt") or "")
    return ""


def apply_asset_display_geometry(doc: dict[str, Any]) -> dict[str, Any]:
    """Attach source display dimensions/roles to canonical image nodes.

    Asset geometry is metadata, not prose. Keeping it on canonical image nodes lets
    HTML/Confluence preserve source display size while human/RAG Markdown remains lean.
    Multiple occurrences of the same physical image are consumed in source order.
    """
    assets = {str(item.get("file") or "").replace("\\", "/"): item for item in (doc.get("assets") or []) if item.get("file")}
    counters: dict[str, int] = {}

    def decorate(image: dict[str, Any]) -> None:
        src = str(image.get("src") or "").replace("\\", "/")
        asset = assets.get(src)
        if not asset:
            return
        role = str(asset.get("role") or "").strip()
        if role and not image.get("role"):
            image["role"] = role
        occurrences = list(asset.get("display_occurrences") or [])
        if not occurrences:
            return
        idx = counters.get(src, 0)
        occurrence = occurrences[min(idx, len(occurrences) - 1)]
        counters[src] = idx + 1
        display = dict(occurrence.get("display") or {})
        try:
            width = int(display.get("width_px") or 0)
            height = int(display.get("height_px") or 0)
        except (TypeError, ValueError):
            return
        if width > 0 and height > 0:
            image.setdefault("width", width)
            image.setdefault("height", height)
            image.setdefault("display", display)
            if occurrence.get("page") is not None:
                image.setdefault("page", occurrence.get("page"))
            if occurrence.get("slide") is not None:
                image.setdefault("slide", occurrence.get("slide"))

    def walk_inlines(inlines: Iterable[dict[str, Any]]) -> None:
        for inline in inlines:
            if inline.get("type") == "image":
                decorate(inline)

    def walk(blocks: Iterable[dict[str, Any]]) -> None:
        for block in blocks:
            kind = block.get("type")
            if kind == "image":
                decorate(block)
            elif kind in {"paragraph", "heading"}:
                walk_inlines(block.get("inlines") or [])
            elif kind == "list":
                for item in block.get("items") or []:
                    walk(item.get("blocks") or [])
            elif kind == "blockquote":
                walk(block.get("blocks") or [])
            elif kind == "table":
                for row in block.get("rows") or []:
                    for cell in row.get("cells") or []:
                        walk(cell.get("blocks") or [])

    walk(doc.get("blocks") or [])
    return doc

from __future__ import annotations

from typing import Any, Iterable

from .canonical import new_document, validate_document


def _marks(mark_list: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for mark in mark_list or []:
        kind = str(mark.get("type") or "")
        attrs = mark.get("attrs") or {}
        if kind == "strong": out.append({"type": "strong"})
        elif kind == "em": out.append({"type": "em"})
        elif kind == "strike": out.append({"type": "strike"})
        elif kind == "underline": out.append({"type": "underline"})
        elif kind == "code": out.append({"type": "code"})
        elif kind == "link" and attrs.get("href"): out.append({"type": "link", "href": attrs.get("href")})
        elif kind == "textColor" and attrs.get("color"): out.append({"type": "color", "value": attrs.get("color")})
        elif kind == "backgroundColor" and attrs.get("color"): out.append({"type": "background", "value": attrs.get("color")})
    return out


def _adf_inlines(nodes: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in nodes or []:
        kind = node.get("type")
        if kind == "text":
            out.append({"type": "text", "text": str(node.get("text") or ""), "marks": _marks(node.get("marks"))})
        elif kind == "hardBreak":
            out.append({"type": "hard_break"})
        elif kind == "mention":
            attrs = node.get("attrs") or {}
            out.append({"type": "text", "text": str(attrs.get("text") or attrs.get("id") or "@mention"), "marks": []})
        elif kind == "emoji":
            attrs = node.get("attrs") or {}
            out.append({"type": "text", "text": str(attrs.get("text") or attrs.get("shortName") or ""), "marks": []})
        elif kind in {"inlineCard", "blockCard"}:
            attrs = node.get("attrs") or {}
            url = str(attrs.get("url") or "")
            out.append({"type": "text", "text": url, "marks": [{"type": "link", "href": url}] if url else []})
    return out


def _adf_blocks(nodes: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for node in nodes or []:
        kind = str(node.get("type") or "")
        content = node.get("content") or []
        attrs = node.get("attrs") or {}
        if kind == "paragraph":
            blocks.append({"type": "paragraph", "inlines": _adf_inlines(content)})
        elif kind == "heading":
            blocks.append({"type": "heading", "level": int(attrs.get("level") or 1), "inlines": _adf_inlines(content)})
        elif kind in {"bulletList", "orderedList"}:
            items = []
            for item in content:
                if item.get("type") == "listItem":
                    items.append({"blocks": _adf_blocks(item.get("content") or [])})
            blocks.append({"type": "list", "ordered": kind == "orderedList", "start": int(attrs.get("order") or 1), "items": items})
        elif kind == "blockquote":
            blocks.append({"type": "blockquote", "blocks": _adf_blocks(content)})
        elif kind == "codeBlock":
            text = "".join(str(x.get("text") or "") for x in content if x.get("type") == "text")
            blocks.append({"type": "code_block", "text": text, "language": str(attrs.get("language") or "")})
        elif kind == "rule":
            blocks.append({"type": "rule"})
        elif kind == "table":
            rows = []
            for row_node in content:
                if row_node.get("type") != "tableRow":
                    continue
                cells = []
                for cell_node in row_node.get("content") or []:
                    if cell_node.get("type") not in {"tableCell", "tableHeader"}:
                        continue
                    cattrs = cell_node.get("attrs") or {}
                    style = {}
                    if cattrs.get("background"):
                        style["background-color"] = cattrs.get("background")
                    cells.append({
                        "type": "table_cell",
                        "header": cell_node.get("type") == "tableHeader",
                        "colspan": int(cattrs.get("colspan") or 1),
                        "rowspan": int(cattrs.get("rowspan") or 1),
                        "style": style,
                        "blocks": _adf_blocks(cell_node.get("content") or []),
                    })
                rows.append({"type": "table_row", "cells": cells})
            blocks.append({"type": "table", "rows": rows})
        elif kind in {"mediaSingle", "mediaGroup"}:
            for media in content:
                if media.get("type") != "media":
                    continue
                mattrs = media.get("attrs") or {}
                blocks.append({
                    "type": "image",
                    "src": str(mattrs.get("url") or mattrs.get("id") or ""),
                    "alt": str(mattrs.get("alt") or ""),
                    "media": mattrs,
                })
        elif kind in {"panel", "expand", "nestedExpand"}:
            blocks.append({"type": "blockquote", "blocks": _adf_blocks(content), "variant": kind})
        elif content:
            nested = _adf_blocks(content)
            if nested:
                blocks.extend(nested)
    return blocks


def canonical_from_adf(adf: dict[str, Any] | None, *, title: str, source: dict[str, Any] | None = None) -> dict[str, Any]:
    doc = new_document(title=title, source=source)
    if adf and adf.get("type") == "doc":
        doc["blocks"] = _adf_blocks(adf.get("content") or [])
    validate_document(doc)
    return doc


def _adf_marks(marks: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out = []
    for mark in marks or []:
        kind = mark.get("type")
        if kind in {"strong", "em", "strike", "underline", "code"}:
            out.append({"type": kind})
        elif kind == "link" and mark.get("href"):
            out.append({"type": "link", "attrs": {"href": mark.get("href")}})
        elif kind == "color" and mark.get("value"):
            out.append({"type": "textColor", "attrs": {"color": mark.get("value")}})
        elif kind == "background" and mark.get("value"):
            out.append({"type": "backgroundColor", "attrs": {"color": mark.get("value")}})
    return out


def _inlines_to_adf(inlines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for item in inlines or []:
        if item.get("type") == "text":
            node = {"type": "text", "text": str(item.get("text") or "")}
            marks = _adf_marks(item.get("marks"))
            if marks: node["marks"] = marks
            out.append(node)
        elif item.get("type") == "hard_break":
            out.append({"type": "hardBreak"})
        elif item.get("type") == "image":
            # Jira media upload is a separate REST workflow. Preserve the reference as a link text
            # rather than inventing an uploaded media id.
            src = str(item.get("src") or "")
            if src:
                out.append({"type": "text", "text": src, "marks": [{"type": "link", "attrs": {"href": src}}]})
    return out


def _blocks_to_adf(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for block in blocks or []:
        kind = block.get("type")
        if kind == "paragraph":
            out.append({"type": "paragraph", "content": _inlines_to_adf(block.get("inlines") or [])})
        elif kind == "heading":
            out.append({"type": "heading", "attrs": {"level": int(block.get("level") or 1)}, "content": _inlines_to_adf(block.get("inlines") or [])})
        elif kind == "list":
            items = [{"type": "listItem", "content": _blocks_to_adf(item.get("blocks") or [])} for item in block.get("items") or []]
            node = {"type": "orderedList" if block.get("ordered") else "bulletList", "content": items}
            if block.get("ordered") and int(block.get("start") or 1) != 1:
                node["attrs"] = {"order": int(block.get("start") or 1)}
            out.append(node)
        elif kind == "blockquote":
            out.append({"type": "blockquote", "content": _blocks_to_adf(block.get("blocks") or [])})
        elif kind == "code_block":
            node = {"type": "codeBlock", "content": [{"type": "text", "text": str(block.get("text") or "")}]} 
            if block.get("language"): node["attrs"] = {"language": block.get("language")}
            out.append(node)
        elif kind == "rule":
            out.append({"type": "rule"})
        elif kind == "table":
            rows = []
            for row in block.get("rows") or []:
                cells = []
                for cell in row.get("cells") or []:
                    attrs = {"colspan": int(cell.get("colspan") or 1), "rowspan": int(cell.get("rowspan") or 1)}
                    bg = (cell.get("style") or {}).get("background-color")
                    if bg: attrs["background"] = bg
                    cells.append({
                        "type": "tableHeader" if cell.get("header") else "tableCell",
                        "attrs": attrs,
                        "content": _blocks_to_adf(cell.get("blocks") or []) or [{"type": "paragraph", "content": []}],
                    })
                rows.append({"type": "tableRow", "content": cells})
            out.append({"type": "table", "content": rows})
        elif kind == "image":
            src = str(block.get("src") or "")
            if src:
                out.append({"type": "paragraph", "content": [{"type": "text", "text": src, "marks": [{"type": "link", "attrs": {"href": src}}]}]})
        elif kind == "diagram":
            mermaid = str(block.get("mermaid") or "")
            if mermaid:
                out.append({"type": "codeBlock", "attrs": {"language": "mermaid"}, "content": [{"type": "text", "text": mermaid}]})
    return out


def canonical_to_adf(doc: dict[str, Any]) -> dict[str, Any]:
    return {"version": 1, "type": "doc", "content": _blocks_to_adf(doc.get("blocks") or [])}

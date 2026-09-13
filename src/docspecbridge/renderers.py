from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any, Iterable

from .canonical import block_plain_text, inline_plain_text, table_grid


def _escape_md(text: str) -> str:
    # Escape characters that can accidentally become Markdown syntax while preserving
    # punctuation and ordinary prose. This specifically protects literal Word '*'.
    value = str(text or "").replace("\\", "\\\\")
    for token in ("*", "_", "`", "[", "]"):
        value = value.replace(token, "\\" + token)
    return value


def _render_inlines_md(inlines: Iterable[dict[str, Any]], *, rag: bool = False) -> str:
    out: list[str] = []
    for item in inlines:
        kind = item.get("type")
        if kind == "hard_break":
            out.append("  \n" if not rag else "\n")
            continue
        if kind == "image":
            src = str(item.get("src") or "")
            alt = _escape_md(str(item.get("alt") or ""))
            if src:
                out.append(f"![{alt}]({src})")
            continue
        if kind != "text":
            continue
        text = _escape_md(str(item.get("text") or ""))
        marks = item.get("marks") or []
        href = next((str(m.get("href") or "") for m in marks if m.get("type") == "link"), "")
        types = {str(m.get("type")) for m in marks}
        if "code" in types:
            text = "`" + text.replace("`", "\\`") + "`"
        if "strong" in types:
            text = f"**{text}**"
        if "em" in types:
            text = f"*{text}*"
        if "strike" in types:
            text = f"~~{text}~~"
        # Markdown has no portable underline/color/background. The information remains
        # canonical and is used by HTML/Confluence renderers; human/RAG Markdown stays clean.
        if href:
            text = f"[{text}]({href})"
        out.append(text)
    return "".join(out)


def _cell_md(cell: dict[str, Any] | None, *, rag: bool) -> str:
    if cell is None:
        return ""
    parts: list[str] = []
    for block in cell.get("blocks") or []:
        kind = block.get("type")
        if kind in {"paragraph", "heading"}:
            value = _render_inlines_md(block.get("inlines") or [], rag=rag).strip()
            if value:
                parts.append(value)
        elif kind == "list":
            list_text = _render_list_md(block, rag=rag, indent=0).replace("\n", "; ")
            if list_text:
                parts.append(list_text)
        elif kind == "image" and not rag:
            src = str(block.get("src") or "")
            if src:
                parts.append(f"![{_escape_md(str(block.get('alt') or ''))}]({src})")
        elif kind == "code_block":
            parts.append(_escape_md(str(block.get("text") or "")))
    return " / ".join(parts).replace("|", "\\|")


def _render_table_md(table: dict[str, Any], *, rag: bool) -> str:
    grid = table_grid(table)
    if not grid:
        return ""
    width = max(len(row) for row in grid)
    lines: list[str] = []
    first = [_cell_md(grid[0][c] if c < len(grid[0]) else None, rag=rag) for c in range(width)]
    lines.append("| " + " | ".join(first) + " |")
    lines.append("| " + " | ".join("---" for _ in range(width)) + " |")
    for row in grid[1:]:
        values = [_cell_md(row[c] if c < len(row) else None, rag=rag) for c in range(width)]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def _render_list_md(block: dict[str, Any], *, rag: bool, indent: int = 0) -> str:
    lines: list[str] = []
    ordered = bool(block.get("ordered"))
    start = int(block.get("start") or 1)
    for idx, item in enumerate(block.get("items") or []):
        prefix = f"{start + idx}." if ordered else "-"
        item_blocks = item.get("blocks") or []
        first_text = ""
        rest: list[dict[str, Any]] = []
        if item_blocks and item_blocks[0].get("type") == "paragraph":
            first_text = _render_inlines_md(item_blocks[0].get("inlines") or [], rag=rag).strip()
            rest = item_blocks[1:]
        else:
            rest = item_blocks
        lines.append(" " * indent + f"{prefix} {first_text}".rstrip())
        for child in rest:
            if child.get("type") == "list":
                lines.append(_render_list_md(child, rag=rag, indent=indent + 2))
            else:
                rendered = _render_block_md(child, rag=rag)
                if rendered:
                    lines.extend(" " * (indent + 2) + line for line in rendered.splitlines())
    return "\n".join(lines)


def _render_block_md(block: dict[str, Any], *, rag: bool) -> str:
    kind = block.get("type")
    if kind == "toc":
        # TOC is navigation, not document substance. Rich targets such as Confluence
        # generate it natively; human/RAG Markdown stays clean and HTML-free.
        return ""
    if kind == "heading":
        level = max(1, min(6, int(block.get("level") or 1)))
        return "#" * level + " " + _render_inlines_md(block.get("inlines") or [], rag=rag).strip()
    if kind == "paragraph":
        return _render_inlines_md(block.get("inlines") or [], rag=rag).strip()
    if kind == "list":
        return _render_list_md(block, rag=rag)
    if kind == "table":
        return _render_table_md(block, rag=rag)
    if kind == "image":
        if rag and str(block.get("role") or "") in {"header", "footer", "decorative"}:
            return ""
        src = str(block.get("src") or "")
        alt = _escape_md(str(block.get("alt") or ""))
        return f"![{alt}]({src})" if src else ""
    if kind == "blockquote":
        body = render_markdown({"blocks": block.get("blocks") or []}, rag=rag).strip()
        return "\n".join("> " + line for line in body.splitlines())
    if kind == "code_block":
        lang = str(block.get("language") or "")
        return f"```{lang}\n{block.get('text') or ''}\n```"
    if kind == "rule":
        return "---"
    if kind == "diagram":
        mermaid = str(block.get("mermaid") or "").strip()
        if mermaid:
            return f"```mermaid\n{mermaid}\n```"
        fallback = str(block.get("fallback_asset") or "")
        return f"![diagram]({fallback})" if fallback else ""
    return ""


def render_markdown(doc: dict[str, Any], *, rag: bool = False) -> str:
    parts = [_render_block_md(block, rag=rag) for block in doc.get("blocks") or []]
    text = "\n\n".join(part for part in parts if part and part.strip())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text + ("\n" if text else "")


def _asset_src(src: str, prefix: str) -> str:
    value = str(src or "")
    if not value or value.startswith(("http://", "https://", "data:", "#", "/")):
        return value
    return prefix + value if prefix else value


def _inline_html(inlines: Iterable[dict[str, Any]], *, asset_prefix: str = "") -> str:
    out: list[str] = []
    for item in inlines:
        kind = item.get("type")
        if kind == "hard_break":
            out.append("<br/>")
            continue
        if kind == "image":
            attrs = [f'src="{html.escape(_asset_src(str(item.get("src") or ""), asset_prefix), quote=True)}"']
            if item.get("alt"):
                attrs.append(f'alt="{html.escape(str(item.get("alt")), quote=True)}"')
            for key in ("width", "height"):
                if item.get(key):
                    attrs.append(f'{key}="{html.escape(str(item.get(key)), quote=True)}"')
            out.append("<img " + " ".join(attrs) + "/>")
            continue
        if kind != "text":
            continue
        value = html.escape(str(item.get("text") or ""))
        marks = item.get("marks") or []
        types = [str(m.get("type")) for m in marks]
        if "code" in types:
            value = f"<code>{value}</code>"
        if "strong" in types:
            value = f"<strong>{value}</strong>"
        if "em" in types:
            value = f"<em>{value}</em>"
        if "underline" in types:
            value = f"<u>{value}</u>"
        if "strike" in types:
            value = f"<s>{value}</s>"
        styles: list[str] = []
        for mark in marks:
            if mark.get("type") == "color" and mark.get("value"):
                styles.append("color: " + str(mark["value"]))
            if mark.get("type") == "background" and mark.get("value"):
                styles.append("background-color: " + str(mark["value"]))
        if styles:
            value = f'<span style="{html.escape(";".join(styles) + ";", quote=True)}">{value}</span>'
        link = next((str(m.get("href") or "") for m in marks if m.get("type") == "link"), "")
        if link:
            value = f'<a href="{html.escape(link, quote=True)}">{value}</a>'
        out.append(value)
    return "".join(out)


def _blocks_html(blocks: Iterable[dict[str, Any]], *, confluence: bool = False, asset_prefix: str = "") -> str:
    out: list[str] = []
    for block in blocks:
        kind = block.get("type")
        if kind == "toc":
            out.append("[[_TOC_]]" if confluence else '<nav class="toc-placeholder"></nav>')
        elif kind == "heading":
            level = max(1, min(6, int(block.get("level") or 1)))
            out.append(f"<h{level}>{_inline_html(block.get('inlines') or [], asset_prefix=asset_prefix)}</h{level}>")
        elif kind == "paragraph":
            out.append(f"<p>{_inline_html(block.get('inlines') or [], asset_prefix=asset_prefix)}</p>")
        elif kind == "image":
            attrs = [f'src="{html.escape(_asset_src(str(block.get("src") or ""), asset_prefix), quote=True)}"']
            if block.get("alt"):
                attrs.append(f'alt="{html.escape(str(block.get("alt")), quote=True)}"')
            for key in ("width", "height"):
                if block.get(key):
                    attrs.append(f'{key}="{html.escape(str(block.get(key)), quote=True)}"')
            out.append("<p><img " + " ".join(attrs) + "/></p>")
        elif kind == "list":
            tag = "ol" if block.get("ordered") else "ul"
            start_attr = f' start="{int(block.get("start") or 1)}"' if tag == "ol" and int(block.get("start") or 1) != 1 else ""
            items = []
            for item in block.get("items") or []:
                items.append("<li>" + _blocks_html(item.get("blocks") or [], confluence=confluence, asset_prefix=asset_prefix) + "</li>")
            out.append(f"<{tag}{start_attr}>" + "".join(items) + f"</{tag}>")
        elif kind == "table":
            rows = []
            for row in block.get("rows") or []:
                cells = []
                for cell in row.get("cells") or []:
                    tag = "th" if cell.get("header") else "td"
                    attrs = []
                    if int(cell.get("colspan") or 1) > 1:
                        attrs.append(f'colspan="{int(cell.get("colspan"))}"')
                    if int(cell.get("rowspan") or 1) > 1:
                        attrs.append(f'rowspan="{int(cell.get("rowspan"))}"')
                    style = cell.get("style") or {}
                    css = ";".join(f"{k}: {v}" for k, v in style.items() if k in {"color", "background-color"} and v)
                    if css:
                        attrs.append(f'style="{html.escape(css + ";", quote=True)}"')
                    attr_text = (" " + " ".join(attrs)) if attrs else ""
                    cells.append(f"<{tag}{attr_text}>" + _blocks_html(cell.get("blocks") or [], confluence=confluence, asset_prefix=asset_prefix) + f"</{tag}>")
                rows.append("<tr>" + "".join(cells) + "</tr>")
            out.append("<table><tbody>" + "".join(rows) + "</tbody></table>")
        elif kind == "blockquote":
            out.append("<blockquote>" + _blocks_html(block.get("blocks") or [], confluence=confluence, asset_prefix=asset_prefix) + "</blockquote>")
        elif kind == "code_block":
            out.append("<pre><code>" + html.escape(str(block.get("text") or "")) + "</code></pre>")
        elif kind == "rule":
            out.append("<hr/>")
        elif kind == "diagram":
            mermaid = str(block.get("mermaid") or "").strip()
            if mermaid:
                out.append("```mermaid\n" + mermaid + "\n```" if confluence else '<pre class="mermaid">' + html.escape(mermaid) + "</pre>")
            elif block.get("fallback_asset"):
                out.append(f'<p><img src="{html.escape(_asset_src(str(block.get("fallback_asset")), asset_prefix), quote=True)}" alt="diagram"/></p>')
    return "\n".join(out)


def render_html(doc: dict[str, Any]) -> str:
    title = html.escape(str(doc.get("title") or "document"))
    body = _blocks_html(doc.get("blocks") or [], confluence=False, asset_prefix="")
    return (
        "<!doctype html>\n<html><head><meta charset=\"utf-8\"/>"
        f"<title>{title}</title>"
        "<style>body{font-family:Arial,sans-serif;max-width:1200px;margin:2rem auto;padding:0 1rem;}"
        "table{border-collapse:collapse;width:100%;margin:1rem 0;}td,th{border:1px solid #ccc;padding:.4rem;vertical-align:top;}"
        "img{max-width:100%;height:auto;}code,pre{font-family:Consolas,monospace;}</style></head><body>\n"
        + body + "\n</body></html>\n"
    )


def render_confluence(doc: dict[str, Any], *, title: str | None = None, asset_prefix: str = "") -> str:
    page_title = str(title or doc.get("title") or "document").replace("\\", "\\\\").replace('"', '\\"')
    body = _blocks_html(doc.get("blocks") or [], confluence=True, asset_prefix=asset_prefix)
    return f'---\ntitle: "{page_title}"\n---\n\n{body.strip()}\n'


def render_rag(doc: dict[str, Any], profile: dict[str, Any] | None = None) -> str:
    profile = profile or {}
    # Work on block semantics rather than a flattened source. Header/footer/decorative
    # images and TOC navigation are omitted before Markdown generation.
    cleaned = {**doc, "blocks": []}
    seen_images: set[str] = set()
    for block in doc.get("blocks") or []:
        if block.get("type") == "toc":
            continue
        if block.get("type") == "image":
            role = str(block.get("role") or "")
            if role == "header" and not profile.get("include_header_images", False):
                continue
            if role == "footer" and not profile.get("include_footer_images", False):
                continue
            if role == "decorative":
                continue
            src = str(block.get("src") or "")
            if profile.get("collapse_repeated_images", True) and src and src in seen_images:
                continue
            if src:
                seen_images.add(src)
            if not profile.get("keep_image_references", True):
                continue
        cleaned["blocks"].append(block)
    return render_markdown(cleaned, rag=True)

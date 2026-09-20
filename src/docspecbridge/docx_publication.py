from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import tempfile
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unicodedata import normalize as unicode_normalize

from lxml import etree, html


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}
W = f"{{{W_NS}}}"
TOC_MARKER = "DOCSPECBRIDGE_TOC_MARKER"

# Word highlight names supported by Mammoth.  Keep the actual Word colour rather
# than mapping every highlight to a generic Confluence yellow.
HIGHLIGHT_RGB: dict[str, tuple[int, int, int]] = {
    "black": (0, 0, 0),
    "blue": (0, 0, 255),
    "cyan": (0, 255, 255),
    "green": (0, 255, 0),
    "magenta": (255, 0, 255),
    "red": (255, 0, 0),
    "yellow": (255, 255, 0),
    "white": (255, 255, 255),
    "darkBlue": (0, 0, 128),
    "darkCyan": (0, 128, 128),
    "darkGreen": (0, 128, 0),
    "darkMagenta": (128, 0, 128),
    "darkRed": (128, 0, 0),
    "darkYellow": (128, 128, 0),
    "darkGray": (128, 128, 128),
    "lightGray": (192, 192, 192),
}

TOC_NAMES = {
    "sommaire",
    "table des matieres",
    "table des matières",
    "table of contents",
    "contents",
    "inhalt",
    "inhaltsverzeichnis",
    "indice",
    "índice",
    "tabla de contenido",
    "tabla de contenidos",
    "目录",
    "目次",
}


@dataclass
class DocxPublicationResult:
    content: str
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    added_assets: list[dict[str, Any]] = field(default_factory=list)


def _norm(text: str) -> str:
    value = unicode_normalize("NFKD", text or "")
    value = "".join(ch for ch in value if not ("\u0300" <= ch <= "\u036f"))
    return re.sub(r"\s+", " ", value).strip().lower()


def _docx_heading_style_rules(source: Path) -> list[str]:
    """Map every Word paragraph style carrying outlineLvl to a semantic heading.

    Mammoth's built-in map knows the conventional Heading 1..6 styles but not
    arbitrary/template styles such as Word's Title style.  DOCX templates often
    use those styles for real outline entries, so derive the mapping from OOXML.
    """
    try:
        with zipfile.ZipFile(source) as archive:
            root = etree.fromstring(archive.read("word/styles.xml"))
    except Exception:
        return []

    rules: list[str] = []
    seen: set[tuple[str, int]] = set()
    for style in root.findall(f".//{W}style"):
        if style.get(f"{W}type") != "paragraph":
            continue
        p_pr = style.find(f"{W}pPr")
        outline = p_pr.find(f"{W}outlineLvl") if p_pr is not None else None
        if outline is None:
            continue
        try:
            level = int(outline.get(f"{W}val") or 0) + 1
        except ValueError:
            continue
        if not 1 <= level <= 6:
            continue
        name_el = style.find(f"{W}name")
        style_name = str(name_el.get(f"{W}val") if name_el is not None else "").strip()
        if not style_name:
            continue
        # Word's TOC Heading is navigation, not a body heading to map here.
        if "toc" in _norm(style_name) and "heading" in _norm(style_name):
            continue
        key = (style_name, level)
        if key in seen:
            continue
        seen.add(key)
        escaped = style_name.replace("\\", "\\\\").replace("'", "\\'")
        rules.append(f"p[style-name='{escaped}'] => h{level}:fresh")
    return rules


def _style_map(source: Path, preserve_highlight_colors: bool = True) -> str:
    # Mammoth already emits strong/em/s/del semantics. Underline is ignored by
    # default, so opt in explicitly. Highlight colours are mapped to CSS because
    # md2conf supports background-color on spans and relays valid XHTML.
    rules = ["u => u", *_docx_heading_style_rules(source)]
    if preserve_highlight_colors:
        for name, (r, g, b) in HIGHLIGHT_RGB.items():
            rules.append(
                f"highlight[color='{name}'] => span[style='background-color: rgb({r},{g},{b});']"
            )
    else:
        rules.append("highlight => mark")
    return "\n".join(rules)




def _has_toc_field(root: etree._Element) -> bool:
    for node in root.findall(f".//{W}instrText"):
        if re.search(r"(?:^|\s)TOC(?:\s|$)", str(node.text or ""), flags=re.I):
            return True
    return False


def _is_toc_sdt(node: etree._Element) -> bool:
    for gallery in node.findall(f".//{W}docPartGallery"):
        if "table of contents" in str(gallery.get(f"{W}val") or "").casefold():
            return True
    for instr in node.findall(f".//{W}instrText"):
        if re.search(r"(?:^|\s)TOC(?:\s|$)", str(instr.text or ""), flags=re.I):
            return True
    return False


def _marker_paragraph() -> etree._Element:
    paragraph = etree.Element(f"{W}p")
    run = etree.SubElement(paragraph, f"{W}r")
    text = etree.SubElement(run, f"{W}t")
    text.text = TOC_MARKER
    return paragraph


def _strip_floating_textbox_flow(root: etree._Element) -> list[str]:
    """Remove text that exists only inside floating Word text boxes.

    Mammoth cannot preserve DrawingML/VML geometry. Without this guard it flattens
    labels positioned over screenshots (e.g. 2.1, 2.2, arrows/callouts) into normal
    body paragraphs. That changes meaning. Keep the original OOXML as source of truth
    and suppress only paragraphs whose *entire* textual content belongs to a textbox.
    """
    removed: list[str] = []
    for paragraph in root.findall(f".//{W}p"):
        text_nodes = paragraph.findall(f".//{W}t")
        if not text_nodes:
            continue
        textbox_nodes: list[etree._Element] = []
        flow_nodes: list[etree._Element] = []
        for node in text_nodes:
            if node.xpath("ancestor::w:txbxContent", namespaces=NS):
                textbox_nodes.append(node)
            else:
                flow_nodes.append(node)
        textbox_text = "".join(node.text or "" for node in textbox_nodes).strip()
        flow_text = "".join(node.text or "" for node in flow_nodes).strip()
        if textbox_text and not flow_text:
            removed.append(textbox_text)
            for node in textbox_nodes:
                node.text = ""
    return removed


def _prepare_docx_for_mammoth(source: Path, *, suppress_floating_textboxes: bool = True) -> tuple[Path, bool, list[str]]:
    """Create a temporary DOCX view that removes generated TOC results and unsafe flow flattening."""
    toc_detected = False
    suppressed: list[str] = []
    fd, tmp_name = tempfile.mkstemp(prefix="docspecbridge-", suffix=".docx")
    os.close(fd)
    Path(tmp_name).unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(source, "r") as src, zipfile.ZipFile(tmp_name, "w", zipfile.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                payload = src.read(item.filename)
                if item.filename == "word/document.xml":
                    root = etree.fromstring(payload)
                    toc_detected = _has_toc_field(root)
                    # Generated Word TOCs are commonly wrapped in an SDT. Replace the
                    # result set (which contains obsolete page numbers) with one marker.
                    for sdt in list(root.findall(f".//{W}sdt")):
                        if not _is_toc_sdt(sdt):
                            continue
                        parent = sdt.getparent()
                        if parent is not None:
                            parent.replace(sdt, _marker_paragraph())
                            toc_detected = True
                    if suppress_floating_textboxes:
                        suppressed = _strip_floating_textbox_flow(root)
                    payload = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")
                dst.writestr(item, payload)
        return Path(tmp_name), toc_detected, suppressed
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _numbering_data(archive: zipfile.ZipFile) -> tuple[dict[str, str], dict[str, dict[int, dict[str, Any]]]]:
    try:
        root = etree.fromstring(archive.read("word/numbering.xml"))
    except Exception:
        return {}, {}
    num_to_abstract: dict[str, str] = {}
    for num in root.findall(f"{W}num"):
        num_id = str(num.get(f"{W}numId") or "")
        abstract = num.find(f"{W}abstractNumId")
        if num_id and abstract is not None:
            num_to_abstract[num_id] = str(abstract.get(f"{W}val") or "")
    levels: dict[str, dict[int, dict[str, Any]]] = {}
    for abstract in root.findall(f"{W}abstractNum"):
        abstract_id = str(abstract.get(f"{W}abstractNumId") or "")
        data: dict[int, dict[str, Any]] = {}
        for level in abstract.findall(f"{W}lvl"):
            try:
                ilvl = int(level.get(f"{W}ilvl") or 0)
            except ValueError:
                continue
            start = level.find(f"{W}start")
            fmt = level.find(f"{W}numFmt")
            text = level.find(f"{W}lvlText")
            data[ilvl] = {
                "start": int(start.get(f"{W}val") or 1) if start is not None else 1,
                "format": str(fmt.get(f"{W}val") or "decimal") if fmt is not None else "decimal",
                "text": str(text.get(f"{W}val") or f"%{ilvl + 1}.") if text is not None else f"%{ilvl + 1}.",
            }
        levels[abstract_id] = data
    return num_to_abstract, levels


def _roman(value: int) -> str:
    pairs = [(1000,"M"),(900,"CM"),(500,"D"),(400,"CD"),(100,"C"),(90,"XC"),(50,"L"),(40,"XL"),(10,"X"),(9,"IX"),(5,"V"),(4,"IV"),(1,"I")]
    out = []
    n = max(1, value)
    for number, token in pairs:
        while n >= number:
            out.append(token); n -= number
    return "".join(out)


def _format_num(value: int, fmt: str) -> str:
    if fmt == "upperRoman":
        return _roman(value)
    if fmt == "lowerRoman":
        return _roman(value).lower()
    if fmt in {"upperLetter", "lowerLetter"}:
        n = max(1, value); chars = []
        while n:
            n, rem = divmod(n - 1, 26); chars.append(chr(ord("A") + rem))
        text = "".join(reversed(chars))
        return text.lower() if fmt == "lowerLetter" else text
    return str(value)


def _source_heading_specs(source: Path) -> list[dict[str, Any]]:
    """Extract semantic headings and visible Word numbering directly from OOXML."""
    try:
        with zipfile.ZipFile(source) as archive:
            document = etree.fromstring(archive.read("word/document.xml"))
            styles = etree.fromstring(archive.read("word/styles.xml"))
            num_to_abstract, abstract_levels = _numbering_data(archive)
    except Exception:
        return []

    style_data: dict[str, dict[str, Any]] = {}
    for style in styles.findall(f".//{W}style"):
        if style.get(f"{W}type") != "paragraph":
            continue
        sid = str(style.get(f"{W}styleId") or "")
        ppr = style.find(f"{W}pPr")
        outline = ppr.find(f"{W}outlineLvl") if ppr is not None else None
        numpr = ppr.find(f"{W}numPr") if ppr is not None else None
        num_id_el = numpr.find(f"{W}numId") if numpr is not None else None
        ilvl_el = numpr.find(f"{W}ilvl") if numpr is not None else None
        style_data[sid] = {
            "outline": int(outline.get(f"{W}val") or 0) if outline is not None else None,
            "num_id": str(num_id_el.get(f"{W}val") or "") if num_id_el is not None else None,
            "ilvl": int(ilvl_el.get(f"{W}val") or 0) if ilvl_el is not None else None,
        }

    counters: dict[str, dict[int, int]] = defaultdict(dict)
    specs: list[dict[str, Any]] = []
    for paragraph in document.findall(f".//{W}body/{W}p"):
        # Skip field-result paragraphs inside an SDT (TOC etc.).
        if paragraph.xpath("ancestor::w:sdt", namespaces=NS):
            continue
        ppr = paragraph.find(f"{W}pPr")
        style_el = ppr.find(f"{W}pStyle") if ppr is not None else None
        sid = str(style_el.get(f"{W}val") or "") if style_el is not None else ""
        inherited = style_data.get(sid, {})
        outline = inherited.get("outline")
        if outline is None or not 0 <= int(outline) <= 5:
            continue
        title = "".join(paragraph.xpath(".//w:t[not(ancestor::w:txbxContent)]/text()", namespaces=NS)).strip()
        if not title:
            continue
        numpr = ppr.find(f"{W}numPr") if ppr is not None else None
        num_id_el = numpr.find(f"{W}numId") if numpr is not None else None
        ilvl_el = numpr.find(f"{W}ilvl") if numpr is not None else None
        num_id = str(num_id_el.get(f"{W}val") or "") if num_id_el is not None else inherited.get("num_id")
        ilvl = int(ilvl_el.get(f"{W}val") or outline) if ilvl_el is not None else inherited.get("ilvl")
        if ilvl is None:
            ilvl = int(outline)
        prefix = ""
        if num_id and num_id != "0" and num_id in num_to_abstract:
            abs_id = num_to_abstract[num_id]
            levels = abstract_levels.get(abs_id, {})
            level_cfg = levels.get(int(ilvl), {"start": 1, "format": "decimal", "text": f"%{int(ilvl)+1}."})
            current = counters[num_id]
            for deeper in [key for key in list(current) if key > int(ilvl)]:
                current.pop(deeper, None)
            current[int(ilvl)] = current.get(int(ilvl), int(level_cfg.get("start", 1)) - 1) + 1
            # Ensure missing ancestors have their configured start value.
            for ancestor in range(int(ilvl)):
                if ancestor not in current:
                    cfg = levels.get(ancestor, {"start": 1})
                    current[ancestor] = int(cfg.get("start", 1))
            template = str(level_cfg.get("text") or f"%{int(ilvl)+1}.")
            prefix = template
            for number_level in range(9):
                if f"%{number_level + 1}" not in prefix:
                    continue
                cfg = levels.get(number_level, {"format": "decimal"})
                prefix = prefix.replace(
                    f"%{number_level + 1}",
                    _format_num(current.get(number_level, 1), str(cfg.get("format") or "decimal")),
                )
        specs.append({"level": int(outline) + 1, "title": title, "number": prefix.strip()})
    return specs


def _apply_source_heading_numbers(root: etree._Element, source: Path) -> int:
    specs = _source_heading_specs(source)
    if not specs:
        return 0
    applied = 0
    cursor = 0
    headings = root.xpath(".//h1|.//h2|.//h3|.//h4|.//h5|.//h6")
    for heading in headings:
        text = _element_text(heading)
        target = _norm(text)
        match_index = None
        for idx in range(cursor, len(specs)):
            if _norm(specs[idx]["title"]) == target:
                match_index = idx
                break
        if match_index is None:
            continue
        spec = specs[match_index]
        cursor = match_index + 1
        prefix = str(spec.get("number") or "").strip()
        if not prefix:
            continue
        if text.startswith(prefix):
            continue
        heading.text = f"{prefix} " + (heading.text or "")
        applied += 1
    return applied

def _guess_extension(content_type: str) -> str:
    guessed = mimetypes.guess_extension((content_type or "").split(";", 1)[0].strip())
    if guessed == ".jpe":
        return ".jpg"
    return guessed or ".bin"


def _valid_display_size(display: dict[str, Any], profile: dict[str, Any]) -> tuple[int, int] | None:
    try:
        width = int(display.get("width_px") or 0)
        height = int(display.get("height_px") or 0)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    min_px = int(profile.get("min_display_px", 12))
    max_px = int(profile.get("max_display_px", 1800))
    if width < min_px and height < min_px:
        scale = min_px / max(width, height)
        width = max(1, int(round(width * scale)))
        height = max(1, int(round(height * scale)))
    if max(width, height) > max_px:
        scale = max_px / max(width, height)
        width = max(1, int(round(width * scale)))
        height = max(1, int(round(height * scale)))
    return width, height


def _image_converter(package_dir: Path, image_meta: list[dict[str, Any]], profile: dict[str, Any]):
    import mammoth

    by_sha: dict[str, str] = {}
    occurrences: dict[str, list[dict[str, Any]]] = defaultdict(list)
    native: dict[str, tuple[int | None, int | None]] = {}
    for item in image_meta:
        rel = str(item.get("file") or "")
        if not rel:
            continue
        path = package_dir / rel
        digest = str(item.get("sha256") or "")
        if not digest and path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest:
            by_sha.setdefault(digest, rel)
        for occ in item.get("display_occurrences") or []:
            if occ.get("role") == "body":
                occurrences[rel].append(occ)
        native_data = item.get("native") or {}
        native[rel] = (native_data.get("width_px"), native_data.get("height_px"))

    counters: dict[str, int] = defaultdict(int)
    added_assets: list[dict[str, Any]] = []
    created_by_sha: dict[str, str] = {}
    new_counter = 0

    def convert_image(image: Any) -> dict[str, str]:
        nonlocal new_counter
        with image.open() as image_bytes:
            payload = image_bytes.read()
        digest = hashlib.sha256(payload).hexdigest()
        rel = by_sha.get(digest) or created_by_sha.get(digest)
        if not rel:
            new_counter += 1
            ext = _guess_extension(str(getattr(image, "content_type", "") or ""))
            name = f"docx_publication_{new_counter:03d}{ext}"
            target = package_dir / "images" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            rel = str(Path("images") / name).replace("\\", "/")
            created_by_sha[digest] = rel
            added_assets.append(
                {
                    "role": "body",
                    "file": rel,
                    "saved": True,
                    "source_name": name,
                    "sha256": digest,
                    "recovered_by": "mammoth",
                }
            )

        attrs: dict[str, str] = {"src": rel}
        occs = occurrences.get(rel) or []
        if occs and profile.get("preserve_image_display_size", True):
            idx = counters[rel]
            occurrence = occs[min(idx, len(occs) - 1)]
            counters[rel] += 1
            size = _valid_display_size(occurrence.get("display") or {}, profile)
            if size:
                width, height = size
                if bool(profile.get("avoid_upscale", False)):
                    nw, nh = native.get(rel, (None, None))
                    if nw and nh and (width > int(nw) or height > int(nh)):
                        width, height = int(nw), int(nh)
                attrs["width"] = str(width)
                attrs["height"] = str(height)
        return attrs

    return mammoth.images.img_element(convert_image), added_assets


def _hex_color(value: str | None) -> str | None:
    text = str(value or "").strip().lstrip("#")
    if not text or text.lower() in {"auto", "none"}:
        return None
    if re.fullmatch(r"[0-9A-Fa-f]{6}", text):
        return f"#{text.upper()}"
    return None


@dataclass(frozen=True)
class _CellStyle:
    background: str | None = None
    foreground: str | None = None


def _cell_uniform_foreground(cell: etree._Element) -> str | None:
    """Return one explicit run colour when all coloured text in a cell agrees.

    Mammoth intentionally drops arbitrary Word font colours.  Applying a colour at cell
    level is safe only when the DOCX exposes one unambiguous explicit colour.  This is
    especially useful for white labels on dark shaded header cells.
    """
    colours: set[str] = set()
    for run in cell.iter(f"{W}r"):
        text = "".join(node.text or "" for node in run.iter(f"{W}t"))
        if not text.strip():
            continue
        r_pr = run.find(f"{W}rPr")
        if r_pr is None:
            continue
        color = r_pr.find(f"{W}color")
        value = _hex_color(color.get(f"{W}val") if color is not None else None)
        if value:
            colours.add(value)
    return next(iter(colours)) if len(colours) == 1 else None


def _docx_table_cell_styles(source: Path) -> list[list[list[_CellStyle]]]:
    """Return direct Word table-cell styling aligned with Mammoth's emitted cells.

    Vertical-merge continuation cells are skipped because Mammoth represents them via
    rowspan on the restart cell. Horizontal grid spans remain one source cell and are
    represented by colspan.
    """
    with zipfile.ZipFile(source) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))

    tables: list[list[list[_CellStyle]]] = []
    # Keep nested table order consistent with XML document order. Direct-row XPath avoids
    # accidentally pulling rows from a nested table into its parent.
    for table in root.iter(f"{W}tbl"):
        table_rows: list[list[_CellStyle]] = []
        for row in table.findall(f"{W}tr"):
            styles: list[_CellStyle] = []
            for cell in row.findall(f"{W}tc"):
                tc_pr = cell.find(f"{W}tcPr")
                if tc_pr is not None:
                    vmerge = tc_pr.find(f"{W}vMerge")
                    if vmerge is not None:
                        val = vmerge.get(f"{W}val")
                        # Missing val means continuation according to OOXML.
                        if val is None or val == "continue":
                            continue
                    shd = tc_pr.find(f"{W}shd")
                    fill = _hex_color(shd.get(f"{W}fill") if shd is not None else None)
                else:
                    fill = None
                styles.append(_CellStyle(background=fill, foreground=_cell_uniform_foreground(cell)))
            table_rows.append(styles)
        tables.append(table_rows)
    return tables


def _append_css_style(element: etree._Element, declaration: str) -> None:
    current = str(element.get("style") or "").strip()
    if current and not current.endswith(";"):
        current += ";"
    element.set("style", f"{current}{declaration}")


def _apply_table_cell_styles(
    root: etree._Element,
    source: Path,
    *,
    preserve_shading: bool = True,
    preserve_text_color: bool = True,
) -> dict[str, int]:
    try:
        source_tables = _docx_table_cell_styles(source)
    except Exception:
        return {"shading": 0, "text_color": 0}
    counts = {"shading": 0, "text_color": 0}
    html_tables = root.xpath(".//table")
    for table_idx, html_table in enumerate(html_tables):
        if table_idx >= len(source_tables):
            break
        source_rows = source_tables[table_idx]
        html_rows = html_table.xpath("./tr | ./thead/tr | ./tbody/tr | ./tfoot/tr")
        for row_idx, html_row in enumerate(html_rows):
            if row_idx >= len(source_rows):
                break
            styles = source_rows[row_idx]
            html_cells = html_row.xpath("./th | ./td")
            for cell_idx, html_cell in enumerate(html_cells):
                if cell_idx >= len(styles):
                    break
                style = styles[cell_idx]
                if preserve_shading and style.background:
                    _append_css_style(html_cell, f"background-color: {style.background};")
                    counts["shading"] += 1
                if preserve_text_color and style.foreground:
                    _append_css_style(html_cell, f"color: {style.foreground};")
                    counts["text_color"] += 1
    return counts


def _top_level_blocks(fragment: str) -> etree._Element:
    # html.fragment_fromstring is tolerant of the clean HTML Mammoth emits. Serializing
    # each child as XML later makes empty tags self-closing, as required by md2conf/CSF.
    return html.fragment_fromstring(fragment or "", create_parent="div")


def _element_text(element: etree._Element) -> str:
    return " ".join("".join(element.itertext()).split())


def _replace_source_toc(root: etree._Element, enabled: str | bool, toc_detected: bool) -> tuple[bool, int | None]:
    if not toc_detected:
        return False, None
    if enabled is False or str(enabled).lower() in {"false", "off", "no", "0"}:
        want_macro = False
    else:
        want_macro = True

    children = list(root)
    toc_index: int | None = None
    # Preferred path in 0.3: the DOCX preprocessor replaces the complete Word TOC
    # field result by a marker before Mammoth sees it. This guarantees obsolete page
    # numbers and PAGEREF links cannot leak into Confluence.
    for idx, child in enumerate(children):
        if TOC_MARKER in _element_text(child):
            toc_index = idx
            root.remove(child)
            marker = etree.Element("docspecbridge-toc")
            marker.set("enabled", "true" if want_macro else "false")
            root.insert(idx, marker)
            return True, idx

    # Backward-compatible heuristic for malformed/non-SDT TOCs.
    normalized_names = {_norm(name) for name in TOC_NAMES}
    for idx, child in enumerate(children):
        if _norm(_element_text(child)) in normalized_names:
            toc_index = idx
            break
    if toc_index is None:
        return False, None

    end = toc_index + 1
    while end < len(children):
        tag = str(children[end].tag).lower() if isinstance(children[end].tag, str) else ""
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            break
        end += 1
    for child in children[toc_index:end]:
        root.remove(child)
    marker = etree.Element("docspecbridge-toc")
    marker.set("enabled", "true" if want_macro else "false")
    root.insert(toc_index, marker)
    return True, toc_index


def _normalize_mammoth_html(root: etree._Element) -> dict[str, int]:
    """Normalize Mammoth XHTML to constructs md2conf/Confluence accepts safely.

    md2conf traverses normal XHTML nodes (including images nested in tables), but raw
    form checkbox inputs are not a supported Confluence Storage Format primitive.
    Preserve their visual meaning as Unicode instead.  Also strip unsafe URL schemes.
    """
    stats = {"checkboxes": 0, "unsafe_links_removed": 0}
    for input_node in list(root.xpath(".//input[translate(@type,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')='checkbox']")):
        checked = input_node.get("checked") is not None
        replacement = etree.Element("span")
        replacement.text = "☒" if checked else "☐"
        replacement.tail = input_node.tail
        parent = input_node.getparent()
        if parent is not None:
            parent.replace(input_node, replacement)
            stats["checkboxes"] += 1

    for anchor in root.xpath(".//a[@href]"):
        href = str(anchor.get("href") or "").strip()
        scheme = href.split(":", 1)[0].lower() if ":" in href else ""
        if scheme in {"javascript", "data", "vbscript"}:
            anchor.attrib.pop("href", None)
            stats["unsafe_links_removed"] += 1
    return stats


def _serialize_for_md2conf(root: etree._Element) -> str:
    blocks: list[str] = []
    if root.text and root.text.strip():
        blocks.append(root.text.strip())
    for child in list(root):
        if child.tag == "docspecbridge-toc":
            if child.get("enabled") == "true":
                blocks.append("[[_TOC_]]")
        else:
            # method=xml deliberately emits <img .../> and <br/>, which md2conf requires
            # because Confluence Storage Format is XHTML/XML, not permissive HTML.
            blocks.append(etree.tostring(child, encoding="unicode", method="xml", with_tail=False))
        if child.tail and child.tail.strip():
            blocks.append(child.tail.strip())
    return "\n\n".join(block for block in blocks if block.strip()).rstrip() + "\n"


def build_docx_publication(
    source: Path,
    package_dir: Path,
    image_meta: list[dict[str, Any]],
    publication_profile: dict[str, Any],
    *,
    toc_detected: bool,
) -> DocxPublicationResult:
    """Build a DOCX publication view optimized for md2conf/Confluence fidelity.

    Xberg remains the canonical/RAG extractor. For publication, Mammoth emits semantic
    XHTML so structures Markdown cannot express (merged cells) survive, while literal
    asterisks cannot corrupt bold delimiters and Word lists remain real ol/ul elements.
    """
    import mammoth

    docx_cfg = publication_profile.get("docx") or {}
    image_converter, added_assets = _image_converter(package_dir, image_meta, publication_profile)
    style_map = _style_map(source, bool(docx_cfg.get("preserve_highlight_colors", True)))
    warnings: list[str] = []

    prepared_source, ooxml_toc_detected, suppressed_textboxes = _prepare_docx_for_mammoth(
        source,
        suppress_floating_textboxes=bool(docx_cfg.get("suppress_floating_textboxes_in_flow", True)),
    )
    try:
        with prepared_source.open("rb") as handle:
            result = mammoth.convert_to_html(
                handle,
                style_map=style_map,
                include_default_style_map=True,
                include_embedded_style_map=True,
                convert_image=image_converter,
                ignore_empty_paragraphs=True,
                external_file_access=False,
                id_prefix="dsb-",
            )
    finally:
        prepared_source.unlink(missing_ok=True)
    for message in result.messages or []:
        text = str(getattr(message, "message", message))
        kind = str(getattr(message, "type", "warning"))
        warnings.append(f"Mammoth {kind}: {text}")

    root = _top_level_blocks(result.value or "")
    cell_style_stats = _apply_table_cell_styles(
        root,
        source,
        preserve_shading=bool(docx_cfg.get("preserve_cell_shading", True)),
        preserve_text_color=bool(docx_cfg.get("preserve_cell_text_color", True)),
    )
    html_normalization = _normalize_mammoth_html(root)
    heading_numbers_applied = _apply_source_heading_numbers(root, source)

    toc_cfg = publication_profile.get("table_of_contents") or {}
    replace_source_toc = bool(toc_cfg.get("replace_source_toc", True))
    if replace_source_toc:
        toc_replaced, _ = _replace_source_toc(
            root, toc_cfg.get("enabled", "auto"), bool(toc_detected or ooxml_toc_detected)
        )
    else:
        toc_replaced = False
    if (toc_detected or ooxml_toc_detected) and replace_source_toc and not toc_replaced:
        warnings.append(
            "Sommaire DOCX détecté mais non localisé de façon sûre dans la vue Mammoth; le contenu source a été conservé."
        )

    if suppressed_textboxes:
        warnings.append(
            f"{len(suppressed_textboxes)} texte(s) de zone flottante DrawingML/VML exclu(s) du flux linéaire. "
            "Les détails restent disponibles dans canonical JSON / manifest.json et l'OOXML source est conservé."
        )

    content = _serialize_for_md2conf(root)
    return DocxPublicationResult(
        content=content,
        warnings=warnings,
        metadata={
            "engine": "mammoth-html",
            "merged_cells": "preserved-as-colspan-rowspan",
            "list_semantics": "html-ol-ul",
            "literal_markdown_delimiters": "safe-inside-xhtml",
            "highlight_colors": bool(docx_cfg.get("preserve_highlight_colors", True)),
            "cell_shading_applied": cell_style_stats["shading"],
            "cell_text_color_applied": cell_style_stats["text_color"],
            "checkboxes_normalized": html_normalization["checkboxes"],
            "unsafe_links_removed": html_normalization["unsafe_links_removed"],
            "source_toc_detected_ooxml": ooxml_toc_detected,
            "source_toc_replaced": toc_replaced,
            "heading_numbers_applied": heading_numbers_applied,
            "floating_textboxes_suppressed": len(suppressed_textboxes),
        },
        added_assets=added_assets,
    )

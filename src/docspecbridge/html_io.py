from __future__ import annotations

import hashlib
import mimetypes
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from lxml import html

from .canonical import canonical_from_html_document


def _is_url(value: str) -> bool:
    return value.lower().startswith(("http://", "https://"))


def _safe_asset_name(url: str, index: int, content_type: str | None = None) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if not suffix and content_type:
        suffix = mimetypes.guess_extension(content_type.split(";", 1)[0].strip()) or ""
    if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tif", ".tiff"}:
        suffix = ".bin"
    return f"web_{index:03d}{suffix}"


def _download_remote_images(
    html_text: str,
    *,
    base_url: str,
    package_dir: Path,
    timeout: float = 30.0,
    user_agent: str = "DocSpecBridge/0.5.1",
) -> tuple[str, list[dict[str, Any]], list[str]]:
    root = html.fromstring(html_text)
    images_dir = package_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    assets: list[dict[str, Any]] = []
    warnings: list[str] = []
    digest_to_rel: dict[str, str] = {}
    headers = {"User-Agent": user_agent}
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
        for idx, img in enumerate(root.xpath("//img[@src]"), 1):
            src = str(img.get("src") or "").strip()
            if not src or src.startswith(("data:", "cid:")):
                continue
            absolute = urljoin(base_url, src)
            try:
                response = client.get(absolute)
                response.raise_for_status()
                payload = response.content
            except Exception as exc:
                warnings.append(f"HTML image not downloaded: {absolute} ({exc})")
                continue
            digest = hashlib.sha256(payload).hexdigest()
            rel = digest_to_rel.get(digest)
            if rel is None:
                name = _safe_asset_name(absolute, idx, response.headers.get("content-type"))
                target = images_dir / name
                target.write_bytes(payload)
                rel = str(Path("images") / name).replace("\\", "/")
                digest_to_rel[digest] = rel
                assets.append({
                    "role": "body",
                    "file": rel,
                    "saved": True,
                    "sha256": digest,
                    "source_url": absolute,
                    "mime_type": response.headers.get("content-type"),
                })
            img.set("src", rel)
    return html.tostring(root, encoding="unicode", method="html"), assets, warnings



def _copy_local_images(html_text: str, *, source_dir: Path, package_dir: Path) -> tuple[str, list[dict[str, Any]], list[str]]:
    root = html.fromstring(html_text)
    images_dir = package_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    assets: list[dict[str, Any]] = []
    warnings: list[str] = []
    digest_to_rel: dict[str, str] = {}
    for idx, img in enumerate(root.xpath("//img[@src]"), 1):
        src = str(img.get("src") or "").strip()
        if not src or src.startswith(("data:", "http://", "https://", "//")):
            continue
        candidate = (source_dir / src).resolve()
        if not candidate.is_file():
            warnings.append(f"HTML image not found: {src}")
            continue
        payload = candidate.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        rel = digest_to_rel.get(digest)
        if rel is None:
            suffix = candidate.suffix.lower() or ".bin"
            name = f"html_{idx:03d}{suffix}"
            target = images_dir / name
            target.write_bytes(payload)
            rel = str(Path("images") / name).replace("\\", "/")
            digest_to_rel[digest] = rel
            assets.append({"role": "body", "file": rel, "saved": True, "sha256": digest, "source_name": str(candidate)})
        img.set("src", rel)
    return html.tostring(root, encoding="unicode", method="html"), assets, warnings

def canonical_from_html_source(
    source: str | Path,
    *,
    package_dir: Path | None = None,
    fetch_config: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str], str | None]:
    cfg = fetch_config or {}
    warnings: list[str] = []
    assets: list[dict[str, Any]] = []
    source_value = str(source)
    source_url: str | None = None
    if _is_url(source_value):
        source_url = source_value
        headers = {"User-Agent": str(cfg.get("user_agent") or "DocSpecBridge/0.5.1")}
        with httpx.Client(timeout=float(cfg.get("timeout_seconds") or 30), follow_redirects=True, headers=headers) as client:
            response = client.get(source_url)
            response.raise_for_status()
            text = response.text
            final_url = str(response.url)
        if package_dir is not None:
            package_dir.mkdir(parents=True, exist_ok=True)
            # Preserve the server-returned HTML before any asset rewriting so a web
            # source remains auditable/re-processable just like an Office source file.
            (package_dir / "source.html").write_text(text, encoding="utf-8")
        if package_dir is not None and bool(cfg.get("download_images", True)):
            text, assets, image_warnings = _download_remote_images(
                text,
                base_url=final_url,
                package_dir=package_dir,
                timeout=float(cfg.get("timeout_seconds") or 30),
                user_agent=str(cfg.get("user_agent") or "DocSpecBridge/0.5.1"),
            )
            warnings.extend(image_warnings)
        source_meta = {"type": "html", "url": final_url, "packaged_file": "source.html" if package_dir is not None else None}
        title = None
    else:
        path = Path(source).resolve()
        text = path.read_text(encoding="utf-8", errors="replace")
        if package_dir is not None and bool(cfg.get("copy_local_images", True)):
            text, assets, local_warnings = _copy_local_images(text, source_dir=path.parent, package_dir=package_dir)
            warnings.extend(local_warnings)
        source_meta = {"type": "html", "original_path": str(path), "extension": path.suffix.lower()}
        title = None
    doc = canonical_from_html_document(
        text,
        title=title,
        source=source_meta,
        assets=assets,
        prefer_main=bool(cfg.get("prefer_main", True)),
    )
    if not _is_url(source_value) and str((doc.get("metadata") or {}).get("title_source")) == "fallback":
        doc["title"] = path.stem
        doc.setdefault("metadata", {}).update({
            "title": path.stem,
            "title_source": "filename",
            "filename_title": path.stem,
        })
    return doc, warnings, source_url


def _md_inline(text: str) -> list[dict[str, Any]]:
    """Conservative Markdown inline parser for DocSpecBridge-generated/human Markdown.

    It intentionally covers the portable subset used by CanonicalDocument: links,
    images, bold, emphasis, code and escaped punctuation. Unknown syntax is kept as text.
    """
    token_re = re.compile(r"(!?\[[^\]]*\]\([^\)]+\)|\*\*[^\n]+?\*\*|`[^`]+`|\*[^\n*]+?\*)")
    out: list[dict[str, Any]] = []
    pos = 0
    def push(value: str, marks: list[dict[str, Any]] | None = None) -> None:
        if not value:
            return
        value = re.sub(r"\\([\\*_`\[\]])", r"\1", value)
        out.append({"type": "text", "text": value, "marks": marks or []})
    for match in token_re.finditer(text):
        push(text[pos:match.start()])
        token = match.group(0)
        if token.startswith("!["):
            m = re.match(r"!\[([^\]]*)\]\(([^\)]+)\)", token)
            if m:
                out.append({"type": "image", "alt": m.group(1), "src": m.group(2)})
            else:
                push(token)
        elif token.startswith("["):
            m = re.match(r"\[([^\]]*)\]\(([^\)]+)\)", token)
            if m:
                push(m.group(1), [{"type": "link", "href": m.group(2)}])
            else:
                push(token)
        elif token.startswith("**"):
            push(token[2:-2], [{"type": "strong"}])
        elif token.startswith("`"):
            push(token[1:-1], [{"type": "code"}])
        elif token.startswith("*"):
            push(token[1:-1], [{"type": "em"}])
        pos = match.end()
    push(text[pos:])
    return out


def canonical_from_markdown(markdown: str, *, title: str, source: dict[str, Any] | None = None) -> dict[str, Any]:
    from .canonical import new_document, validate_document

    lines = (markdown or "").splitlines()
    doc = new_document(title=title, source=source)
    blocks: list[dict[str, Any]] = []
    i = 0
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            text = " ".join(part.strip() for part in paragraph).strip()
            if text:
                blocks.append({"type": "paragraph", "inlines": _md_inline(text)})
            paragraph = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush_paragraph(); i += 1; continue
        if stripped == "[[_TOC_]]":
            flush_paragraph(); blocks.append({"type": "toc"}); i += 1; continue
        hm = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if hm:
            flush_paragraph(); blocks.append({"type": "heading", "level": len(hm.group(1)), "inlines": _md_inline(hm.group(2))}); i += 1; continue
        if stripped.startswith("```"):
            flush_paragraph(); lang = stripped[3:].strip(); content=[]; i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                content.append(lines[i]); i += 1
            if i < len(lines): i += 1
            blocks.append({"type": "code_block", "language": lang, "text": "\n".join(content)}); continue
        # GFM pipe table: header row + separator.
        if stripped.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{3,}", lines[i+1]):
            flush_paragraph(); table_lines=[line]; i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                table_lines.append(lines[i]); i += 1
            rows=[]
            for ridx, row_text in enumerate(table_lines):
                parts=[p.strip() for p in row_text.strip().strip("|").split("|")]
                cells=[{"type":"table_cell","header":ridx==0,"colspan":1,"rowspan":1,"style":{},"blocks":[{"type":"paragraph","inlines":_md_inline(p.replace("\\|","|"))}]} for p in parts]
                rows.append({"type":"table_row","cells":cells})
            blocks.append({"type":"table","rows":rows}); continue
        lm = re.match(r"^(\s*)([-+*]|\d+[.)])\s+(.+)$", line)
        if lm:
            flush_paragraph(); ordered = lm.group(2)[0].isdigit(); items=[]; start_num=int(re.match(r"\d+",lm.group(2)).group()) if ordered else 1
            indent=len(lm.group(1));
            while i < len(lines):
                m = re.match(r"^(\s*)([-+*]|\d+[.)])\s+(.+)$", lines[i])
                if not m or len(m.group(1)) != indent or (m.group(2)[0].isdigit()) != ordered: break
                items.append({"blocks":[{"type":"paragraph","inlines":_md_inline(m.group(3))}]}); i += 1
            blocks.append({"type":"list","ordered":ordered,"start":start_num,"items":items}); continue
        if stripped in {"---", "***", "___"}:
            flush_paragraph(); blocks.append({"type":"rule"}); i += 1; continue
        paragraph.append(line); i += 1
    flush_paragraph()
    doc["blocks"] = blocks
    validate_document(doc)
    return doc


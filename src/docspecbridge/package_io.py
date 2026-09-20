from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import __version__
from .canonical import apply_asset_display_geometry
from .geometry import build_publication_variants
from .rag import chunk_markdown, write_chunks_jsonl
from .renderers import render_confluence, render_html, render_markdown, render_rag
from .utils import safe_stem, write_json


def read_manifest(package_dir: Path) -> dict[str, Any]:
    """Read a DocSpecBridge manifest, returning an empty mapping for a loose Markdown file."""
    path = package_dir / "manifest.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def resolve_package_output(package_dir: Path, key: str, *legacy_names: str) -> Path | None:
    """Resolve an output via manifest first, then 0.4.x legacy names.

    This is the central backwards-compatibility shim for packages produced before
    0.5.0, when files were named document.* / render_document.md.
    """
    outputs = (read_manifest(package_dir).get("outputs") or {})
    value = outputs.get(key)
    if value:
        candidate = package_dir / str(value)
        if candidate.is_file():
            return candidate
    for name in legacy_names:
        candidate = package_dir / name
        if candidate.is_file():
            return candidate
    return None


def write_canonical_package(
    doc: dict[str, Any],
    package_dir: Path,
    *,
    stem: str | None = None,
    rag_profile: dict[str, Any] | None = None,
    publication_profile: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    source_sha256: str | None = None,
    extra_manifest: dict[str, Any] | None = None,
) -> dict[str, Path | None]:
    package_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_stem(stem or str(doc.get("title") or "document"))
    apply_asset_display_geometry(doc)
    rag_profile = rag_profile or {}
    publication_profile = publication_profile or {}
    warnings = list(warnings or [])

    # 0.5.0: human-facing artefacts carry the source stem. Generic state/index files
    # (manifest/chunks/rag descriptor/publication state) intentionally remain stable.
    document_json = package_dir / f"{stem}.json"
    write_json(document_json, doc)

    human_md = package_dir / f"{stem}.md"
    human_text = render_markdown(doc, rag=False)
    markdown_image_re = re.compile(r"(?P<escaped>\\?)!\[(?P<alt>[^\]]*)\]\((?P<target>[^)]+)\)")
    human_text, publication_variants, variant_warnings = build_publication_variants(
        human_text, package_dir, list(doc.get("assets") or []), publication_profile, markdown_image_re
    )
    warnings.extend(variant_warnings)
    human_md.write_text(human_text, encoding="utf-8")

    html_path = package_dir / f"{stem}.html"
    html_path.write_text(render_html(doc), encoding="utf-8")

    confluence_md = package_dir / f"{stem}.confluence.md"
    confluence_md.write_text(
        render_confluence(doc, title=str(doc.get("title") or stem), asset_prefix=""),
        encoding="utf-8",
    )

    rag_md: Path | None = None
    chunks_path: Path | None = None
    chunks: list[dict[str, Any]] = []
    if rag_profile.get("enabled", True):
        rag_text = render_rag(doc, rag_profile)
        rag_md = package_dir / f"{stem}.rag.md"
        rag_md.write_text(rag_text, encoding="utf-8")
        chunk_cfg = rag_profile.get("chunking") or {}
        if chunk_cfg.get("enabled", True):
            chunks = chunk_markdown(
                rag_text,
                max_characters=int(chunk_cfg.get("max_characters", 1600)),
                overlap=int(chunk_cfg.get("overlap", 150)),
                prepend_heading_context=bool(chunk_cfg.get("prepend_heading_context", True)),
            )
            chunks_path = package_dir / "chunks.jsonl"
            source = dict(doc.get("source") or {})
            if source_sha256:
                source["sha256"] = source_sha256
            write_chunks_jsonl(chunks_path, chunks, source)

    manifest = {
        "schema_version": "0.5",
        "docspecbridge_version": __version__,
        "canonical_schema_version": doc.get("schema_version"),
        "source": doc.get("source") or {},
        "outputs": {
            "human_markdown": human_md.name,
            "publication_markdown": confluence_md.name,
            "html": html_path.name,
            "rag_markdown": rag_md.name if rag_md else None,
            "chunks": chunks_path.name if chunks_path else None,
            "document_json": document_json.name,
            "manifest": "manifest.json",
        },
        "assets": {
            "images": doc.get("assets") or [],
            "publication_variants": publication_variants,
        },
        "profiles": {"publication": publication_profile, "rag": rag_profile},
        "warnings": warnings + list((doc.get("diagnostics") or {}).get("warnings") or []),
        "rag_ready": bool(rag_md),
    }
    if source_sha256:
        manifest["source"] = {**manifest["source"], "sha256": source_sha256}
    if extra_manifest:
        manifest.update(extra_manifest)
    write_json(package_dir / "manifest.json", manifest)

    if rag_md and rag_profile.get("write_descriptor", True):
        write_json(package_dir / "rag.json", {
            "source": manifest["source"],
            "markdown": rag_md.name,
            "document": document_json.name,
            "manifest": "manifest.json",
            "chunks": chunks_path.name if chunks_path else None,
            "chunk_count": len(chunks),
            "canonical": True,
            "notes": "RAG Markdown is rendered directly from CanonicalDocument; merged-cell coverage never duplicates cell content.",
        })

    return {
        "document_json": document_json,
        "human_markdown": human_md,
        "html": html_path,
        "confluence_markdown": confluence_md,
        "rag_markdown": rag_md,
        "chunks": chunks_path,
    }

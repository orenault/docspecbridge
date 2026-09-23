from __future__ import annotations

import asyncio
import base64
import json
import binascii
import mimetypes
import re
import shutil
import hashlib
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xberg import ExtractInput, extract

from . import __version__
from .config import SUPPORTED_SOURCE_EXTENSIONS
from .geometry import build_publication_variants, collect_image_geometry
from .docx_publication import build_docx_publication
from .ooxml import inspect_ooxml, read_ooxml_media
from .outline import apply_outline, extract_outline, publication_toc, rag_without_toc
from .rag import build_rag_markdown, chunk_markdown, write_chunks_jsonl
from .utils import json_safe, safe_stem, sha256_file, write_json
from .canonical import canonical_from_xhtml
from .html_io import canonical_from_html_source, canonical_from_markdown
from .i18n import tr
from .package_io import write_canonical_package
from .title_detection import apply_detected_title
from .xlsx_io import extract_xlsx_to_package


# Xberg sometimes escapes image markers as `\![...](...)`, notably for PPTX.
# Capture the optional escape so a successfully normalized image becomes valid Markdown.
IMAGE_RE = re.compile(r"(?P<escaped>\\?)!\[(?P<alt>[^\]]*)\]\((?P<target>[^)]+)\)")
EMBEDDED_RE = re.compile(r"^embedded:(?P<token>.+)$")
XBERG_LOCAL_IMAGE_RE = re.compile(r"^(?:\./)?(?:.*/)?image_(?P<index>\d+)(?:\.[A-Za-z0-9]+)?$")
HTML_IMAGE_RE = re.compile(r"<img\b[^>]*?\bsrc=[\"\'](?P<target>[^\"\']+)[\"\'][^>]*/?>", re.I)


def _publication_front_matter(markdown: str, title: str) -> str:
    """Add deterministic YAML front matter for md2conf page title selection."""
    escaped = title.replace("\\", "\\\\").replace('"', '\"')
    return f'---\ntitle: "{escaped}"\n---\n\n' + markdown.lstrip()


@dataclass
class ExtractionOutcome:
    source: Path
    package_dir: Path
    markdown: Path | None = None
    images: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


class XbergExtractor:
    def __init__(self, config: dict[str, Any]):
        self.config = config

    def discover(self, source: Path) -> tuple[list[Path], Path]:
        app = self.config["app"]
        supported = set(SUPPORTED_SOURCE_EXTENSIONS)
        requested = ((self.config.get("_runtime") or {}).get("extensions") or [])
        if requested:
            extensions = {str(ext).strip().lower() for ext in requested if str(ext).strip()}
            extensions = {ext if ext.startswith(".") else f".{ext}" for ext in extensions}
            unsupported = sorted(extensions - supported)
            if unsupported:
                raise ValueError(f"Unsupported source extension filter: {', '.join(unsupported)}")
        else:
            extensions = supported
        if source.is_file():
            if source.suffix.lower() not in extensions:
                raise ValueError(f"Unsupported source format: {source.suffix}")
            return [source.resolve()], source.parent.resolve()
        if not source.is_dir():
            raise FileNotFoundError(source)
        pattern = "**/*" if app.get("recursive", True) else "*"
        files = sorted(p.resolve() for p in source.glob(pattern) if p.is_file() and p.suffix.lower() in extensions)
        return files, source.resolve()

    async def extract_source(self, source: Path, destination: Path) -> list[ExtractionOutcome]:
        files, source_root = self.discover(source)
        outcomes: list[ExtractionOutcome] = []
        for file in files:
            if bool(((self.config.get("_runtime") or {}).get("force_extract", False))):
                outcomes.append(await self._force_extract_one(file, source_root, destination.resolve()))
            else:
                outcomes.append(await self._extract_one(file, source_root, destination.resolve()))
        return outcomes

    async def _force_extract_one(self, source: Path, source_root: Path, destination: Path) -> ExtractionOutcome:
        """Re-extract a package transactionally while preserving publication identity.

        The existing package is first renamed to a sibling backup. Extraction then runs
        into the original package path. Only after a successful extraction are Confluence
        and Jira publication states migrated into the new package and the backup removed.
        On failure the partial package is discarded and the previous package is restored.
        """
        package_dir = self._package_dir(source, source_root, destination)
        if not package_dir.exists() or not any(package_dir.iterdir()):
            return await self._extract_one(source, source_root, destination)

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        backup_dir = package_dir.with_name(f".{package_dir.name}.docspecbridge-force-{stamp}.bak")
        package_dir.rename(backup_dir)
        try:
            outcome = await self._extract_one(source, source_root, destination)
            if outcome.error:
                if package_dir.exists():
                    shutil.rmtree(package_dir, ignore_errors=True)
                backup_dir.rename(package_dir)
                outcome.package_dir = package_dir
                outcome.warnings.append(tr(self.config, "extract.force_rollback"))
                return outcome

            self._restore_publication_states(backup_dir, package_dir)
            shutil.rmtree(backup_dir, ignore_errors=True)
            outcome.warnings.append(tr(self.config, "extract.force_preserved"))
            return outcome
        except Exception:
            if package_dir.exists():
                shutil.rmtree(package_dir, ignore_errors=True)
            if backup_dir.exists():
                backup_dir.rename(package_dir)
            raise

    def _restore_publication_states(self, old_package: Path, new_package: Path) -> None:
        """Restore/migrate durable target identity for the root and nested packages.

        XLSX workbooks can publish child worksheet packages independently, so every
        nested state file must survive a force extraction. If an old state cannot be
        mapped to a regenerated package at the same relative location, fail the whole
        transaction and restore the previous corpus rather than silently losing identity.
        """
        state_paths = sorted(old_package.rglob("publication_state.json"))
        jira_paths = sorted(old_package.rglob("jira_publication_state.json"))

        for cf_old in state_paths:
            rel_dir = cf_old.parent.relative_to(old_package)
            target_package = new_package / rel_dir
            if not target_package.is_dir():
                raise RuntimeError(tr(
                    self.config, "extract.force_missing_nested_package", path=str(rel_dir or Path("."))
                ))
            manifest = target_package / "manifest.json"
            manifest_data: dict[str, Any] = {}
            if manifest.is_file():
                try:
                    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
                except Exception:
                    manifest_data = {}
            publication_name = str((manifest_data.get("outputs") or {}).get("publication_markdown") or "").strip()
            try:
                state = json.loads(cf_old.read_text(encoding="utf-8"))
                if not isinstance(state, dict):
                    raise ValueError("invalid state")
                for row in state.get("confluence") or []:
                    if isinstance(row, dict) and publication_name:
                        row["source"] = Path(publication_name).name
                state["schema_version"] = "1.1"
                state["migrated_by"] = __version__
                state["migrated_at"] = datetime.now(timezone.utc).isoformat()
                write_json(target_package / "publication_state.json", state)
            except Exception as exc:
                raise RuntimeError(tr(
                    self.config, "extract.force_state_error", file=str(cf_old.relative_to(old_package)), error=exc
                )) from exc

        for jira_old in jira_paths:
            rel_dir = jira_old.parent.relative_to(old_package)
            target_package = new_package / rel_dir
            if not target_package.is_dir():
                raise RuntimeError(tr(
                    self.config, "extract.force_missing_nested_package", path=str(rel_dir or Path("."))
                ))
            manifest = target_package / "manifest.json"
            try:
                state = json.loads(jira_old.read_text(encoding="utf-8"))
                if not isinstance(state, dict):
                    raise ValueError("invalid state")
                fingerprint = hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.is_file() else ""
                for row in state.get("publications") or []:
                    if isinstance(row, dict) and fingerprint:
                        row["source_fingerprint"] = fingerprint
                state["schema_version"] = "1.1"
                state["migrated_by"] = __version__
                state["migrated_at"] = datetime.now(timezone.utc).isoformat()
                write_json(target_package / "jira_publication_state.json", state)
            except Exception as exc:
                raise RuntimeError(tr(
                    self.config, "extract.force_state_error", file=str(jira_old.relative_to(old_package)), error=exc
                )) from exc

    def _package_dir(self, source: Path, source_root: Path, destination: Path) -> Path:
        app = self.config["app"]
        relative_parent = Path()
        if app.get("preserve_source_tree", True) and source_root != source.parent:
            try:
                relative_parent = source.parent.relative_to(source_root)
            except ValueError:
                relative_parent = Path()
        # MVP decision: one self-contained package directory per source document.
        return destination / relative_parent / f"{safe_stem(source.stem)}__{source.suffix.lower().lstrip('.') or 'file'}"

    async def _extract_one(self, source: Path, source_root: Path, destination: Path) -> ExtractionOutcome:
        package_dir = self._package_dir(source, source_root, destination)
        outcome = ExtractionOutcome(source=source, package_dir=package_dir)
        app_cfg = self.config["app"]

        try:
            if package_dir.exists() and any(package_dir.iterdir()):
                if not app_cfg.get("overwrite", False):
                    raise FileExistsError(f"Destination non vide (utiliser --overwrite): {package_dir}")
                shutil.rmtree(package_dir)
            package_dir.mkdir(parents=True, exist_ok=True)
            images_dir = package_dir / "images"
            images_dir.mkdir(parents=True, exist_ok=True)

            # Text sources can otherwise collide with generated human outputs
            # (`page.html` -> generated `page.html`, `notes.md` -> generated `notes.md`).
            # Preserve the original under an explicit source name instead.
            if source.suffix.lower() in {".html", ".htm", ".md", ".markdown"}:
                source_copy = package_dir / f"{safe_stem(source.stem)}.source{source.suffix.lower()}"
            else:
                source_copy = package_dir / source.name
            if app_cfg.get("copy_source", True):
                if source.resolve() != source_copy.resolve():
                    shutil.copy2(source, source_copy)

            # XLSX is handled natively to preserve worksheet boundaries, formulas and charts.
            if source.suffix.lower() == ".xlsx":
                result = extract_xlsx_to_package(source, package_dir, self.config)
                outcome.markdown = result["root"]["human_markdown"]
                for image in sorted(package_dir.rglob("images/*")):
                    if image.is_file():
                        outcome.images.append(image)
                return outcome

            # Native structured-text sources do not need Xberg. HTML/Markdown enter
            # the same CanonicalDocument pipeline as Office/PDF, then all target views
            # are rendered from that canonical representation.
            if source.suffix.lower() in {".html", ".htm", ".md", ".markdown"}:
                profiles_cfg = self.config.get("profiles") or {}
                publication_cfg = profiles_cfg.get("publication") or {}
                rag_cfg = profiles_cfg.get("rag") or {}
                if source.suffix.lower() in {".html", ".htm"}:
                    doc, native_warnings, _ = canonical_from_html_source(
                        source,
                        package_dir=package_dir,
                        fetch_config=(self.config.get("html") or {}).get("fetch") or {},
                    )
                    outcome.warnings.extend(native_warnings)
                else:
                    text = source.read_text(encoding="utf-8", errors="replace")
                    doc = canonical_from_markdown(
                        text, title=source.stem,
                        source={
                            "type": "markdown", "original_path": str(source), "extension": source.suffix.lower(),
                            "packaged_file": source_copy.name if app_cfg.get("copy_source", True) else None,
                        },
                    )
                    apply_detected_title(doc, source)
                outputs = write_canonical_package(
                    doc, package_dir, stem=safe_stem(source.stem),
                    rag_profile=rag_cfg, publication_profile=publication_cfg,
                    warnings=outcome.warnings, source_sha256=sha256_file(source),
                    extra_manifest={"engine": "canonical-native"},
                )
                outcome.markdown = outputs["human_markdown"]
                images_dir = package_dir / "images"
                if images_dir.is_dir():
                    outcome.images.extend(sorted(p for p in images_dir.iterdir() if p.is_file()))
                return outcome

            extract_cfg = self.config["extract"]
            xberg_cfg: dict[str, Any] = {
                "use_cache": bool(extract_cfg.get("use_cache", True)),
                "enable_quality_processing": bool(extract_cfg.get("enable_quality_processing", True)),
                "output_format": "markdown",
                "extraction_timeout_secs": int(extract_cfg.get("extraction_timeout_secs", 600)),
                "images": dict(extract_cfg.get("images") or {}),
                "pdf_options": dict(extract_cfg.get("pdf_options") or {}),
            }
            profiles_cfg = self.config.get("profiles") or {}
            publication_cfg = profiles_cfg.get("publication") or {}
            rag_cfg = profiles_cfg.get("rag") or {}
            # RAG chunking is performed by DocSpecBridge on the normalized Markdown.
            # This keeps publication layout metadata completely out of retrieval text.

            result = await extract(ExtractInput(uri=str(source)), config=xberg_cfg)
            if not result.results:
                details = "; ".join(str(getattr(err, "message", err)) for err in (result.errors or []))
                raise RuntimeError(f"Xberg n'a retourné aucun document. {details}")

            document = result.results[0]
            markdown = document.content or ""
            image_objects = list(document.images or [])
            saved, image_meta, index_to_path, dedup_stats = self._save_images(
                image_objects, images_dir, outcome.warnings, deduplicate=bool(extract_cfg.get("deduplicate_assets", True))
            )
            outcome.images.extend(saved)

            diagnostics_cfg = extract_cfg.get("diagnostics") or {}
            ooxml = inspect_ooxml(source) if diagnostics_cfg.get("inspect_ooxml", True) else None
            ooxml_media_map = self._map_ooxml_media_to_saved_paths(source, ooxml, image_objects, index_to_path)
            markdown = self._rewrite_image_references(
                markdown,
                image_objects,
                index_to_path,
                outcome.warnings,
                source_media_map=ooxml_media_map,
            )

            # OOXML extractors may leave ../media/imageNNN.* references even when the
            # original asset is present in the DOCX/PPTX archive. Recover those assets
            # directly from the source package and normalize the links.
            if ooxml and source.suffix.lower() in {".docx", ".pptx"}:
                markdown, recovered_meta = self._recover_ooxml_media_references(
                    markdown,
                    source,
                    ooxml,
                    images_dir,
                    outcome.images,
                )
                image_meta.extend(recovered_meta)

            # Xberg PPTX output can contain formatting artifacts such as a bold opener
            # followed by an isolated closing ** on a later line. Repair only these
            # mechanically recognizable cases; do not attempt stylistic rewriting.
            markdown, formatting_repairs = self._normalize_markdown_artifacts(markdown)
            if formatting_repairs:
                outcome.warnings.append(
                    f"Markdown normalisé: {formatting_repairs} artefact(s) de mise en forme Xberg corrigé(s)."
                )

            # DOCX headers are not part of Xberg's body Markdown. For a web/Confluence
            # document, preserve header artwork once at the top instead of once per page.
            docx_layout_cfg = extract_cfg.get("docx_layout") or {}
            if source.suffix.lower() == ".docx" and ooxml and docx_layout_cfg.get("include_header_images_once", True):
                header_refs, header_meta = self._add_ooxml_assets_once(
                    source,
                    list(ooxml.get("header_media_files") or []),
                    images_dir,
                    outcome.images,
                    role="header",
                )
                if header_refs:
                    markdown = "\n".join(header_refs) + "\n\n" + markdown.lstrip()
                    image_meta.extend(header_meta)

            # PDF extraction naturally sees page headers on every page. Collapse only
            # images repeated many times; this targets logos/watermarks while preserving
            # ordinary figures that happen to appear twice.
            pdf_layout_cfg = extract_cfg.get("pdf_layout") or {}
            collapsed_refs = 0
            if source.suffix.lower() == ".pdf" and pdf_layout_cfg.get("collapse_repeated_images", True):
                markdown, collapsed_refs = self._collapse_repeated_local_image_refs(
                    markdown,
                    min_occurrences=int(pdf_layout_cfg.get("repeat_threshold", 3)),
                )

            # Recover document outline / heading levels from the strongest available
            # source (DOCX styles, PDF bookmarks, PPTX slide titles, then Markdown).
            # A detected source table of contents is replaced by a neutral placeholder:
            # publication turns it into a native Confluence TOC; RAG drops it to avoid
            # indexing duplicate navigation text.
            outline_entries, outline_source = extract_outline(source, markdown)
            toc_cfg = (publication_cfg.get("table_of_contents") or {})
            outline_result = apply_outline(
                markdown,
                outline_entries,
                replace_source_toc=bool(toc_cfg.get("replace_source_toc", True)),
            )
            outcome.warnings.extend(outline_result.warnings)

            # 0.4: the normalized Xberg Markdown is an input signal, not the canonical
            # document. DOCX gets its structural truth from Mammoth+OOXML; other Xberg
            # formats use the normalized Markdown as a conservative adapter input.
            canonical_markdown = outline_result.markdown.rstrip() + "\n"
            stem = safe_stem(source.stem)

            if diagnostics_cfg.get("keep_raw_xberg_markdown", False):
                raw_path = package_dir / f"{stem}.raw.md"
                raw_path.write_text((document.content or "").rstrip() + "\n", encoding="utf-8")
            else:
                raw_path = None

            geometry_occurrences, geometry_stats, geometry_warnings = collect_image_geometry(
                source, image_meta, package_dir
            )
            outcome.warnings.extend(geometry_warnings)

            publication_metadata: dict[str, Any] = {"engine": "canonical-renderer"}
            doc = None
            if publication_cfg.get("enabled", True) and source.suffix.lower() == ".docx":
                docx_pub_cfg = publication_cfg.get("docx") or {}
                use_mammoth = str(docx_pub_cfg.get("engine") or "mammoth-html").lower() in {"mammoth", "mammoth-html", "html"}
                if use_mammoth:
                    try:
                        docx_publication = build_docx_publication(
                            source,
                            package_dir,
                            image_meta,
                            publication_cfg,
                            toc_detected=outline_result.toc_detected,
                        )
                        publication_metadata = docx_publication.metadata
                        outcome.warnings.extend(docx_publication.warnings)
                        if docx_publication.added_assets:
                            image_meta.extend(docx_publication.added_assets)
                            for item in docx_publication.added_assets:
                                rel = str(item.get("file") or "")
                                if rel:
                                    added = package_dir / rel
                                    if added.is_file() and added not in outcome.images:
                                        outcome.images.append(added)
                        doc = canonical_from_xhtml(
                            docx_publication.content,
                            title=source.stem,
                            source={
                                "type": "docx",
                                "original_path": str(source),
                                "packaged_file": source.name if app_cfg.get("copy_source", True) else None,
                                "extension": source.suffix.lower(),
                            },
                            assets=image_meta,
                            header_assets_once=bool(docx_layout_cfg.get("include_header_images_once", True)),
                        )
                    except Exception as exc:
                        outcome.warnings.append(
                            f"Publication DOCX Mammoth indisponible ({exc}); repli canonique sur le Markdown Xberg."
                        )
                        publication_metadata = {"engine": "canonical-xberg-fallback", "fallback_from": "mammoth-html"}

            if doc is None:
                doc = canonical_from_markdown(
                    rag_without_toc(canonical_markdown),
                    title=source.stem,
                    source={
                        "type": source.suffix.lower().lstrip("."),
                        "original_path": str(source),
                        "packaged_file": source.name if app_cfg.get("copy_source", True) else None,
                        "extension": source.suffix.lower(),
                    },
                )
                doc["assets"] = image_meta
                # Preserve source TOC as a semantic marker for target renderers when one
                # was reliably detected, but never preserve obsolete Word/PDF page numbers.
                if outline_result.toc_detected:
                    insert_at = next((i for i, b in enumerate(doc["blocks"]) if b.get("type") == "heading"), 0)
                    doc["blocks"].insert(insert_at, {"type": "toc"})

            # Canonical title is detected once at source-adapter time and reused by
            # Confluence/HTML/Jira. Filename remains a deterministic fallback.
            apply_detected_title(
                doc, source, xberg_metadata=json_safe(getattr(document, "metadata", None))
            )

            if ooxml:
                markers = dict(ooxml.get("markers") or {})
                risky = {
                    "connectors": int(markers.get("connector", 0)),
                    "groups": int(markers.get("group_shape", 0)),
                    "charts": int(markers.get("chart", 0)),
                    "diagrams": int(markers.get("diagram", 0)),
                    "vml_shapes": int(markers.get("vml_shape", 0)),
                }
                if any(risky.values()):
                    details = ", ".join(f"{k}={v}" for k, v in risky.items() if v)
                    outcome.warnings.append(
                        "Contenu graphique/vectoriel OOXML détecté (" + details + "). "
                        "La structure est enregistrée dans le canonique/manifest; le rendu visuel peut rester partiel."
                    )
                    doc["graphics"] = {
                        "vector_detected": True,
                        "counts": risky,
                        "render_status": "partial",
                        "mermaid_status": "not-generated",
                    }

            pdf_vector_count = int((geometry_stats or {}).get("vector_drawings", 0))
            fidelity = self._visual_fidelity(ooxml)
            if pdf_vector_count:
                fidelity = {
                    **fidelity,
                    "status": "partial",
                    "vector_graphics_detected": True,
                    "pdf_vector_drawings": pdf_vector_count,
                }
                doc.setdefault("graphics", {}).update({
                    "vector_detected": True,
                    "pdf_vector_drawings": pdf_vector_count,
                    "render_status": "partial",
                    "mermaid_status": "not-generated",
                })

            outputs = write_canonical_package(
                doc,
                package_dir,
                stem=stem,
                rag_profile=rag_cfg,
                publication_profile=publication_cfg,
                warnings=outcome.warnings,
                source_sha256=sha256_file(source),
                extra_manifest={
                    "engine": "canonical-v1",
                    "xberg": {
                        "mime_type": getattr(document, "mime_type", None),
                        "quality_score": getattr(document, "quality_score", None),
                        "processing_warnings": json_safe(getattr(document, "processing_warnings", [])),
                        "metadata": json_safe(getattr(document, "metadata", None)),
                    },
                    "ooxml_diagnostics": ooxml,
                    "geometry": {"occurrences": geometry_occurrences, "stats": geometry_stats},
                    "outline": {
                        "source": outline_source,
                        "entries": outline_result.outline,
                        "heading_count": len(outline_result.outline),
                        "headings_applied": outline_result.headings_applied,
                        "source_toc_detected": outline_result.toc_detected,
                    },
                    "visual_fidelity": fidelity,
                    "publication": publication_metadata,
                    "image_normalization": {
                        **dedup_stats,
                        "collapsed_repeated_markdown_references": collapsed_refs,
                    },
                },
            )
            outcome.markdown = outputs["human_markdown"]

            if raw_path is not None:
                manifest_path = package_dir / "manifest.json"
                try:
                    import json as _json
                    manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
                    manifest.setdefault("outputs", {})["raw_xberg_markdown"] = raw_path.name
                    write_json(manifest_path, manifest)
                except Exception:
                    pass

            if diagnostics_cfg.get("warn_on_unresolved_images", True):
                confluence_render = outputs.get("confluence_markdown")
                if confluence_render:
                    broken = self._find_broken_local_image_refs(Path(confluence_render).read_text(encoding="utf-8"), Path(confluence_render).parent)
                    if broken:
                        outcome.warnings.append(
                            "Référence(s) image de rendu non résolue(s): " + ", ".join(sorted(set(broken)))
                        )

            if ooxml:
                expected_content_images = int(ooxml.get("content_media_count", ooxml.get("media_count", 0)))
                xberg_payload_count = sum(1 for image in image_objects if self._image_payload(image) is not None)
                if expected_content_images > xberg_payload_count:
                    outcome.warnings.append(
                        f"OOXML référence {expected_content_images} média(s) dans le contenu principal, "
                        f"Xberg en a restitué {xberg_payload_count}. Certaines figures peuvent manquer."
                    )

        except Exception as exc:
            outcome.error = str(exc)
        return outcome

    def _save_images(
        self,
        images: list[Any],
        images_dir: Path,
        warnings: list[str],
        *,
        deduplicate: bool = True,
    ) -> tuple[list[Path], list[dict[str, Any]], dict[int, Path], dict[str, int]]:
        """Persist Xberg images and optionally deduplicate identical binary assets.

        `index_to_path` preserves the relation between each Xberg occurrence and the
        unique physical asset so Markdown references can still be rewritten correctly.
        """
        saved: list[Path] = []
        metadata: list[dict[str, Any]] = []
        index_to_path: dict[int, Path] = {}
        digest_to_path: dict[str, Path] = {}
        duplicate_occurrences = 0

        for original_index, image in enumerate(images):
            display_index = original_index + 1
            payload = self._image_payload(image)
            mime = self._first_attr(image, "mime_type", "media_type", "content_type")
            source_name = self._first_attr(image, "filename", "file_name", "name")
            if payload is None:
                warnings.append(f"Image Xberg #{display_index}: données binaires/base64 absentes.")
                metadata.append({"index": display_index, "saved": False, "xberg": json_safe(image)})
                continue

            digest = hashlib.sha256(payload).hexdigest()
            path = digest_to_path.get(digest) if deduplicate else None
            duplicate_of: str | None = None
            if path is not None:
                duplicate_occurrences += 1
                duplicate_of = str(Path("images") / path.name).replace("\\", "/")
            else:
                ext = self._guess_extension(payload, str(mime or ""), str(source_name or ""))
                path = images_dir / f"image_{len(saved) + 1:03d}{ext}"
                path.write_bytes(payload)
                saved.append(path)
                digest_to_path[digest] = path

            index_to_path[original_index] = path
            metadata.append(
                {
                    "index": display_index,
                    "role": "body",
                    "file": str(Path("images") / path.name).replace("\\", "/"),
                    "saved": True,
                    "duplicate_of": duplicate_of,
                    "sha256": digest,
                    "mime_type": mime,
                    "source_name": source_name,
                    "xberg": json_safe(image),
                }
            )

        return saved, metadata, index_to_path, {
            "xberg_image_occurrences": len(images),
            "unique_assets": len(saved),
            "duplicate_asset_occurrences": duplicate_occurrences,
        }

    def _map_ooxml_media_to_saved_paths(
        self,
        source: Path,
        ooxml: dict[str, Any] | None,
        image_objects: list[Any],
        index_to_path: dict[int, Path],
    ) -> dict[str, Path]:
        """Map OOXML media names (e.g. image132.png) to normalized saved assets.

        PPTX/DOCX extractors can emit Markdown targets such as ``../media/image132.png``
        while the Xberg image objects do not expose that original filename.  The binary
        payload is nevertheless identical to the OOXML media member, so SHA-256 gives us
        a deterministic mapping without relying on extraction order.
        """
        if not ooxml or source.suffix.lower() not in {".docx", ".pptx"}:
            return {}

        digest_to_path: dict[str, Path] = {}
        for idx, image in enumerate(image_objects):
            path = index_to_path.get(idx)
            payload = self._image_payload(image)
            if path is None or payload is None:
                continue
            digest_to_path[hashlib.sha256(payload).hexdigest()] = path

        mapping: dict[str, Path] = {}
        member_names = list(ooxml.get("media_files") or [])
        for member_name, payload in read_ooxml_media(source, member_names):
            path = digest_to_path.get(hashlib.sha256(payload).hexdigest())
            if path is None:
                continue
            normalized = member_name.replace("\\", "/").lower()
            mapping[normalized] = path
            mapping[Path(normalized).name.lower()] = path
        return mapping

    def _recover_ooxml_media_references(
        self,
        markdown: str,
        source: Path,
        ooxml: dict[str, Any],
        images_dir: Path,
        current_assets: list[Path],
    ) -> tuple[str, list[dict[str, Any]]]:
        """Recover unresolved ../media/* links directly from DOCX/PPTX OOXML.

        This is a fallback for Xberg 1.0.x PPTX/DOCX Markdown where the link is emitted
        but the Xberg image object does not retain enough source-name information for the
        first normalization pass.
        """
        media_names = list(ooxml.get("media_files") or [])
        if not media_names:
            return markdown, []

        by_basename = {Path(name).name.lower(): name for name in media_names}
        referenced: dict[str, str] = {}
        for match in IMAGE_RE.finditer(markdown):
            target = match.group("target").strip().replace("\\", "/")
            lowered = target.lower()
            if lowered.startswith(("http://", "https://", "data:", "#", "images/")):
                continue
            basename = Path(target).name.lower()
            if basename in by_basename:
                referenced[lowered] = by_basename[basename]
        if not referenced:
            return markdown, []

        known: dict[str, Path] = {}
        for asset in current_assets:
            if asset.exists():
                known[hashlib.sha256(asset.read_bytes()).hexdigest()] = asset

        target_to_asset: dict[str, Path] = {}
        metadata: list[dict[str, Any]] = []
        needed_members = sorted(set(referenced.values()))
        payloads = dict(read_ooxml_media(source, needed_members))
        for normalized_target, member_name in referenced.items():
            payload = payloads.get(member_name)
            if payload is None:
                continue
            digest = hashlib.sha256(payload).hexdigest()
            path = known.get(digest)
            was_existing = path is not None
            if path is None:
                ext = Path(member_name).suffix.lower() or self._guess_extension(payload, "", member_name)
                path = images_dir / f"image_{len(current_assets) + 1:03d}{ext}"
                path.write_bytes(payload)
                current_assets.append(path)
                known[digest] = path
            target_to_asset[normalized_target] = path
            metadata.append(
                {
                    "role": "recovered_ooxml_media",
                    "file": str(Path("images") / path.name).replace("\\", "/"),
                    "saved": True,
                    "source_name": member_name,
                    "sha256": digest,
                    "duplicate_of_existing_asset": was_existing,
                }
            )

        def replace(match: re.Match[str]) -> str:
            target = match.group("target").strip().replace("\\", "/")
            path = target_to_asset.get(target.lower())
            if path is None:
                return match.group(0)
            alt = match.group("alt") or "Image"
            rel = str(Path("images") / path.name).replace("\\", "/")
            return f"![{alt}]({rel})"

        return IMAGE_RE.sub(replace, markdown), metadata

    @staticmethod
    def _normalize_markdown_artifacts(markdown: str) -> tuple[str, int]:
        """Repair conservative, mechanically identifiable Xberg Markdown artifacts."""
        repairs = 0
        lines = markdown.splitlines()
        out: list[str] = []

        # Xberg PPTX sometimes emits:
        #   **Bold text
        #
        #   **
        # Move an isolated closing marker back to the previous non-empty line only when
        # that line has an unmatched opener of the same kind.
        for line in lines:
            marker = line.strip()
            if marker in {"**", "***"}:
                prev_idx = len(out) - 1
                while prev_idx >= 0 and not out[prev_idx].strip():
                    prev_idx -= 1
                if prev_idx >= 0:
                    prev = out[prev_idx]
                    if prev.count(marker) % 2 == 1:
                        out[prev_idx] = prev.rstrip() + marker
                        repairs += 1
                        continue
            out.append(line)

        text = "\n".join(out)

        # Remove empty bold/strong spans between two adjacent fragments: ** ** or *** ***.
        # These occur frequently when PowerPoint runs are exported separately.
        text, n = re.subn(r"\*\*[ \t]+\*\*", " ", text)
        repairs += n
        text, n = re.subn(r"\*\*\*[ \t]+\*\*\*", " ", text)
        repairs += n
        return text, repairs

    @staticmethod
    def _visual_fidelity(ooxml: dict[str, Any] | None) -> dict[str, Any]:
        if not ooxml:
            return {"status": "unknown", "vector_graphics_detected": False}
        markers = dict(ooxml.get("markers") or {})
        risky = {
            "connectors": int(markers.get("connector", 0)),
            "groups": int(markers.get("group_shape", 0)),
            "charts": int(markers.get("chart", 0)),
            "diagrams": int(markers.get("diagram", 0)),
            "vml_shapes": int(markers.get("vml_shape", 0)),
        }
        detected = any(risky.values())
        return {
            "status": "partial" if detected else "normal",
            "vector_graphics_detected": detected,
            "vector_counts": risky,
        }

    def _add_ooxml_assets_once(
        self,
        source: Path,
        member_names: list[str],
        images_dir: Path,
        current_assets: list[Path],
        *,
        role: str,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """Add DOCX/PPTX media such as header artwork once, deduplicated by SHA-256."""
        refs: list[str] = []
        metadata: list[dict[str, Any]] = []
        known: dict[str, Path] = {}
        for asset in current_assets:
            if asset.exists():
                known[hashlib.sha256(asset.read_bytes()).hexdigest()] = asset

        role_counter = 0
        for member_name, payload in read_ooxml_media(source, member_names):
            digest = hashlib.sha256(payload).hexdigest()
            path = known.get(digest)
            duplicate = path is not None
            if path is None:
                role_counter += 1
                ext = Path(member_name).suffix.lower() or self._guess_extension(payload, "", member_name)
                path = images_dir / f"{role}_{role_counter:03d}{ext}"
                path.write_bytes(payload)
                current_assets.append(path)
                known[digest] = path
            rel = str(Path("images") / path.name).replace("\\", "/")
            if f"![]({rel})" not in refs:
                refs.append(f"![]({rel})")
            metadata.append(
                {
                    "role": role,
                    "file": rel,
                    "saved": True,
                    "source_name": member_name,
                    "sha256": digest,
                    "duplicate_of_existing_asset": duplicate,
                }
            )
        return refs, metadata

    @staticmethod
    def _collapse_repeated_local_image_refs(markdown: str, min_occurrences: int = 3) -> tuple[str, int]:
        """Keep only the first inline occurrence of highly repeated local images.

        This is aimed at PDF page headers/watermarks. A threshold avoids collapsing an
        ordinary figure that is intentionally reused only once or twice.
        """
        matches = list(IMAGE_RE.finditer(markdown))
        counts: dict[str, int] = {}
        for match in matches:
            target = match.group("target").strip().replace("\\", "/")
            if target.startswith("images/"):
                counts[target] = counts.get(target, 0) + 1
        repeated = {target for target, count in counts.items() if count >= max(2, min_occurrences)}
        if not repeated:
            return markdown, 0

        seen: set[str] = set()
        collapsed = 0

        def replace(match: re.Match[str]) -> str:
            nonlocal collapsed
            target = match.group("target").strip().replace("\\", "/")
            if target not in repeated:
                return match.group(0)
            if target not in seen:
                seen.add(target)
                return match.group(0)
            collapsed += 1
            return ""

        return IMAGE_RE.sub(replace, markdown), collapsed

    @staticmethod
    def _first_attr(obj: Any, *names: str) -> Any:
        for name in names:
            try:
                value = getattr(obj, name)
            except Exception:
                continue
            if value not in (None, ""):
                return value
        return None

    def _image_payload(self, image: Any) -> bytes | None:
        value = self._first_attr(image, "data_base64")
        if isinstance(value, str) and value:
            # Accept both raw base64 and data URI.
            if value.startswith("data:") and "," in value:
                value = value.split(",", 1)[1]
            try:
                return base64.b64decode(value, validate=False)
            except (binascii.Error, ValueError):
                pass
        for name in ("data", "bytes", "content"):
            value = self._first_attr(image, name)
            if isinstance(value, (bytes, bytearray)):
                return bytes(value)
        return None

    @staticmethod
    def _guess_extension(payload: bytes, mime: str, source_name: str) -> str:
        suffix = Path(source_name).suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg"}:
            return suffix
        if mime:
            guessed = mimetypes.guess_extension(mime.split(";", 1)[0].strip())
            if guessed:
                return ".jpg" if guessed == ".jpe" else guessed
        if payload.startswith(b"\x89PNG"):
            return ".png"
        if payload.startswith(b"\xff\xd8\xff"):
            return ".jpg"
        if payload.startswith((b"GIF87a", b"GIF89a")):
            return ".gif"
        if payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
            return ".webp"
        if payload.lstrip().startswith(b"<svg"):
            return ".svg"
        return ".bin"

    def _rewrite_image_references(
        self,
        markdown: str,
        image_objects: list[Any],
        index_to_path: dict[int, Path],
        warnings: list[str],
        *,
        source_media_map: dict[str, Path] | None = None,
    ) -> str:
        """Normalize Xberg image references to portable images/image_NNN.ext links.

        Xberg 1.0.x can emit documented ``embedded:`` placeholders, generated
        ``image_0.png`` names, or OOXML-relative targets such as ``../media/image132.png``.
        Support all three forms and remove an optional Markdown escape on resolved images.
        """
        source_media_map = source_media_map or {}
        matches = list(IMAGE_RE.finditer(markdown))
        if not matches:
            if index_to_path:
                warnings.append(f"Xberg a extrait {len(index_to_path)} occurrence(s) image mais le Markdown ne contient aucune image inline.")
            return markdown

        token_map: dict[str, int] = {}
        name_map: dict[str, int] = {}
        for idx, image in enumerate(image_objects):
            for attr in ("id", "image_id", "key", "name", "filename", "file_name", "index", "source"):
                value = self._first_attr(image, attr)
                if value is not None:
                    text = str(value)
                    token_map[text] = idx
                    name_map[Path(text).name.lower()] = idx

        saved_by_original_index = dict(index_to_path)
        sequential = 0

        def next_saved() -> Path | None:
            nonlocal sequential
            while sequential < len(image_objects) and sequential not in saved_by_original_index:
                sequential += 1
            path = saved_by_original_index.get(sequential)
            sequential += 1
            return path

        def replace(match: re.Match[str]) -> str:
            target = match.group("target").strip()
            alt = match.group("alt") or "Image"

            # Leave URLs, anchors and data URIs alone.
            lowered = target.lower()
            if lowered.startswith(("http://", "https://", "data:", "#")):
                return match.group(0)

            idx: int | None = None
            embedded = EMBEDDED_RE.match(target)
            if embedded:
                idx = token_map.get(embedded.group("token"))
            else:
                normalized_target = target.replace("\\", "/")
                basename = Path(normalized_target).name.lower()
                direct_path = source_media_map.get(normalized_target.lower()) or source_media_map.get(basename)
                if direct_path is not None:
                    rel = str(Path("images") / direct_path.name).replace("\\", "/")
                    # Xberg can escape PPTX image markers as \![...]. Once the target is
                    # resolved, remove that escape so Markdown renderers display the image.
                    return f"![{alt}]({rel})"

                idx = name_map.get(basename)
                if idx is None:
                    numbered = XBERG_LOCAL_IMAGE_RE.match(normalized_target)
                    if numbered:
                        candidate = int(numbered.group("index"))
                        # Xberg's generated image_N names are zero-based in current DOCX output.
                        if candidate in saved_by_original_index:
                            idx = candidate

            path = saved_by_original_index.get(idx) if idx is not None else None
            if path is None and (embedded or XBERG_LOCAL_IMAGE_RE.match(target.replace("\\", "/"))):
                path = next_saved()
            if path is None:
                return match.group(0)

            rel = str(Path("images") / path.name).replace("\\", "/")
            return f"![{alt}]({rel})"

        return IMAGE_RE.sub(replace, markdown)

    @staticmethod
    def _find_broken_local_image_refs(markdown: str, package_dir: Path) -> list[str]:
        broken: list[str] = []
        targets: list[str] = [match.group("target").strip() for match in IMAGE_RE.finditer(markdown)]
        targets.extend(match.group("target").strip() for match in HTML_IMAGE_RE.finditer(markdown))
        for target in targets:
            lowered = target.lower()
            if lowered.startswith(("http://", "https://", "data:", "#")):
                continue
            # Strip optional Markdown title: path "title". Xberg doesn't currently use it,
            # but this keeps the validation harmless for common Markdown.
            target_path = target.split(' "', 1)[0].strip().strip("<>")
            candidate = package_dir / Path(target_path.replace("/", str(Path('/'))))
            if not candidate.exists():
                broken.append(target_path)
        return broken



def run_extract(config: dict[str, Any], source: Path, destination: Path) -> list[ExtractionOutcome]:
    return asyncio.run(XbergExtractor(config).extract_source(source, destination))

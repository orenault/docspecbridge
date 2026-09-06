from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utils import safe_stem, write_json


@dataclass
class RagExportResult:
    document_count: int
    chunk_count: int
    destination: Path
    index_path: Path
    chunks_path: Path


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _package_manifests(source: Path | list[Path]) -> list[Path]:
    if isinstance(source, list):
        manifests: list[Path] = []
        for item in source:
            manifest = item if item.name == "manifest.json" else item / "manifest.json"
            if manifest.is_file():
                manifests.append(manifest)
        return sorted(manifests)
    if source.is_file() and source.name == "manifest.json":
        return [source]
    if source.is_dir():
        return sorted(source.rglob("manifest.json"))
    raise FileNotFoundError(source)


def export_rag_corpus(
    source: Path | list[Path],
    destination: Path,
    *,
    copy_assets: bool = True,
    copy_document_json: bool = True,
    overwrite: bool = True,
) -> RagExportResult:
    """Aggregate DocSpecBridge packages into a portable RAG corpus.

    This is an exchange/export step, not vector-store ingestion.  The resulting corpus
    contains one normalized document directory per package plus a global ``index.jsonl``
    and ``chunks.jsonl`` suitable for downstream loaders.
    """
    manifests = _package_manifests(source)
    destination = destination.resolve()
    if destination.exists() and overwrite:
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    docs_root = destination / "documents"
    docs_root.mkdir(parents=True, exist_ok=True)

    index_path = destination / "index.jsonl"
    chunks_path = destination / "chunks.jsonl"
    document_count = 0
    chunk_count = 0

    with index_path.open("w", encoding="utf-8") as index_handle, chunks_path.open("w", encoding="utf-8") as chunk_handle:
        for manifest_path in manifests:
            manifest = _load_json(manifest_path)
            outputs = manifest.get("outputs") or {}
            rag_name = outputs.get("rag_markdown")
            if not rag_name:
                continue
            rag_path = manifest_path.parent / str(rag_name)
            if not rag_path.is_file():
                continue

            source_meta = manifest.get("source") or {}
            digest = str(source_meta.get("sha256") or "")
            document_id = digest[:16] if digest else safe_stem(manifest_path.parent.name)
            package_name = safe_stem(manifest_path.parent.name)
            doc_dir = docs_root / f"{package_name}__{document_id}"
            doc_dir.mkdir(parents=True, exist_ok=True)

            copied_rag = doc_dir / rag_path.name
            shutil.copy2(rag_path, copied_rag)
            shutil.copy2(manifest_path, doc_dir / "manifest.json")

            document_json = manifest_path.parent / str(outputs.get("document_json") or "document.json")
            if copy_document_json and document_json.is_file():
                shutil.copy2(document_json, doc_dir / "document.json")

            rag_descriptor = manifest_path.parent / "rag.json"
            if rag_descriptor.is_file():
                shutil.copy2(rag_descriptor, doc_dir / "rag.json")

            copied_assets: list[str] = []
            if copy_assets:
                assets_dir = manifest_path.parent / str((manifest.get("assets") or {}).get("images_directory") or "images")
                if assets_dir.is_dir():
                    target_assets = doc_dir / "images"
                    shutil.copytree(assets_dir, target_assets, dirs_exist_ok=True)
                    copied_assets = [str(Path("images") / item.name).replace("\\", "/") for item in target_assets.iterdir() if item.is_file()]

            row = {
                "document_id": document_id,
                "package": manifest_path.parent.name,
                "source": source_meta,
                "rag_markdown": str(copied_rag.relative_to(destination)).replace("\\", "/"),
                "manifest": str((doc_dir / "manifest.json").relative_to(destination)).replace("\\", "/"),
                "document_json": str((doc_dir / "document.json").relative_to(destination)).replace("\\", "/") if (doc_dir / "document.json").is_file() else None,
                "assets": [str((doc_dir / asset).relative_to(destination)).replace("\\", "/") for asset in copied_assets],
                "outline": manifest.get("outline") or {},
            }
            index_handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            document_count += 1

            package_chunks = manifest_path.parent / str(outputs.get("chunks") or "chunks.jsonl")
            if package_chunks.is_file():
                for line in package_chunks.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    chunk["document_id"] = document_id
                    chunk["package"] = manifest_path.parent.name
                    chunk["rag_markdown"] = row["rag_markdown"]
                    chunk_handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")
                    chunk_count += 1

    corpus = {
        "schema_version": "0.2.1",
        "kind": "docspecbridge-rag-corpus",
        "documents": document_count,
        "chunks": chunk_count,
        "index": "index.jsonl",
        "chunks_file": "chunks.jsonl",
        "documents_directory": "documents",
        "notes": "Portable RAG export. Embedding/vector-store ingestion is intentionally delegated to a target-specific loader.",
    }
    write_json(destination / "corpus.json", corpus)
    return RagExportResult(document_count, chunk_count, destination, index_path, chunks_path)

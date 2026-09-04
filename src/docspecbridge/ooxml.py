from __future__ import annotations

import posixpath
import re
import zipfile
from pathlib import Path
from typing import Any


MARKERS = {
    "drawing": re.compile(rb"<(?:w:)?drawing\b"),
    "legacy_vml_picture": re.compile(rb"<(?:w:)?pict\b"),
    "vml_shape": re.compile(rb"<v:shape\b"),
    "chart": re.compile(rb"<c:chart\b"),
    "diagram": re.compile(rb"<dgm:relIds\b"),
    "shape": re.compile(rb"<(?:wps:wsp|p:sp)\b"),
    "connector": re.compile(rb"<p:cxnSp\b"),
    "group_shape": re.compile(rb"<(?:wpg:wgp|p:grpSp)\b"),
    "alternate_content": re.compile(rb"<mc:AlternateContent\b"),
}

REL_TARGET_RE = re.compile(rb'Target="([^"]+)"')


def _media_targets(archive: zipfile.ZipFile, rel_name: str, owner_dir: str) -> list[str]:
    """Return normalized OOXML media paths referenced by a .rels part."""
    try:
        data = archive.read(rel_name)
    except KeyError:
        return []
    targets: list[str] = []
    for raw in REL_TARGET_RE.findall(data):
        target = raw.decode("utf-8", "ignore")
        if "media/" not in target:
            continue
        normalized = posixpath.normpath(posixpath.join(owner_dir, target))
        targets.append(normalized)
    return targets


def read_ooxml_media(path: Path, member_names: list[str]) -> list[tuple[str, bytes]]:
    """Read selected media members from a DOCX/PPTX archive."""
    if path.suffix.lower() not in {".docx", ".pptx"} or not member_names:
        return []
    result: list[tuple[str, bytes]] = []
    with zipfile.ZipFile(path) as archive:
        for name in member_names:
            try:
                result.append((name, archive.read(name)))
            except KeyError:
                continue
    return result


def inspect_ooxml(path: Path) -> dict[str, Any] | None:
    if path.suffix.lower() not in {".docx", ".pptx"}:
        return None

    suffix = path.suffix.lower()
    result: dict[str, Any] = {
        "media_count": 0,
        "media_files": [],
        "content_media_count": 0,
        "content_media_files": [],
        "header_media_count": 0,
        "header_media_files": [],
        "footer_media_count": 0,
        "footer_media_files": [],
        "header_footer_media_count": 0,
        "header_footer_media_files": [],
        "other_media_count": 0,
        "other_media_files": [],
        "markers": {},
    }

    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            prefix = "word/media/" if suffix == ".docx" else "ppt/media/"
            media = sorted(name for name in names if name.startswith(prefix) and not name.endswith("/"))
            result["media_count"] = len(media)
            result["media_files"] = media

            if suffix == ".docx":
                content_refs = set(_media_targets(archive, "word/_rels/document.xml.rels", "word"))
                header_refs: set[str] = set()
                footer_refs: set[str] = set()
                for name in names:
                    if re.fullmatch(r"word/_rels/header\d+\.xml\.rels", name):
                        header_refs.update(_media_targets(archive, name, "word"))
                    elif re.fullmatch(r"word/_rels/footer\d+\.xml\.rels", name):
                        footer_refs.update(_media_targets(archive, name, "word"))
            else:
                content_refs = set()
                for name in names:
                    if re.fullmatch(r"ppt/slides/_rels/slide\d+\.xml\.rels", name):
                        content_refs.update(_media_targets(archive, name, "ppt/slides"))
                content_refs = {x.replace("ppt/slides/../", "ppt/") for x in content_refs}
                header_refs = set()
                footer_refs = set()

            media_set = set(media)
            content_refs &= media_set
            header_refs &= media_set
            footer_refs &= media_set
            header_footer_refs = header_refs | footer_refs
            other_refs = media_set - content_refs - header_footer_refs

            result["content_media_files"] = sorted(content_refs)
            result["content_media_count"] = len(content_refs)
            result["header_media_files"] = sorted(header_refs)
            result["header_media_count"] = len(header_refs)
            result["footer_media_files"] = sorted(footer_refs)
            result["footer_media_count"] = len(footer_refs)
            result["header_footer_media_files"] = sorted(header_footer_refs)
            result["header_footer_media_count"] = len(header_footer_refs)
            result["other_media_files"] = sorted(other_refs)
            result["other_media_count"] = len(other_refs)

            xml_names = [name for name in names if name.endswith((".xml", ".rels"))]
            counts = {key: 0 for key in MARKERS}
            for name in xml_names:
                try:
                    data = archive.read(name)
                except Exception:
                    continue
                for key, pattern in MARKERS.items():
                    counts[key] += len(pattern.findall(data))
            result["markers"] = counts
    except zipfile.BadZipFile:
        result["error"] = "OOXML archive invalide"
    return result

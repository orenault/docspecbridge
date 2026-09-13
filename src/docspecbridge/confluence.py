from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml
from md2conf.api import ConfluenceAPI
from md2conf.environment import ConnectionProperties
from md2conf.options import ConfluencePageID, ProcessorOptions
from md2conf.options_converter import ConverterOptions, ImageLayoutOptions, LayoutOptions, TableLayoutOptions
from md2conf.publisher import Publisher

from .config import get_confluence_instance, normalize_domain
from .i18n import tr


def _token(instance: dict[str, Any]) -> str:
    env_name = str(instance.get("token_env") or "ATLASSIAN_API_TOKEN")
    value = os.getenv(env_name)
    if not value:
        raise RuntimeError(f"Variable d'environnement absente: {env_name}")
    return value


def _auth(instance: dict[str, Any]) -> httpx.BasicAuth:
    user = str(instance.get("user_name") or "").strip()
    if not user:
        raise RuntimeError("Email Atlassian manquant pour l'authentification par API token.")
    return httpx.BasicAuth(user, _token(instance))


def _gateway_base(instance: dict[str, Any]) -> str:
    auth_type = str(instance.get("auth_type") or "classic").lower()
    if auth_type == "scoped":
        cloud_id = str(instance.get("cloud_id") or "").strip()
        if not cloud_id:
            raise RuntimeError("cloud_id est obligatoire avec un API token scoped.")
        return f"https://api.atlassian.com/ex/confluence/{cloud_id}"
    domain = normalize_domain(instance.get("domain"))
    if not domain:
        raise RuntimeError("Domaine Confluence Cloud manquant.")
    return f"https://{domain}"


def connection_properties(
    config: dict[str, Any],
    instance_name: str | None = None,
    space_key: str | None = None,
) -> tuple[str, dict[str, Any], ConnectionProperties]:
    name, instance = get_confluence_instance(config, instance_name)
    auth_type = str(instance.get("auth_type") or "classic").lower()
    api_url = _gateway_base(instance) if auth_type == "scoped" else None
    properties = ConnectionProperties(
        domain=(normalize_domain(instance.get("domain")) or None),
        base_path="/wiki/",
        api_url=api_url,
        user_name=(instance.get("user_name") or None),
        api_key=_token(instance),
        space_key=space_key or instance.get("default_space") or None,
        api_version="v2",
    )
    return name, instance, properties


def _api_base(instance: dict[str, Any]) -> str:
    return _gateway_base(instance).rstrip("/") + "/wiki/api/v2"


def _absolute_next(instance: dict[str, Any], next_link: str) -> str:
    if next_link.startswith("http://") or next_link.startswith("https://"):
        return next_link
    return _gateway_base(instance).rstrip("/") + (next_link if next_link.startswith("/") else "/" + next_link)


def _paged_get(instance: dict[str, Any], path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    headers = {"Accept": "application/json"}
    url = f"{_api_base(instance)}{path}"
    results: list[dict[str, Any]] = []
    with httpx.Client(headers=headers, auth=_auth(instance), timeout=30.0, follow_redirects=True) as client:
        current_params = params
        while url:
            response = client.get(url, params=current_params)
            response.raise_for_status()
            payload = response.json()
            results.extend(payload.get("results", []))
            next_link = (payload.get("_links") or {}).get("next")
            if not next_link:
                break
            url = _absolute_next(instance, str(next_link))
            current_params = None
    return results


def list_spaces(config: dict[str, Any], instance_name: str | None = None) -> list[dict[str, Any]]:
    _, instance = get_confluence_instance(config, instance_name)
    return _paged_get(instance, "/spaces", {"limit": 250})


def _get_json(instance: dict[str, Any], path: str) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    url = f"{_api_base(instance)}{path}"
    with httpx.Client(headers=headers, auth=_auth(instance), timeout=30.0, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        return dict(response.json() or {})


def list_root_pages(
    config: dict[str, Any],
    instance_name: str | None,
    space_id: str,
    *,
    max_depth: int | None = None,
) -> list[dict[str, Any]]:
    """Return a visible space-root choice plus an ordered page tree.

    max_depth=0 means: virtual space root + first-level pages.
    max_depth=1 adds their children; max_depth=2 adds grandchildren.
    """
    _, instance = get_confluence_instance(config, instance_name)
    if max_depth is None:
        selector_cfg = (config.get("confluence") or {}).get("page_selector") or {}
        max_depth = int(selector_cfg.get("max_depth", 0))
    max_depth = max(0, min(2, int(max_depth)))

    space = _get_json(instance, f"/spaces/{space_id}")
    homepage_id = str(space.get("homepageId") or "").strip()
    if not homepage_id:
        return []

    root_title = str(space.get("name") or space.get("key") or space_id)
    # The Confluence space homepage is the real parent page used for content
    # published at the visible root of a space. Keep its real ID all the way
    # through selection and YAML persistence; never invent a synthetic ID.
    result: list[dict[str, Any]] = [{
        "id": homepage_id, "title": f"⌂ {root_title}", "depth": -1, "level": 0,
        "space_root": True, "parentId": None, "breadcrumb": [],
        "tree_label": f"⌂ {root_title}",
    }]

    def children(parent_id: str) -> list[dict[str, Any]]:
        values = _paged_get(instance, f"/pages/{parent_id}/direct-children", {"limit": 250})
        values = [item for item in values if str(item.get("type") or "page").lower() == "page"]
        return sorted(values, key=lambda item: (int(item.get("childPosition") or 0), str(item.get("title") or "").casefold()))

    def visit(parent_id: str, depth: int, breadcrumb: list[str], prefix: str) -> None:
        siblings = children(parent_id)
        for idx, child in enumerate(siblings):
            last = idx == len(siblings) - 1
            branch = "└─ " if last else "├─ "
            title = str(child.get("title") or "")
            item = dict(child)
            item["depth"] = depth
            item["level"] = depth + 1
            item["parentId"] = parent_id
            item["breadcrumb"] = [*breadcrumb, title]
            item["tree_label"] = prefix + branch + title
            result.append(item)
            if depth < max_depth:
                visit(str(child.get("id") or ""), depth + 1, item["breadcrumb"], prefix + ("   " if last else "│  "))

    visit(homepage_id, 0, [], "")
    return result

def _publication_markdown_from_manifest(manifest: Path) -> Path | None:
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return None
    outputs = data.get("outputs") or {}
    name = outputs.get("publication_markdown") or data.get("markdown")
    if not name:
        return None
    candidate = manifest.parent / str(name)
    return candidate if candidate.is_file() else None


def find_markdown_inputs(source: Path) -> list[Path]:
    if source.is_file():
        if source.suffix.lower() != ".md":
            raise ValueError("Pour publier un fichier, fournir le Markdown publication généré par DocSpecBridge.")
        return [source]
    if not source.is_dir():
        raise FileNotFoundError(source)

    markdown: list[Path] = []
    for manifest in sorted(source.rglob("manifest.json")):
        candidate = _publication_markdown_from_manifest(manifest)
        if candidate is not None:
            markdown.append(candidate)
    if markdown:
        return markdown

    for md in sorted(source.rglob("*.md")):
        if md.name.endswith((".rag.md", ".raw.md")):
            continue
        markdown.append(md)
    return markdown


def _page_width_properties(page_width: str | None) -> dict[str, str]:
    value = str(page_width or "max").strip().lower()
    aliases = {
        "narrow": "default",
        "etroit": "default",
        "étroit": "default",
        "fixed": "default",
        "fixed-width": "default",
        "wide": "full-width",
        "large": "full-width",
        "full": "full-width",
        "full-width": "full-width",
        "max": "max",
        "maximum": "max",
        "default": "default",
        "confluence-default": "",
        "unset": "",
        "none": "",
    }
    api_value = aliases.get(value)
    if api_value is None:
        raise ValueError(f"Unsupported Confluence page width: {page_width}")
    if not api_value:
        return {}
    return {
        "content-appearance-published": api_value,
        "content-appearance-draft": api_value,
    }


def _write_global_properties(page_width: str | None) -> Path | None:
    properties = _page_width_properties(page_width)
    if not properties:
        return None
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", prefix="docspecbridge-confluence-", delete=False, encoding="utf-8"
    )
    try:
        yaml.safe_dump(properties, handle, sort_keys=False, allow_unicode=True)
    finally:
        handle.close()
    return Path(handle.name)


def _package_document(md: Path) -> dict[str, Any]:
    path = md.parent / "document.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _filename_title(md: Path) -> str:
    doc = _package_document(md)
    metadata = doc.get("metadata") or {}
    value = str(metadata.get("filename_title") or "").strip()
    if value:
        return value
    source = doc.get("source") or {}
    original = str(source.get("original_path") or source.get("packaged_file") or "").strip()
    if original:
        return Path(original).stem
    return md.parent.name.rsplit("__", 1)[0] if "__" in md.parent.name else md.stem


def publication_title(md: Path, config: dict[str, Any]) -> str:
    """Resolve the default Confluence title for a package.

    document_title uses CanonicalDocument.title and falls back to the source filename.
    filename deliberately reproduces the historical DocSpecBridge behaviour.
    """
    publication_cfg = ((config.get("confluence") or {}).get("publication") or {})
    source_mode = str(publication_cfg.get("page_title_source") or "document_title").strip().lower()
    filename = _filename_title(md)
    if source_mode == "filename":
        return filename
    doc = _package_document(md)
    title = str(doc.get("title") or (doc.get("metadata") or {}).get("title") or "").strip()
    return title or filename


def _resolve_space(instance: dict[str, Any], space_key: str) -> dict[str, Any]:
    rows = _paged_get(instance, "/spaces", {"keys": space_key, "limit": 250})
    exact = [row for row in rows if str(row.get("key") or "").casefold() == space_key.casefold()]
    if not exact:
        raise RuntimeError(f"Espace Confluence introuvable: {space_key}")
    return exact[0]


def _find_pages_by_title(
    instance: dict[str, Any], space_id: str, title: str, *, status: str = "current"
) -> list[dict[str, Any]]:
    rows = _paged_get(
        instance,
        f"/spaces/{space_id}/pages",
        {"depth": "all", "status": status, "title": title, "limit": 250},
    )
    wanted = title.casefold()
    return [row for row in rows if str(row.get("title") or "").casefold() == wanted]


def _get_page_by_id(instance: dict[str, Any], page_id: str) -> dict[str, Any] | None:
    try:
        return _get_json(instance, f"/pages/{page_id}")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise


def _publication_state_path(md: Path) -> Path:
    return md.parent / "publication_state.json"


def _load_publication_state(md: Path) -> dict[str, Any]:
    path = _publication_state_path(md)
    if not path.is_file():
        return {"schema_version": "1.0", "confluence": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("schema_version", "1.0")
            data.setdefault("confluence", [])
            return data
    except Exception:
        pass
    return {"schema_version": "1.0", "confluence": []}


def _state_entry(
    md: Path, *, instance_name: str, space_key: str, parent_id: str
) -> dict[str, Any] | None:
    """Return the primary replace-target for this package/location.

    `add` publications are retained as copies in state but never become the implicit
    target of a later `replace`. Legacy 0.4.2-state rows without `role` are treated as
    primary for backward compatibility.
    """
    source_name = md.name
    for item in _load_publication_state(md).get("confluence") or []:
        role = str(item.get("role") or "primary")
        if (
            role == "primary"
            and str(item.get("instance") or "") == instance_name
            and str(item.get("space") or "") == space_key
            and str(item.get("parent_id") or "") == parent_id
            and str(item.get("source") or source_name) == source_name
        ):
            return dict(item)
    return None


def _save_publication_state(
    md: Path,
    *,
    instance_name: str,
    space_key: str,
    space_id: str,
    parent_id: str,
    page_id: str,
    title: str,
    publication_mode: str,
) -> None:
    from datetime import datetime, timezone

    state = _load_publication_state(md)
    rows = list(state.get("confluence") or [])
    source_name = md.name
    role = "copy" if str(publication_mode).lower() == "add" else "primary"
    new_item = {
        "instance": instance_name,
        "space": space_key,
        "space_id": space_id,
        "parent_id": parent_id,
        "page_id": page_id,
        "title": title,
        "source": source_name,
        "role": role,
        "published_at": datetime.now(timezone.utc).isoformat(),
    }
    replaced = False
    if role == "primary":
        for idx, item in enumerate(rows):
            if (
                str(item.get("role") or "primary") == "primary"
                and str(item.get("instance") or "") == instance_name
                and str(item.get("space") or "") == space_key
                and str(item.get("parent_id") or "") == parent_id
                and str(item.get("source") or source_name) == source_name
            ):
                rows[idx] = new_item
                replaced = True
                break
    else:
        # Avoid duplicate copy rows when a caller retries after a network timeout.
        for idx, item in enumerate(rows):
            if str(item.get("page_id") or "") == page_id:
                rows[idx] = new_item
                replaced = True
                break
    if not replaced:
        rows.append(new_item)
    state["confluence"] = rows
    _publication_state_path(md).write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    normalized = text.lstrip("\ufeff")
    if not normalized.startswith("---\n"):
        return {}, normalized
    lines = normalized.splitlines(keepends=True)
    end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end = idx
            break
    if end is None:
        return {}, normalized
    try:
        data = yaml.safe_load("".join(lines[1:end])) or {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return data, "".join(lines[end + 1 :]).lstrip("\r\n")


def _publication_text(md: Path, *, title: str, page_id: str | None = None) -> str:
    data, body = _split_front_matter(md.read_text(encoding="utf-8"))
    data["title"] = title
    # page_id is an ephemeral routing hint for md2conf. It is never written back into
    # render_document.md; persistent identity lives in publication_state.json.
    if page_id:
        data["page_id"] = str(page_id)
    else:
        data.pop("page_id", None)
    data.pop("space_key", None)
    body = re.sub(r"(?m)^\s*<!--\s*confluence-(?:page-id|space-key):.*?-->\s*\n?", "", body)
    front = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False).strip()
    return f"---\n{front}\n---\n\n{body.rstrip()}\n"


def _temporary_publication_file(md: Path, *, title: str, page_id: str | None = None) -> Path:
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".md",
        prefix=".docspecbridge-publish-",
        dir=md.parent,
        delete=False,
        encoding="utf-8",
    )
    try:
        handle.write(_publication_text(md, title=title, page_id=page_id))
    finally:
        handle.close()
    return Path(handle.name)


def _format_exception(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if response is None:
        return str(exc)
    status = getattr(response, "status_code", "?")
    text = str(getattr(response, "text", "") or "").strip()
    if len(text) > 1200:
        text = text[:1200] + "…"
    return f"HTTP {status}: {text or exc}"


def _unique_add_title(
    instance: dict[str, Any], space_id: str, requested: str, suffix_pattern: str
) -> str:
    if not _find_pages_by_title(instance, space_id, requested):
        return requested
    n = 2
    while n < 10000:
        suffix = suffix_pattern.replace("{n}", str(n))
        candidate = f"{requested}{suffix}"
        if not _find_pages_by_title(instance, space_id, candidate):
            return candidate
        n += 1
    raise RuntimeError("Impossible de trouver un titre Confluence unique après 9999 essais.")


def suggest_add_title(
    config: dict[str, Any],
    *,
    instance_name: str,
    space_id: str,
    requested: str,
) -> str:
    _, instance = get_confluence_instance(config, instance_name)
    publication_cfg = ((config.get("confluence") or {}).get("publication") or {})
    suffix = str(publication_cfg.get("add_title_suffix") or " ({n})")
    return _unique_add_title(instance, space_id, requested.strip(), suffix)


@dataclass
class PublicationResult:
    source: Path
    page_id: str
    title: str
    space: str
    parent_id: str
    action: str


def prepare_publication_target(
    config: dict[str, Any],
    md: Path,
    *,
    instance_name: str,
    instance: dict[str, Any],
    space_key: str,
    space_id: str,
    parent_id: str,
    mode: str,
    requested_title: str | None = None,
) -> tuple[str, str | None, str]:
    """Resolve final title, target page ID and action before md2conf runs."""
    publication_cfg = ((config.get("confluence") or {}).get("publication") or {})
    title = (requested_title or publication_title(md, config)).strip()
    if not title:
        raise RuntimeError("Le titre de page Confluence ne peut pas être vide.")
    mode = str(mode or publication_cfg.get("default_mode") or "replace").strip().lower()
    if mode not in {"replace", "add"}:
        raise ValueError("Le mode de publication doit être 'replace' ou 'add'.")

    if mode == "add":
        suffix = str(publication_cfg.get("add_title_suffix") or " ({n})")
        return _unique_add_title(instance, space_id, title, suffix), None, "create"

    state = _state_entry(md, instance_name=instance_name, space_key=space_key, parent_id=parent_id)
    target_id = str((state or {}).get("page_id") or "").strip()
    if target_id:
        target = _get_page_by_id(instance, target_id)
        if target is not None:
            same_space = str(target.get("spaceId") or "") == space_id
            same_parent = str(target.get("parentId") or "") == parent_id
            if same_space and same_parent:
                conflicts = [
                    page for page in _find_pages_by_title(instance, space_id, title)
                    if str(page.get("id") or "") != target_id
                ]
                if conflicts:
                    other = conflicts[0]
                    raise RuntimeError(
                        f"Conflit de titre dans l'espace {space_key}: '{title}' existe déjà "
                        f"(ID {other.get('id')}, parent {other.get('parentId')})."
                    )
                return title, target_id, "update"

    matches = _find_pages_by_title(instance, space_id, title)
    if not matches:
        return title, None, "create"
    under_parent = [page for page in matches if str(page.get("parentId") or "") == parent_id]
    if len(under_parent) == 1:
        return title, str(under_parent[0].get("id") or ""), "update"
    if len(under_parent) > 1:
        raise RuntimeError(f"Plusieurs pages '{title}' ont été trouvées sous le parent {parent_id}; publication annulée.")
    other = matches[0]
    raise RuntimeError(
        f"Conflit de titre dans l'espace {space_key}: '{title}' existe déjà ailleurs "
        f"(ID {other.get('id')}, parent {other.get('parentId')}). "
        "Choisir un autre titre ou publier explicitement à cet emplacement."
    )


def _verify_publication(
    instance: dict[str, Any],
    *,
    space_id: str,
    parent_id: str,
    title: str,
    target_page_id: str | None,
) -> dict[str, Any]:
    if target_page_id:
        page = _get_page_by_id(instance, target_page_id)
        if page is None:
            raise RuntimeError(f"Confluence n'a pas retourné la page attendue après publication (ID {target_page_id}).")
        candidates = [page]
    else:
        candidates = _find_pages_by_title(instance, space_id, title)

    valid = [
        page for page in candidates
        if str(page.get("spaceId") or "") == space_id
        and str(page.get("title") or "") == title
        and str(page.get("parentId") or "") == parent_id
        and str(page.get("status") or "current").lower() == "current"
    ]
    if len(valid) == 1:
        return valid[0]

    drafts = _find_pages_by_title(instance, space_id, title, status="draft")
    draft_hint = ""
    if drafts:
        draft = drafts[0]
        draft_hint = f" Un brouillon existe (ID {draft.get('id')}, parent {draft.get('parentId')})."
    raise RuntimeError(
        f"La publication n'a pas été confirmée par Confluence pour '{title}' sous le parent {parent_id}."
        + draft_hint
    )


def publish(
    config: dict[str, Any],
    source: Path,
    space_key: str | None = None,
    root_page: str | None = None,
    instance_name: str | None = None,
    *,
    keep_hierarchy: bool | None = None,
    overwrite_manual_changes: bool | None = None,
    mode: str | None = None,
    title: str | None = None,
) -> list[PublicationResult]:
    instance_name, instance, properties = connection_properties(config, instance_name, space_key)
    space = space_key or str(instance.get("default_space") or "").strip()
    if not space:
        raise RuntimeError(tr(config, "confluence.missing_space", instance=instance_name))

    md_files = find_markdown_inputs(source)
    if not md_files:
        raise RuntimeError(tr(config, "confluence.no_publication_md", source=source))
    if title is not None and len(md_files) != 1:
        raise RuntimeError("--title ne peut être utilisé que pour une publication contenant une seule page.")

    if root_page is None:
        root = str(instance.get("root_page") or "").strip()
    else:
        root = str(root_page).strip()
    if not root:
        # A deterministic real parent is required for conflict detection and verification.
        space_info = _resolve_space(instance, space)
        root = str(space_info.get("homepageId") or "").strip()
        if not root:
            raise RuntimeError(f"Aucune homepage Confluence détectée pour l'espace {space}.")

    space_info = _resolve_space(instance, space)
    space_id = str(space_info.get("id") or "")
    if not space_id:
        raise RuntimeError(f"ID de l'espace Confluence introuvable: {space}")

    cf = config.get("confluence") or {}
    publication_cfg = cf.get("publication") or {}
    publish_mode = str(mode or publication_cfg.get("default_mode") or "replace").strip().lower()
    verify = bool(publication_cfg.get("verify_after_publish", True))
    layout_cfg = cf.get("layout") or {}
    converter_cfg = cf.get("converter") or {}
    render_mermaid = converter_cfg.get("render_mermaid")
    if render_mermaid is None:
        render_mermaid = cf.get("render_mermaid", False)

    converter = ConverterOptions(
        heading_anchors=bool(cf.get("heading_anchors", True)),
        force_valid_url=bool(converter_cfg.get("force_valid_url", True)),
        skip_title_heading=bool(converter_cfg.get("skip_title_heading", False)),
        prefer_raster=bool(converter_cfg.get("prefer_raster", True)),
        render_drawio=bool(converter_cfg.get("render_drawio", False)),
        render_mermaid=bool(render_mermaid),
        render_plantuml=bool(converter_cfg.get("render_plantuml", False)),
        render_latex=bool(converter_cfg.get("render_latex", False)),
        diagram_output_format=(
            "svg" if str(converter_cfg.get("diagram_output_format") or "png").lower() == "svg" else "png"
        ),
        webui_links=bool(converter_cfg.get("webui_links", False)),
        user_mentions=bool(converter_cfg.get("user_mentions", True)),
        use_panel=bool(converter_cfg.get("use_panel", False)),
        force_valid_language=bool(converter_cfg.get("force_valid_language", True)),
        layout=LayoutOptions(
            image=ImageLayoutOptions(
                alignment=layout_cfg.get("image_alignment") or None,
                max_width=layout_cfg.get("image_max_width") or None,
            ),
            table=TableLayoutOptions(
                width=layout_cfg.get("table_width") or None,
                display_mode=layout_cfg.get("table_display_mode") or "responsive",
            ),
            alignment=layout_cfg.get("alignment") or None,
        ),
    )
    global_properties = _write_global_properties(str(cf.get("page_width") or "max"))
    effective_hierarchy = bool(cf.get("keep_hierarchy", False)) if keep_hierarchy is None else bool(keep_hierarchy)

    # Hierarchical corpus mode remains delegated to md2conf because directory ancestry is
    # part of its synchronization algorithm. Destination IDs are never injected back into
    # DocSpecBridge render files.
    if source.is_dir() and effective_hierarchy and len(md_files) > 1:
        options = ProcessorOptions(
            root_page=ConfluencePageID(root),
            keep_hierarchy=True,
            overwrite=(
                bool(cf.get("overwrite_manual_changes", False))
                if overwrite_manual_changes is None else bool(overwrite_manual_changes)
            ),
            comments=str(cf.get("comments") or "remove"),
            skip_update=True,
            converter=converter,
            global_properties=global_properties,
        )
        try:
            with ConfluenceAPI(properties) as api:
                publisher = Publisher(api, options)
                ignore = source / ".mdignore"
                had_ignore = ignore.is_file()
                existing = ignore.read_text(encoding="utf-8") if had_ignore else ""
                allowed = {md.resolve() for md in md_files}
                generated_rules = [
                    candidate.relative_to(source).as_posix()
                    for candidate in sorted(source.rglob("*.md"))
                    if candidate.resolve() not in allowed
                ]
                if generated_rules:
                    lines = existing.splitlines()
                    additions = [rule for rule in generated_rules if rule not in lines]
                    if additions:
                        prefix = "" if not existing or existing.endswith("\n") else "\n"
                        ignore.write_text(existing + prefix + "\n".join(additions) + "\n", encoding="utf-8")
                try:
                    publisher.process(source)
                except Exception as exc:
                    raise RuntimeError(_format_exception(exc)) from exc
                finally:
                    if had_ignore:
                        ignore.write_text(existing, encoding="utf-8")
                    else:
                        ignore.unlink(missing_ok=True)
        finally:
            if global_properties is not None:
                global_properties.unlink(missing_ok=True)
        # Verify that every expected title exists somewhere in the target space. Parent
        # verification is intentionally omitted here because hierarchy determines it.
        results: list[PublicationResult] = []
        for md in md_files:
            expected = publication_title(md, config)
            pages = _find_pages_by_title(instance, space_id, expected) if verify else []
            if verify and not pages:
                raise RuntimeError(f"Publication hiérarchique non confirmée pour '{expected}'.")
            page = pages[0] if pages else {}
            results.append(PublicationResult(md, str(page.get("id") or ""), expected, space, str(page.get("parentId") or root), "sync"))
        return results

    results: list[PublicationResult] = []
    try:
        for md in md_files:
            requested = title if len(md_files) == 1 else None
            final_title, target_page_id, action = prepare_publication_target(
                config,
                md,
                instance_name=instance_name,
                instance=instance,
                space_key=space,
                space_id=space_id,
                parent_id=root,
                mode=publish_mode,
                requested_title=requested,
            )
            temp_md = _temporary_publication_file(md, title=final_title, page_id=target_page_id)
            options = ProcessorOptions(
                root_page=ConfluencePageID(root),
                keep_hierarchy=False,
                overwrite=(
                    bool(cf.get("overwrite_manual_changes", False))
                    if overwrite_manual_changes is None else bool(overwrite_manual_changes)
                ),
                comments=str(cf.get("comments") or "remove"),
                # Critical 0.4.2 rule: render_document.md is target-agnostic.
                skip_update=True,
                converter=converter,
                global_properties=global_properties,
            )
            try:
                with ConfluenceAPI(properties) as api:
                    Publisher(api, options).process(temp_md)
            except Exception as exc:
                raise RuntimeError(_format_exception(exc)) from exc
            finally:
                temp_md.unlink(missing_ok=True)

            if verify:
                page = _verify_publication(
                    instance,
                    space_id=space_id,
                    parent_id=root,
                    title=final_title,
                    target_page_id=target_page_id,
                )
            else:
                page = _get_page_by_id(instance, target_page_id) if target_page_id else None
                if page is None:
                    matches = _find_pages_by_title(instance, space_id, final_title)
                    page = next((x for x in matches if str(x.get("parentId") or "") == root), {})

            page_id = str((page or {}).get("id") or target_page_id or "")
            if not page_id:
                raise RuntimeError(f"Confluence n'a pas fourni d'ID vérifiable pour '{final_title}'.")
            _save_publication_state(
                md,
                instance_name=instance_name,
                space_key=space,
                space_id=space_id,
                parent_id=root,
                page_id=page_id,
                title=final_title,
                publication_mode=publish_mode,
            )
            results.append(PublicationResult(md, page_id, final_title, space, root, action))
    finally:
        if global_properties is not None:
            global_properties.unlink(missing_ok=True)
    return results


# ---------------------------------------------------------------------------
# 0.4 source adapter: Confluence Cloud -> CanonicalDocument package
# ---------------------------------------------------------------------------

def get_page_storage(
    config: dict[str, Any], page_id: str, instance_name: str | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    name, instance = get_confluence_instance(config, instance_name)
    payload = _get_json(instance, f"/pages/{page_id}?body-format=storage")
    return name, instance, payload


def _storage_body_value(page: dict[str, Any]) -> str:
    body = page.get("body") or {}
    storage = body.get("storage") or {}
    return str(storage.get("value") or "")


def _normalize_storage_for_canonical(storage: str) -> str:
    """Convert common Confluence Storage Format nodes to neutral XHTML.

    The original storage is always preserved beside document.json, so unsupported
    macros are not destroyed; they simply become textual placeholders in the canonical
    view until a dedicated adapter exists.
    """
    import re
    value = storage or ""
    # Native TOC macro.
    value = re.sub(
        r"<ac:structured-macro\b[^>]*ac:name=[\"']toc[\"'][^>]*>.*?</ac:structured-macro>",
        "<docspecbridge-toc></docspecbridge-toc>", value, flags=re.I | re.S,
    )
    # Attached images. Keep filename as a portable local reference; export_page_to_package
    # downloads attachments and rewrites matching paths when possible.
    def image_repl(match: re.Match[str]) -> str:
        body = match.group(1)
        filename = re.search(r"ri:filename=[\"']([^\"']+)[\"']", body, flags=re.I)
        if filename:
            name = filename.group(1)
            return f'<img src="attachments/{name}" alt="{name}"/>'
        url = re.search(r"ri:value=[\"']([^\"']+)[\"']", body, flags=re.I)
        if url:
            return f'<img src="{url.group(1)}" alt=""/>'
        return ""
    value = re.sub(r"<ac:image\b[^>]*>(.*?)</ac:image>", image_repl, value, flags=re.I | re.S)
    # Rich-text bodies of generic macros: preserve their visible body, dropping only
    # the macro wrapper. This is intentionally conservative.
    value = re.sub(r"<ac:rich-text-body\b[^>]*>", "<div>", value, flags=re.I)
    value = re.sub(r"</ac:rich-text-body>", "</div>", value, flags=re.I)
    value = re.sub(r"<ac:structured-macro\b[^>]*>", "<div class=\"confluence-macro\">", value, flags=re.I)
    value = re.sub(r"</ac:structured-macro>", "</div>", value, flags=re.I)
    # Drop remaining parameter / resource identifier tags but keep textual content.
    value = re.sub(r"</?(?:ac|ri):[^>]+>", "", value, flags=re.I)
    return value


def export_page_to_package(
    config: dict[str, Any], page_id: str, destination: Path, *, instance_name: str | None = None,
) -> Path:
    from .canonical import canonical_from_xhtml
    from .package_io import write_canonical_package
    from .utils import safe_stem, write_json

    name, instance, page = get_page_storage(config, page_id, instance_name)
    title = str(page.get("title") or f"confluence-{page_id}")
    package = destination / f"{safe_stem(title)}__confluence"
    package.mkdir(parents=True, exist_ok=True)
    write_json(package / "confluence.page.json", page)
    storage = _storage_body_value(page)
    (package / "confluence.storage.xhtml").write_text(storage, encoding="utf-8")

    assets: list[dict[str, Any]] = []
    attachments_dir = package / "attachments"
    attachment_rows = _paged_get(instance, f"/pages/{page_id}/attachments", {"limit": 250})
    if attachment_rows:
        attachments_dir.mkdir(parents=True, exist_ok=True)
        with httpx.Client(auth=_auth(instance), timeout=60.0, follow_redirects=True) as client:
            for item in attachment_rows:
                filename = Path(str(item.get("title") or item.get("id") or "attachment.bin")).name
                link = str(item.get("downloadLink") or (item.get("_links") or {}).get("download") or "").strip()
                if not link:
                    continue
                url = link if link.startswith(("http://", "https://")) else _gateway_base(instance).rstrip("/") + (link if link.startswith("/") else "/" + link)
                try:
                    response = client.get(url)
                    response.raise_for_status()
                    target = attachments_dir / filename
                    target.write_bytes(response.content)
                    assets.append({
                        "role": "attachment", "file": str(Path("attachments") / filename).replace("\\", "/"),
                        "saved": True, "attachment_id": item.get("id"), "mime_type": item.get("mediaType"),
                        "source_url": url,
                    })
                except Exception:
                    assets.append({
                        "role": "attachment", "file": str(Path("attachments") / filename).replace("\\", "/"),
                        "saved": False, "attachment_id": item.get("id"), "mime_type": item.get("mediaType"),
                        "source_url": url,
                    })

    neutral = _normalize_storage_for_canonical(storage)
    doc = canonical_from_xhtml(
        neutral,
        title=title,
        assets=assets,
        source={
            "type": "confluence",
            "instance": name,
            "page_id": str(page.get("id") or page_id),
            "space_id": str(page.get("spaceId") or ""),
            "parent_id": str(page.get("parentId") or ""),
            "version": (page.get("version") or {}).get("number") if isinstance(page.get("version"), dict) else None,
        },
    )
    write_canonical_package(
        doc,
        package,
        stem=safe_stem(title),
        rag_profile=((config.get("profiles") or {}).get("rag") or {}),
        publication_profile=((config.get("profiles") or {}).get("publication") or {}),
        extra_manifest={"confluence": {"storage_source": "confluence.storage.xhtml"}},
    )
    return package

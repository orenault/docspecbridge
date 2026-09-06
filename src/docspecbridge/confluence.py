from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
from md2conf.api import ConfluenceAPI
from md2conf.environment import ConnectionProperties
from md2conf.options import ConfluencePageID, ProcessorOptions
from md2conf.options_converter import ConverterOptions, ImageLayoutOptions, LayoutOptions, TableLayoutOptions
from md2conf.publisher import Publisher

from .config import get_confluence_instance, normalize_domain


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


def list_root_pages(
    config: dict[str, Any],
    instance_name: str | None,
    space_id: str,
) -> list[dict[str, Any]]:
    _, instance = get_confluence_instance(config, instance_name)
    return _paged_get(instance, f"/spaces/{space_id}/pages", {"depth": "root", "limit": 250, "status": "current"})


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


def publish(
    config: dict[str, Any],
    source: Path,
    space_key: str | None = None,
    root_page: str | None = None,
    instance_name: str | None = None,
    *,
    keep_hierarchy: bool | None = None,
    overwrite_manual_changes: bool | None = None,
) -> list[Path]:
    instance_name, instance, properties = connection_properties(config, instance_name, space_key)
    space = space_key or str(instance.get("default_space") or "").strip()
    if not space:
        raise RuntimeError(
            f"Espace Confluence non renseigné pour l'instance {instance_name} (--space ou default_space dans le paramétrage)"
        )

    md_files = find_markdown_inputs(source)
    if not md_files:
        raise RuntimeError(f"Aucun Markdown publication à publier sous {source}")

    if root_page is None:
        root = str(instance.get("root_page") or "").strip() or None
    else:
        root = str(root_page).strip() or None
    cf = config.get("confluence") or {}
    layout_cfg = cf.get("layout") or {}
    converter = ConverterOptions(
        render_mermaid=bool(cf.get("render_mermaid", False)),
        heading_anchors=bool(cf.get("heading_anchors", True)),
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
    options = ProcessorOptions(
        root_page=ConfluencePageID(root) if root else None,
        keep_hierarchy=bool(cf.get("keep_hierarchy", False)) if keep_hierarchy is None else bool(keep_hierarchy),
        overwrite=(
            bool(cf.get("overwrite_manual_changes", False))
            if overwrite_manual_changes is None
            else bool(overwrite_manual_changes)
        ),
        comments=str(cf.get("comments") or "remove"),
        skip_update=not bool(cf.get("write_page_id_to_markdown", True)),
        converter=converter,
    )

    effective_hierarchy = bool(cf.get("keep_hierarchy", False)) if keep_hierarchy is None else bool(keep_hierarchy)
    with ConfluenceAPI(properties) as api:
        publisher = Publisher(api, options)
        if source.is_dir() and effective_hierarchy:
            # md2conf directory mode can preserve hierarchy and resolve cross-page links,
            # but DocSpecBridge packages also contain *.rag.md/*.raw.md. Exclude those
            # derivatives from publication with md2conf's native .mdignore support.
            ignore = source / ".mdignore"
            existing = ignore.read_text(encoding="utf-8") if ignore.is_file() else ""
            required = ["*.rag.md", "*.raw.md"]
            missing = [rule for rule in required if rule not in existing.splitlines()]
            if missing:
                prefix = "" if not existing or existing.endswith("\n") else "\n"
                ignore.write_text(existing + prefix + "\n".join(missing) + "\n", encoding="utf-8")
            publisher.process(source)
        else:
            for md in md_files:
                publisher.process(md)
    return md_files

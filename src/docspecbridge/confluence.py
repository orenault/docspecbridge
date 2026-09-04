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

from .config import get_confluence_instance


def _token(instance: dict[str, Any]) -> str:
    env_name = str(instance.get("token_env") or "ATLASSIAN_API_TOKEN")
    value = os.getenv(env_name)
    if not value:
        raise RuntimeError(f"Variable d'environnement absente: {env_name}")
    return value


def connection_properties(
    config: dict[str, Any],
    instance_name: str | None = None,
    space_key: str | None = None,
) -> tuple[str, dict[str, Any], ConnectionProperties]:
    name, instance = get_confluence_instance(config, instance_name)
    properties = ConnectionProperties(
        domain=(instance.get("domain") or None),
        base_path=(instance.get("base_path") or "/wiki/"),
        api_url=(instance.get("api_url") or None),
        user_name=(instance.get("user_name") or None),
        api_key=_token(instance),
        space_key=space_key or instance.get("default_space") or None,
        api_version="v2",
    )
    return name, instance, properties


def _api_base(instance: dict[str, Any]) -> str:
    api_url = str(instance.get("api_url") or "").strip()
    if api_url:
        base = api_url.rstrip("/")
        if base.endswith("/wiki/api/v2"):
            return base
        return base + "/wiki/api/v2"
    domain = str(instance.get("domain") or "").strip()
    if not domain:
        raise RuntimeError("confluence.instances.<name>.domain doit être renseigné")
    return f"https://{domain}/wiki/api/v2"


def list_spaces(config: dict[str, Any], instance_name: str | None = None) -> list[dict[str, Any]]:
    _, instance = get_confluence_instance(config, instance_name)
    token = _token(instance)
    user = str(instance.get("user_name") or "").strip()
    headers = {"Accept": "application/json"}
    auth = None
    if user:
        auth = httpx.BasicAuth(user, token)
    else:
        headers["Authorization"] = f"Bearer {token}"

    url = f"{_api_base(instance)}/spaces"
    spaces: list[dict[str, Any]] = []
    params: dict[str, Any] | None = {"limit": 250}
    with httpx.Client(headers=headers, auth=auth, timeout=30.0, follow_redirects=True) as client:
        while url:
            response = client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
            spaces.extend(payload.get("results", []))
            next_link = (payload.get("_links") or {}).get("next")
            if next_link:
                if next_link.startswith("http"):
                    url = next_link
                else:
                    domain = str(instance.get("domain") or "").strip()
                    if domain:
                        url = f"https://{domain}" + (next_link if next_link.startswith("/") else "/" + next_link)
                    else:
                        url = _api_base(instance).split("/wiki/api/v2", 1)[0] + (
                            next_link if next_link.startswith("/") else "/" + next_link
                        )
                params = None
            else:
                url = ""
    return spaces


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

    # Backward-compatible fallback for old 0.1.x packages.
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
) -> list[Path]:
    instance_name, instance, properties = connection_properties(config, instance_name, space_key)
    space = space_key or str(instance.get("default_space") or "").strip()
    if not space:
        raise RuntimeError(
            f"Espace Confluence non renseigné pour l'instance {instance_name} (--space ou default_space dans le YAML)"
        )

    md_files = find_markdown_inputs(source)
    if not md_files:
        raise RuntimeError(f"Aucun Markdown publication à publier sous {source}")

    root = root_page or str(instance.get("root_page") or "").strip() or None
    cf = config.get("confluence") or {}
    layout_cfg = cf.get("layout") or {}
    converter = ConverterOptions(
        render_mermaid=bool(cf.get("render_mermaid", False)),
        layout=LayoutOptions(
            image=ImageLayoutOptions(
                alignment=layout_cfg.get("image_alignment") or None,
                max_width=layout_cfg.get("image_max_width") or None,
            ),
            table=TableLayoutOptions(display_mode=layout_cfg.get("table_display_mode") or "responsive"),
        ),
    )
    options = ProcessorOptions(
        root_page=ConfluencePageID(root) if root else None,
        keep_hierarchy=bool(cf.get("keep_hierarchy", False)),
        overwrite=bool(cf.get("overwrite_manual_changes", False)),
        skip_update=not bool(cf.get("write_page_id_to_markdown", False)),
        converter=converter,
    )

    # Publication Markdown points to display-sized raster derivatives when source
    # geometry is known. md2conf therefore uploads those derivatives as normal local
    # attachments, preserving source-relative image sizing without layout metadata in RAG.
    with ConfluenceAPI(properties) as api:
        publisher = Publisher(api, options)
        for md in md_files:
            publisher.process(md)
    return md_files

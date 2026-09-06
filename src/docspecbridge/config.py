from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from .i18n import detect_os_language, normalize_language


DEFAULT_CONFIG: dict[str, Any] = {
    "app": {
        "language": "auto",
        "source": "./input",
        "destination": "./output",
        "extensions": [".docx", ".pdf", ".pptx"],
        "recursive": True,
        "preserve_source_tree": True,
        "copy_source": True,
        "overwrite": False,
    },
    "extract": {
        "engine": "xberg",
        "use_cache": True,
        "enable_quality_processing": True,
        "output_format": "markdown",
        "extraction_timeout_secs": 600,
        "images": {
            "extract_images": True,
            "target_dpi": 300,
            "max_image_dimension": 4096,
            "auto_adjust_dpi": True,
            "inject_placeholders": True,
            "include_data_base64": True,
            "run_ocr_on_images": False,
        },
        "deduplicate_assets": True,
        "docx_layout": {
            "include_header_images_once": True,
            "include_footer_images_once": False,
        },
        "pdf_layout": {
            "collapse_repeated_images": True,
            "repeat_threshold": 3,
        },
        "pdf_options": {
            "extract_images": True,
            "extract_tables": True,
            "extract_metadata": True,
        },
        "diagnostics": {
            "inspect_ooxml": True,
            "warn_on_unresolved_images": True,
            # Raw Xberg output is only for troubleshooting/audit. The packaged source
            # remains the authoritative lossless input, so raw Markdown is off by default.
            "keep_raw_xberg_markdown": False,
        },
    },
    "profiles": {
        "publication": {
            "enabled": True,
            "preserve_image_display_size": True,
            "display_image_directory": "publication_images",
            "min_display_px": 12,
            "max_display_px": 1800,
            "avoid_upscale": False,
            "preserve_formatting": True,
            # Gives md2conf a deterministic page title and prevents filename+digest titles.
            "add_title_front_matter": True,
            "table_of_contents": {
                "enabled": "auto",
                "replace_source_toc": True,
            },
        },
        "rag": {
            "enabled": True,
            "write_descriptor": True,
            "keep_image_references": True,
            "include_header_images": False,
            "include_footer_images": False,
            "collapse_repeated_images": True,
            "token_reduction": "off",
            "chunking": {
                "enabled": True,
                "max_characters": 1600,
                "overlap": 150,
                "prepend_heading_context": True,
            },
        },
    },
    "rag_export": {
        "destination": "./rag",
        "copy_assets": True,
        "copy_document_json": True,
        "overwrite": True,
    },
    "confluence": {
        "default_instance": "",
        "instances": {},
        "layout": {
            "alignment": "center",
            "image_alignment": "center",
            "image_max_width": 1600,
            "table_width": None,
            "table_display_mode": "responsive",
        },
        "keep_hierarchy": False,
        "overwrite_manual_changes": False,
        "comments": "remove",
        "write_page_id_to_markdown": True,
        "heading_anchors": True,
        "render_mermaid": False,
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def normalize_domain(value: str | None) -> str:
    """Normalize a Confluence Cloud site to a bare host name.

    Accepts values such as https://company.atlassian.net/ and returns
    company.atlassian.net. Paths are intentionally discarded.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    return (parsed.hostname or parsed.path.split("/", 1)[0]).rstrip("/").lower()


def _migrate_instance(instance: dict[str, Any]) -> dict[str, Any]:
    item = dict(instance or {})
    item["domain"] = normalize_domain(item.get("domain"))
    item["base_path"] = "/wiki/"
    item["api_version"] = "v2"
    item.setdefault("token_env", "ATLASSIAN_API_TOKEN")
    item.setdefault("user_name", "")
    item.setdefault("default_space", "")
    item.setdefault("root_page", "")

    # 0.2.0b1 inferred Bearer from a blank user and accepted free-form api_url.
    # Migrate to an explicit token model while preserving an existing scoped URL.
    auth_type = str(item.get("auth_type") or "").strip().lower()
    api_url = str(item.get("api_url") or "").strip().rstrip("/")
    cloud_id = str(item.get("cloud_id") or "").strip()
    if not auth_type:
        auth_type = "scoped" if "api.atlassian.com/ex/confluence/" in api_url else "classic"
    if auth_type == "scoped" and not cloud_id and "api.atlassian.com/ex/confluence/" in api_url:
        cloud_id = api_url.split("/ex/confluence/", 1)[1].split("/", 1)[0]
    item["auth_type"] = "scoped" if auth_type == "scoped" else "classic"
    item["cloud_id"] = cloud_id
    if item["auth_type"] == "scoped" and cloud_id:
        item["api_url"] = f"https://api.atlassian.com/ex/confluence/{cloud_id}"
    elif item["auth_type"] == "classic":
        item["api_url"] = ""
    else:
        item["api_url"] = api_url
    return item


def _migrate_legacy(data: dict[str, Any]) -> dict[str, Any]:
    data = deepcopy(data)

    legacy_rag = data.pop("rag", None)
    if isinstance(legacy_rag, dict):
        profiles = data.setdefault("profiles", {})
        profiles["rag"] = _deep_merge(profiles.get("rag", {}), legacy_rag)

    cf = data.get("confluence")
    if isinstance(cf, dict) and not cf.get("instances"):
        legacy_keys = {
            "domain",
            "base_path",
            "api_url",
            "user_name",
            "token_env",
            "api_version",
            "default_space",
            "root_page",
            "auth_type",
            "cloud_id",
        }
        if any(cf.get(key) not in (None, "") for key in legacy_keys):
            instance = {key: cf.get(key) for key in legacy_keys if key in cf}
            cf["instances"] = {"default": _migrate_instance(instance)}
            cf["default_instance"] = cf.get("default_instance") or "default"
        for key in legacy_keys:
            cf.pop(key, None)

    if isinstance(cf, dict):
        instances = cf.get("instances") or {}
        cf["instances"] = {str(name): _migrate_instance(dict(value or {})) for name, value in instances.items()}

    app = data.setdefault("app", {})
    lang = str(app.get("language") or "auto").strip().lower()
    if lang == "sp":
        app["language"] = "es"
    return data


def find_default_config() -> Path | None:
    for name in ("docspecbridge.yaml", "docspecbridge.yml", "config.yaml", "config.yml"):
        path = Path.cwd() / name
        if path.is_file():
            return path
    return None


def selected_config_path(path: Path | None = None) -> Path:
    return (path or find_default_config() or (Path.cwd() / "docspecbridge.yaml")).resolve()


def load_config(path: Path | None = None) -> dict[str, Any]:
    selected = path or find_default_config()
    if selected is None:
        cfg = deepcopy(DEFAULT_CONFIG)
        cfg["app"]["language"] = detect_os_language()
        return cfg
    with selected.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Configuration YAML invalide: {selected}")
    cfg = _deep_merge(DEFAULT_CONFIG, _migrate_legacy(data))
    cfg["app"]["language"] = normalize_language(str(cfg["app"].get("language") or detect_os_language()))
    return cfg


def save_config(config: dict[str, Any], path: Path | None = None) -> Path:
    selected = selected_config_path(path)
    selected.parent.mkdir(parents=True, exist_ok=True)
    selected.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )
    ensure_workdirs(config)
    return selected


def init_config(path: Path | None = None, *, overwrite: bool = False) -> Path:
    selected = selected_config_path(path)
    if selected.exists() and not overwrite:
        return selected
    cfg = deepcopy(DEFAULT_CONFIG)
    cfg["app"]["language"] = detect_os_language()
    return save_config(cfg, selected)


def ensure_workdirs(config: dict[str, Any]) -> tuple[Path | None, Path | None]:
    """Create configured working directories when the configured values are directories."""
    app = config.get("app") or {}
    extensions = {str(item).lower() for item in app.get("extensions") or []}
    source = Path(str(app.get("source") or "./input"))
    destination = Path(str(app.get("destination") or "./output"))

    source_dir: Path | None = source
    if source.suffix.lower() in extensions:
        source_dir = None
    elif not source.exists():
        source.mkdir(parents=True, exist_ok=True)
    destination.mkdir(parents=True, exist_ok=True)
    rag_destination = Path(str((config.get("rag_export") or {}).get("destination") or "./rag"))
    rag_destination.mkdir(parents=True, exist_ok=True)
    return source_dir, destination


def confluence_instances(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cf = config.get("confluence") or {}
    instances = cf.get("instances") or {}
    return {str(name): _migrate_instance(dict(value or {})) for name, value in instances.items()}


def get_confluence_instance(config: dict[str, Any], name: str | None = None) -> tuple[str, dict[str, Any]]:
    cf = config.get("confluence") or {}
    instances = confluence_instances(config)
    selected = (name or cf.get("default_instance") or "").strip()
    if not selected:
        if len(instances) == 1:
            selected = next(iter(instances))
        else:
            raise RuntimeError("Aucune instance Confluence sélectionnée et confluence.default_instance est vide.")
    if selected not in instances:
        raise RuntimeError(f"Instance Confluence inconnue: {selected}")
    instance = _deep_merge(
        {
            "domain": "",
            "base_path": "/wiki/",
            "api_url": "",
            "user_name": "",
            "token_env": "ATLASSIAN_API_TOKEN",
            "api_version": "v2",
            "auth_type": "classic",
            "cloud_id": "",
            "default_space": "",
            "root_page": "",
        },
        instances[selected],
    )
    return selected, _migrate_instance(instance)

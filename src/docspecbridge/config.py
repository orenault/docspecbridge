from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG: dict[str, Any] = {
    "app": {
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
            "keep_raw_xberg_markdown": True,
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
    "confluence": {
        "default_instance": "",
        "instances": {},
        "layout": {
            "image_alignment": "center",
            "image_max_width": 1600,
            "table_display_mode": "responsive",
        },
        "keep_hierarchy": False,
        "overwrite_manual_changes": False,
        "write_page_id_to_markdown": False,
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


def _migrate_legacy(data: dict[str, Any]) -> dict[str, Any]:
    data = deepcopy(data)

    # 0.1.x had a top-level `rag` section.
    legacy_rag = data.pop("rag", None)
    if isinstance(legacy_rag, dict):
        profiles = data.setdefault("profiles", {})
        profiles["rag"] = _deep_merge(profiles.get("rag", {}), legacy_rag)

    # 0.1.x described one Confluence endpoint directly under `confluence`.
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
        }
        if any(cf.get(key) not in (None, "") for key in legacy_keys):
            instance = {key: cf.get(key) for key in legacy_keys if key in cf}
            instance.setdefault("base_path", "/wiki/")
            instance.setdefault("token_env", "ATLASSIAN_API_TOKEN")
            instance.setdefault("api_version", "v2")
            cf["instances"] = {"default": instance}
            cf["default_instance"] = cf.get("default_instance") or "default"
        for key in legacy_keys:
            cf.pop(key, None)
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
        return deepcopy(DEFAULT_CONFIG)
    with selected.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Configuration YAML invalide: {selected}")
    return _deep_merge(DEFAULT_CONFIG, _migrate_legacy(data))


def save_config(config: dict[str, Any], path: Path | None = None) -> Path:
    selected = selected_config_path(path)
    selected.parent.mkdir(parents=True, exist_ok=True)
    selected.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False, width=120),
        encoding="utf-8",
    )
    return selected


def init_config(path: Path | None = None, *, overwrite: bool = False) -> Path:
    selected = selected_config_path(path)
    if selected.exists() and not overwrite:
        return selected
    return save_config(deepcopy(DEFAULT_CONFIG), selected)


def confluence_instances(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    cf = config.get("confluence") or {}
    instances = cf.get("instances") or {}
    return {str(name): dict(value or {}) for name, value in instances.items()}


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
            "default_space": "",
            "root_page": "",
        },
        instances[selected],
    )
    # DocSpecBridge 0.2 targets Cloud only.
    instance["api_version"] = "v2"
    return selected, instance

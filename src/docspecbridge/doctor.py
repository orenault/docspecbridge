from __future__ import annotations

import os
import platform
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from . import __version__
from .config import confluence_instances
from .i18n import config_language


def package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "NOT INSTALLED"


def doctor_info(config: dict[str, Any], *, config_path: Path | None = None) -> dict[str, str]:
    app = config.get("app") or {}
    source = Path(str(app.get("source") or "./input"))
    destination = Path(str(app.get("destination") or "./output"))
    rag_destination = Path(str((config.get("rag_export") or {}).get("destination") or "./rag"))
    info = {
        "DocSpecBridge": __version__,
        "Language": config_language(config),
        "Config": str(config_path) if config_path else "auto/default",
        "Source": f"{source} / {'EXISTS' if source.exists() else 'MISSING'}",
        "Destination": f"{destination} / {'EXISTS' if destination.exists() else 'MISSING'}",
        "RAG corpus": f"{rag_destination} / {'EXISTS' if rag_destination.exists() else 'MISSING'}",
        "Python": sys.version.split()[0],
        "Platform": platform.platform(),
        "xberg": package_version("xberg"),
        "markdown-to-confluence": package_version("markdown-to-confluence"),
        "python-pptx": package_version("python-pptx"),
        "PyMuPDF": package_version("PyMuPDF"),
        "Pillow": package_version("Pillow"),
        "typer": package_version("typer"),
        "PyYAML": package_version("PyYAML"),
        "HTTP_PROXY": "SET" if os.getenv("HTTP_PROXY") or os.getenv("http_proxy") else "not set",
        "HTTPS_PROXY": "SET" if os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") else "not set",
    }

    instances = confluence_instances(config)
    if not instances:
        info["Confluence"] = "no instance configured"
    else:
        for name, instance in instances.items():
            env_name = str(instance.get("token_env") or "ATLASSIAN_API_TOKEN")
            token_status = "SET" if os.getenv(env_name) else "NOT SET"
            auth_type = str(instance.get("auth_type") or "classic")
            target = instance.get("domain") or "?"
            if auth_type == "scoped":
                target = f"api.atlassian.com/ex/confluence/{instance.get('cloud_id') or '?'}"
            info[f"Confluence[{name}]"] = f"{auth_type} / {target} / {env_name}={token_status}"

    return info

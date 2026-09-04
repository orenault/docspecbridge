from __future__ import annotations

import os
import platform
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from . import __version__
from .config import confluence_instances


def package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "NOT INSTALLED"


def doctor_info(config: dict[str, Any]) -> dict[str, str]:
    info = {
        "DocSpecBridge": __version__,
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
        info["Confluence"] = "aucune instance configurée"
    else:
        for name, instance in instances.items():
            env_name = str(instance.get("token_env") or "ATLASSIAN_API_TOKEN")
            target = instance.get("domain") or instance.get("api_url") or "?"
            token_status = "SET" if os.getenv(env_name) else "NOT SET"
            info[f"Confluence[{name}]"] = f"{target} / {env_name}={token_status}"

    return info

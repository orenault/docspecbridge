from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


def safe_stem(value: str) -> str:
    cleaned = _SAFE_NAME.sub("_", value).strip(" ._")
    return cleaned or "document"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if hasattr(value, "__dict__"):
        return {k: json_safe(v) for k, v in vars(value).items() if not k.startswith("_")}
    # Native extension objects may expose attributes without __dict__.
    result: dict[str, Any] = {}
    for name in dir(value):
        if name.startswith("_"):
            continue
        try:
            member = getattr(value, name)
        except Exception:
            continue
        if callable(member):
            continue
        if name in {"data_base64", "data", "bytes", "content"} and isinstance(member, (str, bytes)):
            result[name] = f"<payload:{len(member)}>"
        elif isinstance(member, (str, int, float, bool, type(None))):
            result[name] = member
    return result or str(value)

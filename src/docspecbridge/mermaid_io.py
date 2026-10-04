from __future__ import annotations

from pathlib import Path
from typing import Any


def materialize_mermaid_fallbacks(doc: dict[str, Any], package_dir: Path) -> list[str]:
    """Render canonical Mermaid blocks to portable PNG fallbacks.

    mermaidx is a regular Python dependency of DocSpecBridge. It embeds the Mermaid
    runtime and therefore does not require Node.js, npm, Chromium or a separate mmdc
    installation. The original Mermaid source stays canonical; PNG is only a target
    fallback for renderers that cannot interpret Mermaid directly.
    """
    blocks = [b for b in (doc.get("blocks") or []) if b.get("type") == "diagram" and b.get("mermaid")]
    if not blocks:
        return []

    try:
        import mermaidx  # type: ignore
    except Exception as exc:  # keep extraction usable if an unusual platform cannot load the renderer
        return [f"Mermaid image rendering unavailable: {exc}"]

    images_dir = package_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    assets = doc.setdefault("assets", [])
    warnings: list[str] = []

    for idx, block in enumerate(blocks, 1):
        relative = Path("images") / f"mermaid_{idx:03d}.png"
        target = package_dir / relative
        try:
            png = mermaidx.render(str(block.get("mermaid") or "")).png(background="white")
            target.write_bytes(png)
            rel = relative.as_posix()
            block["fallback_asset"] = rel
            if not any(str(item.get("file") or "") == rel for item in assets if isinstance(item, dict)):
                assets.append({
                    "role": "diagram",
                    "diagram_type": "mermaid",
                    "file": rel,
                    "saved": True,
                    "generated": True,
                })
        except Exception as exc:
            warnings.append(f"Mermaid diagram #{idx} could not be rendered as PNG: {exc}")
    return warnings

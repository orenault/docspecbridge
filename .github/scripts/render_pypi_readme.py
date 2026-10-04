from __future__ import annotations

import re
import sys
from pathlib import Path

import mermaidx


def main() -> None:
    if len(sys.argv) != 6:
        raise SystemExit("usage: render_pypi_readme.py README.md OUTPUT_DIR OWNER_REPO TAG ASSET_PREFIX")
    source = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])
    repository = sys.argv[3]
    tag = sys.argv[4]
    prefix = sys.argv[5].rstrip("/")
    output_dir.mkdir(parents=True, exist_ok=True)
    text = source.read_text(encoding="utf-8")
    pattern = re.compile(r"```mermaid\s*\n(?P<body>.*?)\n```", re.S | re.I)
    counter = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal counter
        counter += 1
        name = f"readme-mermaid-{counter:02d}.png"
        target = output_dir / name
        target.write_bytes(mermaidx.render(match.group("body")).png(background="white"))
        url = f"{prefix}/{repository}/releases/download/{tag}/{name}"
        return f"![Mermaid diagram {counter}]({url})"

    rendered = pattern.sub(replace, text)
    (output_dir / "README.md").write_text(rendered, encoding="utf-8")
    print(f"Rendered {counter} Mermaid diagram(s) for PyPI")


if __name__ == "__main__":
    main()

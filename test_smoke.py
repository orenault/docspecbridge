from pathlib import Path

from docspecbridge.config import get_confluence_instance, load_config
from docspecbridge.ooxml import inspect_ooxml
from docspecbridge.rag import build_rag_markdown, chunk_markdown


def test_default_config():
    cfg = load_config(None)
    assert ".docx" in cfg["app"]["extensions"]
    assert cfg["extract"]["images"]["extract_images"] is True
    assert cfg["profiles"]["publication"]["preserve_image_display_size"] is True
    assert cfg["profiles"]["rag"]["enabled"] is True


def test_non_ooxml():
    assert inspect_ooxml(Path("x.pdf")) is None


def test_rag_removes_header_only_image():
    md = "![](images/logo.png)\n\n# Title\n\nText\n\n![](images/figure.png)\n"
    assets = [
        {"file": "images/logo.png", "role": "header"},
        {"file": "images/figure.png", "role": "body"},
    ]
    result = build_rag_markdown(md, assets, {"include_header_images": False, "keep_image_references": True})
    assert "logo.png" not in result
    assert "figure.png" in result


def test_heading_aware_chunks():
    chunks = chunk_markdown("# A\n\nalpha\n\n## B\n\nbeta", max_characters=300, overlap=0)
    assert chunks
    assert chunks[-1]["heading_path"] == ["A", "B"]


def test_multiple_confluence_instances():
    cfg = load_config(None)
    cfg["confluence"]["instances"] = {
        "prod": {"domain": "prod.atlassian.net", "token_env": "PROD_PAT"},
        "sandbox": {"domain": "sandbox.atlassian.net", "token_env": "SB_PAT"},
    }
    cfg["confluence"]["default_instance"] = "prod"
    name, instance = get_confluence_instance(cfg)
    assert name == "prod"
    assert instance["domain"] == "prod.atlassian.net"

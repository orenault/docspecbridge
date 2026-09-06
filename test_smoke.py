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
    assert cfg["profiles"]["publication"]["table_of_contents"]["enabled"] == "auto"
    assert cfg["rag_export"]["destination"] == "./rag"


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
    assert chunks[-1]["embedding_text"].startswith("A > B")


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


def test_domain_normalization():
    from docspecbridge.config import normalize_domain

    assert normalize_domain("https://company.atlassian.net/") == "company.atlassian.net"
    assert normalize_domain("company.atlassian.net") == "company.atlassian.net"


def test_scoped_instance_url_migration():
    cfg = load_config(None)
    cfg["confluence"]["instances"] = {
        "prod": {
            "domain": "https://company.atlassian.net/",
            "auth_type": "scoped",
            "cloud_id": "abc-123",
            "user_name": "user@example.com",
        }
    }
    cfg["confluence"]["default_instance"] = "prod"
    _, instance = get_confluence_instance(cfg)
    assert instance["domain"] == "company.atlassian.net"
    assert instance["api_url"] == "https://api.atlassian.com/ex/confluence/abc-123"


def test_outline_replaces_source_toc_and_applies_levels():
    from docspecbridge.outline import apply_outline, publication_toc, rag_without_toc

    md = "Sommaire\n\n1. Introduction ..... 2\n1.1 Scope ..... 3\n\nIntroduction\n\nBody\n\nScope\n\nText\n"
    outline = [
        {"level": 1, "title": "Introduction", "source": "pdf_outline"},
        {"level": 2, "title": "Scope", "source": "pdf_outline"},
    ]
    result = apply_outline(md, outline)
    assert result.toc_detected is True
    assert "[[_TOC_]]" in publication_toc(result.markdown)
    assert "# Introduction" in result.markdown
    assert "## Scope" in result.markdown
    assert "Sommaire" not in rag_without_toc(result.markdown)


def test_rag_export_builds_global_corpus(tmp_path):
    import json
    from docspecbridge.rag_export import export_rag_corpus

    package = tmp_path / "out" / "doc__pdf"
    package.mkdir(parents=True)
    (package / "doc.rag.md").write_text("# Title\n\nText\n", encoding="utf-8")
    (package / "document.json").write_text("{}\n", encoding="utf-8")
    (package / "chunks.jsonl").write_text(json.dumps({"id": 1, "heading_path": ["Title"], "content": "Text", "source": {"file": "doc.pdf"}}) + "\n", encoding="utf-8")
    (package / "manifest.json").write_text(json.dumps({
        "source": {"sha256": "a" * 64, "original_path": "doc.pdf"},
        "outputs": {"rag_markdown": "doc.rag.md", "chunks": "chunks.jsonl", "document_json": "document.json"},
        "assets": {"images_directory": "images"},
        "outline": {"entries": []},
    }), encoding="utf-8")
    result = export_rag_corpus(tmp_path / "out", tmp_path / "rag")
    assert result.document_count == 1
    assert result.chunk_count == 1
    assert (tmp_path / "rag" / "corpus.json").is_file()
    assert (tmp_path / "rag" / "chunks.jsonl").is_file()


def test_palette_transparency_dhash_has_no_pillow_warning():
    import io
    import warnings
    from PIL import Image
    from docspecbridge.geometry import _dhash_bytes

    image = Image.new("P", (4, 4), 0)
    palette = [0, 0, 0, 255, 255, 255] + [0, 0, 0] * 254
    image.putpalette(palette)
    image.info["transparency"] = bytes([0, 255] + [255] * 254)
    payload = io.BytesIO()
    image.save(payload, format="PNG")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert _dhash_bytes(payload.getvalue()) is not None
    assert not [w for w in caught if "Palette images with Transparency" in str(w.message)]

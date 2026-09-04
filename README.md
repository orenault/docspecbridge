# DocSpecBridge 0.2.0b1 (Beta)

DocSpecBridge is a Python document ETL bridge:

```text
DOCX / PDF / PPTX
        |
        v
      Xberg
        |
        +--> normalized source assets + source geometry
        |
        +--> publication Markdown --> md2conf --> Confluence Cloud
        |
        +--> RAG Markdown + chunks.jsonl
```

The 0.2.0b1 release separates **publication fidelity** from **RAG efficiency**. Layout metadata is never injected into RAG text; it is stored in JSON and used to build publication-specific image variants.

## 1. Installation with uv

Python 3.14.7 is recommended for the current POC.

```powershell
cd C:\DEV\docspecbridge
uv venv --python 3.14.7
.\.venv\Scripts\Activate.ps1
uv sync
uv run docspecbridge doctor
```

`uv.lock` should be committed once generated. `.venv/` must stay in `.gitignore`.

## 2. First extraction

Interactive:

```powershell
uv run docspecbridge
```

CLI:

```powershell
uv run docspecbridge extract `
  --source "C:\MD\input" `
  --dest "C:\MD\output"
```

A package is created per source and the extension is part of the package name to avoid collisions:

```text
output/
├── specification__docx/
│   ├── specification.docx
│   ├── specification.md
│   ├── specification.rag.md
│   ├── specification.raw.md
│   ├── images/
│   ├── publication_images/
│   ├── manifest.json
│   ├── document.json
│   ├── rag.json
│   └── chunks.jsonl
└── specification__pdf/
    └── ...
```

### Files

- `*.md`: publication/human Markdown. Images can point to display-sized raster derivatives.
- `*.rag.md`: lean Markdown for RAG. No x/y/width/height metadata is written in the text.
- `*.raw.md`: raw Xberg Markdown for diagnostics/comparison.
- `images/`: canonical extracted images, kept at the extraction quality for RAG/multimodal use.
- `publication_images/`: display-size derivatives used by publication Markdown when source geometry is known.
- `manifest.json`: provenance, assets, geometry, fidelity warnings, output paths.
- `document.json`: structured technical metadata without binary payloads.
- `chunks.jsonl`: heading-aware RAG chunks.
- `rag.json`: portable descriptor for a future vector/RAG publisher.

## 3. Image size and geometry

DocSpecBridge 0.2.0b1 treats image display geometry as a cross-format concern:

- **PPTX**: picture shape `left/top/width/height` through `python-pptx`.
- **DOCX**: DrawingML/VML image extents from OOXML, including headers/footers where available.
- **PDF**: image rectangles through PyMuPDF.

The source geometry is stored in JSON, not in RAG Markdown.

For publication, DocSpecBridge can create a raster derivative close to the source display size. This prevents small PowerPoint icons from becoming giant images in Markdown/Confluence while keeping the original high-resolution image in `images/` for RAG.

This is still **best effort**. The same binary image can be reused at different sizes in a source; DocSpecBridge maps successive Markdown occurrences to successive source display occurrences where possible.

## 4. Vector content

DOCX/PPTX OOXML connectors, grouped shapes, charts, SmartArt/diagram markers and VML shapes are detected and reported in `manifest.json`.

PDF vector drawing operations are counted through PyMuPDF.

0.2.0b1 does **not** yet rasterize arbitrary vector groups automatically. When vector graphics are detected, the fidelity status is marked `partial` and a warning is emitted. This is the next fallback to implement after qualification on real documents.

## 5. Publication profile vs RAG profile

`docspecbridge.yaml` contains two independent profiles.

```yaml
profiles:
  publication:
    enabled: true
    preserve_image_display_size: true

  rag:
    enabled: true
    keep_image_references: true
    include_header_images: false
    include_footer_images: false
    chunking:
      enabled: true
      max_characters: 1600
      overlap: 150
```

The RAG profile is intentionally conservative: no rewriting/paraphrasing and no aggressive token reduction. Headings, lists, tables, constraints and image references are kept. Repetitive presentation artifacts can be excluded while the original information remains available in the source, raw Markdown and JSON metadata.

## 6. YAML configuration menu

0.1.x could **read** a YAML file but had no editor. 0.2.0b1 adds one.

```powershell
uv run docspecbridge config
```

or in the main menu:

```text
[5] Configuration YAML
```

The menu can edit:

- source/destination/extensions;
- recursive extraction;
- publication and RAG profile switches;
- chunk sizes;
- multiple Confluence Cloud instances;
- the default Confluence instance.

No PAT/token value is stored in YAML; only the environment variable name is stored.

## 7. Multiple Confluence Cloud instances

0.2.0b1 targets **Confluence Cloud only** and uses REST API v2.

Example:

```yaml
confluence:
  default_instance: "production"
  instances:
    production:
      domain: "company.atlassian.net"
      user_name: "user@example.com"
      token_env: "ATLASSIAN_API_TOKEN"
      default_space: "DOC"
      root_page: ""

    sandbox:
      domain: "company-sandbox.atlassian.net"
      user_name: "user@example.com"
      token_env: "ATLASSIAN_SANDBOX_API_TOKEN"
      default_space: "TEST"
      root_page: ""
```

Then:

```powershell
$env:ATLASSIAN_API_TOKEN="..."
$env:ATLASSIAN_SANDBOX_API_TOKEN="..."

uv run docspecbridge spaces --instance production
uv run docspecbridge spaces --instance sandbox
```

Publication:

```powershell
uv run docspecbridge publish `
  --instance production `
  --source ".\output\specification__docx" `
  --space DOC `
  --parent 123456789
```

The interactive publication menu first asks for the configured instance, then loads the spaces visible with that instance/token.

DocSpecBridge publishes the **publication Markdown**, never the RAG Markdown. Local publication images are uploaded by `markdown-to-confluence` as Confluence page attachments and displayed inline.

## 8. Configuration file

Copy the example:

```powershell
Copy-Item config.example.yaml docspecbridge.yaml
```

`docspecbridge.yaml` is intentionally ignored by Git because it contains local paths and account identifiers. `config.example.yaml` belongs in Git.

Legacy 0.1.x YAML with a single `confluence:` endpoint and a top-level `rag:` section is migrated in memory when loaded.

## 9. Useful commands

```powershell
uv run docspecbridge doctor
uv run docspecbridge config
uv run docspecbridge extract --source .\input --dest .\output
uv run docspecbridge spaces --instance production
uv run docspecbridge publish --instance production --source .\output\mydoc__docx --space DOC
```

## 10. Mermaid

The configuration keeps a `render_mermaid` switch because `markdown-to-confluence` already has Mermaid support. DocSpecBridge 0.2.0b1 does not yet attempt to transform legacy diagrams/images into Mermaid. That remains an enrichment stage for a later version.

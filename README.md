# DocSpecBridge 0.2.1

Document ETL bridge for **DOCX / PDF / PPTX → publication Markdown + RAG corpus → Confluence Cloud**.

DocSpecBridge keeps the source document and rich technical metadata, while deriving two different views:

- a **publication** view for humans / Confluence;
- a **RAG** view optimized for retrieval, without layout-only metadata in the indexed text.

## Main commands

```powershell
docspecbridge                 # interactive menu
docspecbridge config          # settings UI
docspecbridge extract         # documents -> self-contained packages
docspecbridge publish         # existing packages -> Confluence Cloud
docspecbridge doc2wiki        # extract + publish, file or batch
docspecbridge rag-export      # existing packages -> portable RAG corpus
docspecbridge doc2rag         # extract + portable RAG corpus
docspecbridge doctor
```

Without command-line overrides, `extract`, `publish`, `doc2wiki` and `doc2rag` use the YAML defaults. CLI options override YAML values for that run only.

Example:

```powershell
docspecbridge doc2wiki `
  --source C:\specs `
  --dest C:\work\docspecbridge `
  --instance production `
  --space ARCHI `
  --parent 123456789 `
  --recursive `
  -e docx -e pdf -e pptx
```

```powershell
docspecbridge doc2rag `
  --source C:\specs `
  --rag-dest C:\rag-corpus
```

## Interactive menu

```text
Paramétrage
Extract
Import Confluence
Doc2Wiki - Extract + Import Confluence
Doc2RAG - Extract + Export RAG
Aide
Quitter
```

Arrow keys select an item, **Enter** validates and **Esc** returns/cancels where supported.

## Package produced by `extract`

A source such as `specification.pdf` produces a collision-safe directory:

```text
output/
└── specification__pdf/
    ├── specification.pdf
    ├── specification.md
    ├── specification.rag.md
    ├── manifest.json
    ├── document.json
    ├── rag.json
    ├── chunks.jsonl
    ├── images/
    └── publication_images/
```

`*.raw.md` is disabled by default; it is only a diagnostic copy of the original Xberg Markdown.

## Document outline and table of contents

DocSpecBridge tries to preserve semantic heading levels from the strongest source available:

- DOCX: Word outline / Heading styles;
- PDF: PDF bookmark outline; if absent, an internal-link table of contents is detected and its indentation + destinations are used;
- PPTX: slide titles;
- fallback: headings already present in Markdown.

When a real source table of contents is detected, the publication profile replaces the duplicated TOC text with md2conf's native marker:

```markdown
[[_TOC_]]
```

md2conf turns that into a Confluence Table of Contents macro based on the reconstructed headings. The RAG profile removes the duplicated source TOC but retains the heading hierarchy in `heading_path` metadata.

## Image fidelity

Image files and their display geometry are kept separately:

- `images/`: original extracted assets for RAG / audit;
- `publication_images/`: display-size variants for publication;
- `manifest.json`: geometry, source positions, hashes, diagnostics and visual-fidelity warnings.

PPTX, DOCX and PDF image display sizes are recovered when the source format exposes them. Complex vector graphics are detected and reported; they are not silently treated as equivalent to raster images.

## RAG: export vs ingestion

`doc2rag` does **not** pretend that every RAG uses the same vector database or embedding model. It performs the format-neutral part of ingestion:

```text
source docs
   ↓
extract / normalize
   ↓
semantic headings + clean RAG Markdown
   ↓
heading-aware chunks + metadata
   ↓
portable RAG corpus
```

Default corpus structure:

```text
rag/
├── corpus.json
├── index.jsonl
├── chunks.jsonl
├── doc2rag-report.json
└── documents/
    └── <package>__<document-id>/
        ├── *.rag.md
        ├── manifest.json
        ├── document.json
        ├── rag.json
        └── images/
```

`chunks.jsonl` is the primary generic feed for a downstream loader. Each chunk contains:

- `content`: original chunk text;
- `heading_path`: semantic location in the document;
- `embedding_text`: heading breadcrumb + content, ready to embed;
- `source`: file/type/hash provenance;
- `document_id` in the aggregated corpus.

Actual vector-store ingestion (embedding model + upsert into a chosen store) is target-specific and is intentionally separate from the portable export.

## Confluence Cloud

Multiple Cloud instances can be configured. Tokens are never stored in YAML; only environment-variable names are stored.

Classic Atlassian API token:

```yaml
confluence:
  default_instance: production
  instances:
    production:
      domain: company.atlassian.net
      auth_type: classic
      user_name: user@example.com
      token_env: ATLASSIAN_API_TOKEN
      default_space: DOC
      root_page: "123456789"
```

Scoped token:

```yaml
    scoped-production:
      domain: company.atlassian.net
      auth_type: scoped
      user_name: user@example.com
      token_env: ATLASSIAN_SCOPED_API_TOKEN
      cloud_id: 00000000-0000-0000-0000-000000000000
```

The settings UI can add, modify/rename, remove and select instances, list spaces/root pages, and persist a default space / parent page.

### md2conf features enabled/exposed

DocSpecBridge currently uses/exposes:

- deterministic front-matter titles (avoids filename digest titles);
- inline local image upload;
- image/table layout settings;
- heading anchors;
- optional directory hierarchy in batch publication;
- page ID persistence in generated Markdown;
- manual-change overwrite protection;
- inline-comment policy (`remove` / `check-open`);
- native Confluence TOC via `[[_TOC_]]`;
- Mermaid rendering option.

## Configuration

See [`config.example.yaml`](config.example.yaml). Main defaults:

```yaml
app:
  source: ./input
  destination: ./output
  recursive: true

rag_export:
  destination: ./rag

profiles:
  publication:
    table_of_contents:
      enabled: auto
      replace_source_toc: true
  rag:
    chunking:
      enabled: true
      max_characters: 1600
      overlap: 150
      prepend_heading_context: true

confluence:
  keep_hierarchy: false
  overwrite_manual_changes: false
  comments: remove
  write_page_id_to_markdown: true
  heading_anchors: true
```

`input`, `output` and the configured RAG corpus directory are created automatically.

## Languages

Interactive UI: French, English, German, Spanish and Chinese (`fr`, `en`, `de`, `es`, `zh`). The OS language is detected on first configuration creation.

## Development

```powershell
uv sync
uv run pytest -q
uv run docspecbridge doctor
```

Build:

```powershell
uv build --no-sources
```

The repository workflow publishes tagged releases to PyPI after build, validation and a wheel smoke test.

# Changelog

All notable DocSpecBridge changes are documented here, with the most recent release first.

## 0.5.4

### Added
- Native semantic recognition of fenced Mermaid blocks in Markdown sources.
- Regression coverage for Mermaid preservation across canonical JSON, human Markdown, RAG Markdown, local HTML and Confluence publication Markdown.

### Changed
- Markdown Mermaid blocks now become canonical `diagram` blocks instead of generic `code_block` nodes.
- Confluence publication now receives Mermaid fences intact so `markdown-to-confluence` can apply the configured Mermaid rendering strategy.

### Fixed
- Mermaid diagrams from local Markdown sources are no longer flattened into ordinary code blocks before Confluence publication.

## 0.5.3

### Added
- Navigable workbook indexes for multi-sheet XLSX packages: root Markdown links to worksheet Markdown files and root HTML links to worksheet HTML files.
- Dynamic Confluence child-page listing on multi-sheet XLSX workbook pages using the publisher's child-page listing marker.
- XLSX workbook navigation text in all five supported UI languages.
- Regression coverage for local HTML files placed directly in the input directory.

### Changed
- A single-sheet XLSX workbook is now represented directly by the root package instead of creating an unnecessary workbook landing page plus one child page.
- Multi-sheet XLSX packages explicitly distinguish the workbook landing page from child worksheet packages in the manifest.
- HTML documentation now clarifies that extraction is semantic rather than a browser snapshot; JavaScript and CSS rendering are not executed by the current HTML adapter.

### Fixed
- Multi-sheet XLSX landing pages are now real tables of contents instead of plain worksheet-name lists.
- Local HTML workbook/navigation outputs no longer leave users without clickable navigation between generated pages.

## 0.5.2

### Added
- Versioned configuration metadata with `docspecbridge.config_schema_version` and `docspecbridge.last_updated_by`.
- Automatic sequential YAML migration with a pre-migration backup and persistence of the complete current configuration structure.
- Protection against modifying configuration files created with a newer unsupported schema.
- Transactional `Force Extract` mode in the interactive UI and `extract --force-extract` on the CLI.
- Force Extract preservation/migration of Confluence `publication_state.json` and Jira `jira_publication_state.json`.
- Strict Confluence replace safety: an existing publication state must resolve to its stored `page_id`; a missing or relocated page stops publication instead of falling back to page creation.
- Translation coverage tests for all five supported languages.
- This `CHANGELOG.md` file, linked from the README.

### Changed
- The README now describes the current product and usage only; release-specific history lives in this changelog.
- README and changelog content are English-only.
- Confluence publication state lookup prioritizes stable page identity over historical generated Markdown filenames.

### Fixed
- Re-extracting a previously published package can retain the correct Confluence page identity even when older releases used a different generated publication filename or the page title was edited manually.
- Jira Force Extract state updates the package fingerprint so resumed publication does not create an unintended new issue.

## 0.5.1

### Added
- Native XLSX discovery and extraction independent of YAML extension lists.
- Jira issue discovery path: direct issue key or browse project → issue type → paginated issue list.
- Jira attachment/image extraction and paginated comment extraction, including Jira Service Management fallback.
- Jira project/type defaults in interactive settings.
- `--version` and `-V` CLI options.

### Changed
- Supported source extensions became application capabilities instead of persistent configuration.
- Legacy `app.extensions` is ignored when reading older YAML and is not written back.
- Interactive Confluence and Jira discovery use configured defaults as preselected choices rather than bypassing selection.
- New/updated interactive menus are localized in English, French, German, Spanish and Chinese.

### Fixed
- Restored Confluence instance → space → page discovery after the menu reorganization.
- Restored Escape cancellation throughout interactive lists and text input.
- Fixed Python 3.14 Pillow packaging by requiring Pillow 12.x.
- Fixed XLSX files being filtered out by legacy configuration.
- Added Jira comment/attachment diagnostics and package output.

## 0.5.0

### Added
- Product navigation organized around Extract and Import directions.
- Confluence, Jira and Web as extraction sources.
- Jira as an import target using ADF descriptions.
- Source-specific package filenames instead of generic `document.*` output names.
- Native XLSX adapter with workbook/worksheet packaging, formula metadata and lightweight chart previews.
- Generic `--set dotted.path=value` configuration overrides.
- Jira partial-publication resume state.

### Changed
- Package readers became manifest-first while retaining legacy filename fallbacks.
- Confluence and Jira received symmetric discovery-oriented interactive workflows.

## 0.4.2

### Added
- Explicit Confluence publication modes: `replace` and `add`.
- Persistent Confluence destination identity in `publication_state.json`.
- Post-publication verification before reporting success.
- Editable title confirmation before interactive publication.

### Fixed
- Confluence page identity no longer needs to be written permanently into generated Markdown.
- Space-root selection uses the real Confluence homepage ID.

## 0.4.1

### Fixed
- Publication renderer remains at package root with stable local image paths.
- Confluence root-page selection and package publication compatibility improvements.

## 0.4.0

### Added
- CanonicalDocument as the structural source of truth.
- Independent human Markdown, RAG Markdown, HTML and Confluence renderings.
- HTML/Web source and destination support.
- Jira source/import foundations.
- DOCX semantic extraction using Mammoth and OOXML inspection alongside Xberg.
- Structural preservation for merged table cells and additional DOCX formatting metadata.

## 0.3.0

### Added
- Multi-instance Confluence Cloud configuration.
- Improved interactive navigation and localized application text.
- Confluence page-width, selector-depth and converter settings.
- Expanded DOCX publication fidelity and native Confluence TOC handling.

## 0.2.1

### Added
- Improved Confluence Cloud authentication/configuration and publication controls.
- More explicit diagnostics and configurable publication behavior.

## 0.2.0

### Added
- Confluence Cloud instance/authentication model and configuration UI foundations.
- RAG/export profile expansion and richer extraction diagnostics.

## 0.1.0

### Added
- Initial Python/uv MVP.
- DOCX, PDF and PPTX extraction through Xberg.
- Self-contained output packages with Markdown, images and metadata.
- Initial Confluence publication and RAG preparation workflow.

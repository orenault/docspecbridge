from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from . import __version__
from .config import (
    confluence_instances,
    ensure_workdirs,
    init_config,
    load_config,
    save_config,
    selected_config_path,
)
from .config_ui import choose_space_root, config_menu
from .confluence import (
    list_root_pages, list_spaces, publish, export_page_to_package,
    find_markdown_inputs, publication_title, suggest_add_title,
)
from .doctor import doctor_info
from .extractor import run_extract
from .rag_export import export_rag_corpus
from .utils import write_json, safe_stem
from .i18n import config_language, tr
from .ui import edit_text, select_option
from .jira import export_issue, create_issue_from_markdown
from .html_io import canonical_from_html_source, canonical_from_markdown
from .package_io import write_canonical_package
from .renderers import render_html

try:
    _HELP_CONFIG = load_config(None)
except Exception:
    _HELP_CONFIG = {"app": {"language": "auto"}}

def _h(key: str) -> str:
    return tr(_HELP_CONFIG, key)

app = typer.Typer(add_completion=False, no_args_is_help=False, help=_h("cli.app.help"))
console = Console()


def _cfg(config: Optional[Path]):
    cfg = load_config(config)
    ensure_workdirs(cfg)
    return cfg


def _apply_overwrite(cfg: dict, overwrite: bool) -> None:
    if overwrite:
        cfg["app"]["overwrite"] = True


def _parent_value(value: str | None) -> str | None:
    """CLI parent override: '-', 'none', 'home' force publication at space home."""
    if value is None:
        return None
    cleaned = value.strip()
    if cleaned.casefold() in {"-", "none", "null", "home", "root"}:
        return ""
    return cleaned


def _select_instance(cfg: dict, default: str | None = None) -> str:
    names = list(confluence_instances(cfg))
    if not names:
        raise RuntimeError(tr(cfg, "confluence.no_instance"))
    if len(names) == 1:
        return names[0]
    default_name = default or str(cfg.get("confluence", {}).get("default_instance") or names[0])
    default_idx = names.index(default_name) if default_name in names else 0
    selected = select_option(tr(cfg, "settings.instances"), [(name, name) for name in names], default_index=default_idx)
    if selected is None:
        raise RuntimeError(tr(cfg, "common.cancelled"))
    return selected


def _space_table(cfg: dict, spaces: list[dict]) -> None:
    table = Table(title=tr(cfg, "spaces.title"))
    table.add_column("#")
    table.add_column(tr(cfg, "common.key"))
    table.add_column(tr(cfg, "common.name"))
    table.add_column("ID")
    for idx, space in enumerate(spaces, 1):
        table.add_row(str(idx), str(space.get("key", "")), str(space.get("name", "")), str(space.get("id", "")))
    console.print(table)


def _root_table(cfg: dict, space: dict, pages: list[dict]) -> None:
    depth = int(((cfg.get("confluence") or {}).get("page_selector") or {}).get("max_depth", 0))
    table = Table(title=tr(cfg, "confluence.pages_title", space=space.get("key", ""), depth=depth))
    table.add_column("#")
    table.add_column(tr(cfg, "common.title"))
    table.add_column(tr(cfg, "common.level"))
    table.add_column("ID")
    for idx, page in enumerate(pages, 1):
        level = int(page.get("level") if page.get("level") is not None else (page.get("depth") or 0))
        table.add_row(str(idx), str(page.get("tree_label") or page.get("title", "")), str(level), str(page.get("id", "")))
    console.print(table)


@app.callback(invoke_without_command=True, help=_h("cli.main.help"))
def main(ctx: typer.Context) -> None:
    """Sans sous-commande, ouvre le menu interactif."""
    if ctx.invoked_subcommand is None:
        menu()


@app.command(help=_h("cli.extract.help"))
def extract(
    source: Annotated[Optional[Path], typer.Option("--source", "-s", help=_h("cli.opt.source"))] = None,
    dest: Annotated[Optional[Path], typer.Option("--dest", "-d", help=_h("cli.opt.dest"))] = None,
    recursive: Annotated[Optional[bool], typer.Option("--recursive/--no-recursive")] = None,
    extension: Annotated[Optional[list[str]], typer.Option("--extension", "-e")] = None,
    chunk_size: Annotated[Optional[int], typer.Option("--chunk-size")] = None,
    overlap: Annotated[Optional[int], typer.Option("--overlap")] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c", help=_h("cli.opt.config"))] = None,
    overwrite: Annotated[bool, typer.Option("--overwrite", help=_h("cli.opt.overwrite"))] = False,
) -> None:
    """Extrait DOCX/PDF/PPTX vers publication Markdown + RAG + images."""
    cfg = _runtime_config(
        _cfg(config), recursive=recursive, extensions=extension, overwrite=overwrite,
        chunk_size=chunk_size, overlap=overlap,
    )
    source = source or Path(cfg["app"]["source"])
    dest = dest or Path(cfg["app"]["destination"])
    outcomes = run_extract(cfg, source, dest)

    table = Table(title=tr(cfg, "extract.table_title"))
    table.add_column(tr(cfg, "common.source"))
    table.add_column(tr(cfg, "common.status"))
    table.add_column(tr(cfg, "common.images"), justify="right")
    table.add_column(tr(cfg, "common.package"))
    for item in outcomes:
        state = f"ERROR: {item.error}" if item.error else ("WARNING" if item.warnings else "OK")
        table.add_row(item.source.name, state, str(len(item.images)), str(item.package_dir))
        for warning in item.warnings:
            console.print(f"[yellow]  ! {item.source.name}: {warning}[/yellow]")
    console.print(table)
    if not outcomes:
        console.print(f"[yellow]{tr(cfg, 'extract.none')}[/yellow]")
    if any(item.error for item in outcomes):
        raise typer.Exit(2)


@app.command("spaces", help=_h("cli.spaces.help"))
def spaces_cmd(
    instance: Annotated[Optional[str], typer.Option("--instance", "-i", help=_h("cli.opt.instance"))] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
) -> None:
    """Liste les espaces Confluence Cloud visibles pour une instance."""
    cfg = _cfg(config)
    selected = instance or _select_instance(cfg)
    _space_table(cfg, list_spaces(cfg, selected))


@app.command("root-pages", help=_h("cli.root_pages.help"))
def root_pages_cmd(
    space_id: Annotated[str, typer.Option("--space-id", help=_h("cli.opt.space_id"))],
    instance: Annotated[Optional[str], typer.Option("--instance", "-i", help=_h("cli.opt.instance"))] = None,
    depth: Annotated[Optional[int], typer.Option("--depth", min=0, max=2, help=_h("cli.opt.depth"))] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c", help=_h("cli.opt.config"))] = None,
) -> None:
    """Liste les pages racines d'un espace Confluence Cloud."""
    cfg = _cfg(config)
    selected = instance or _select_instance(cfg)
    if depth is not None:
        cfg.setdefault("confluence", {}).setdefault("page_selector", {})["max_depth"] = depth
    pages = list_root_pages(cfg, selected, space_id, max_depth=depth)
    _root_table(cfg, {"key": space_id, "name": ""}, pages)


@app.command("publish", help=_h("cli.publish.help"))
def publish_cmd(
    source: Annotated[Optional[Path], typer.Option("--source", "-s", help=_h("cli.opt.publish_source"))] = None,
    instance: Annotated[Optional[str], typer.Option("--instance", "-i", help=_h("cli.opt.instance"))] = None,
    space: Annotated[Optional[str], typer.Option("--space", help=_h("cli.opt.space"))] = None,
    parent: Annotated[Optional[str], typer.Option("--parent", help=_h("cli.opt.parent"))] = None,
    mode: Annotated[Optional[str], typer.Option("--mode", help="replace | add")] = None,
    title: Annotated[Optional[str], typer.Option("--title", help="Titre de page Confluence (publication d'une seule page)")] = None,
    keep_hierarchy: Annotated[Optional[bool], typer.Option("--keep-hierarchy/--no-keep-hierarchy")] = None,
    overwrite_manual: Annotated[Optional[bool], typer.Option("--overwrite-manual/--protect-manual")] = None,
    comments: Annotated[Optional[str], typer.Option("--comments", help="remove | check-open")] = None,
    heading_anchors: Annotated[Optional[bool], typer.Option("--heading-anchors/--no-heading-anchors")] = None,
    write_page_id: Annotated[Optional[bool], typer.Option("--write-page-id/--no-write-page-id")] = None,
    image_max_width: Annotated[Optional[int], typer.Option("--image-max-width")] = None,
    table_mode: Annotated[Optional[str], typer.Option("--table-mode", help="responsive | fixed")] = None,
    page_width: Annotated[Optional[str], typer.Option("--page-width", help=_h("cli.opt.page_width"))] = None,
    render_mermaid: Annotated[Optional[bool], typer.Option("--render-mermaid/--no-render-mermaid")] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
) -> None:
    """Publie les Markdown publication dans Confluence Cloud avec leurs images inline."""
    cfg = _runtime_config(
        _cfg(config), keep_hierarchy=keep_hierarchy, comments=comments, heading_anchors=heading_anchors,
        write_page_id=write_page_id, image_max_width=image_max_width, table_mode=table_mode, page_width=page_width,
        render_mermaid=render_mermaid,
    )
    selected = instance or _select_instance(cfg)
    source = source or Path(cfg["app"]["destination"])
    parent = _parent_value(parent)
    started = time.perf_counter()
    try:
        with console.status(tr(cfg, "confluence.publish_progress", instance=selected), spinner="dots"):
            published = publish(
                cfg, source, space_key=space, root_page=parent, instance_name=selected,
                keep_hierarchy=keep_hierarchy, overwrite_manual_changes=overwrite_manual,
                mode=mode, title=title,
            )
    except Exception as exc:
        duration = time.perf_counter() - started
        console.print(f"[red]{tr(cfg, 'confluence.publish_failed', duration=duration, error=exc)}[/red]")
        raise
    duration = time.perf_counter() - started
    console.print(f"[green]{tr(cfg, 'confluence.publish_done', count=len(published), instance=selected, duration=duration)}[/green]")
    for item in published:
        console.print(f"[dim]{tr(cfg, 'publication.result', action=item.action, title=item.title, page_id=item.page_id)}[/dim]")


app.command("import-confluence")(publish_cmd)


def _runtime_config(
    cfg: dict,
    *,
    recursive: bool | None = None,
    extensions: list[str] | None = None,
    overwrite: bool = False,
    keep_hierarchy: bool | None = None,
    comments: str | None = None,
    heading_anchors: bool | None = None,
    write_page_id: bool | None = None,
    image_max_width: int | None = None,
    table_mode: str | None = None,
    page_width: str | None = None,
    render_mermaid: bool | None = None,
    chunk_size: int | None = None,
    overlap: int | None = None,
    copy_rag_assets: bool | None = None,
) -> dict:
    runtime = deepcopy(cfg)
    if recursive is not None:
        runtime["app"]["recursive"] = recursive
    if extensions:
        runtime["app"]["extensions"] = [e if e.startswith(".") else f".{e}" for e in extensions]
    if overwrite:
        runtime["app"]["overwrite"] = True
    cf = runtime.setdefault("confluence", {})
    if keep_hierarchy is not None:
        cf["keep_hierarchy"] = keep_hierarchy
    if comments is not None:
        if comments not in {"remove", "check-open"}:
            raise ValueError("--comments doit valoir remove ou check-open")
        cf["comments"] = comments
    if heading_anchors is not None:
        cf["heading_anchors"] = heading_anchors
    if write_page_id is not None:
        cf["write_page_id_to_markdown"] = write_page_id
    if image_max_width is not None:
        cf.setdefault("layout", {})["image_max_width"] = image_max_width
    if table_mode is not None:
        if table_mode not in {"responsive", "fixed"}:
            raise ValueError("--table-mode must be responsive or fixed")
        cf.setdefault("layout", {})["table_display_mode"] = table_mode
    if page_width is not None:
        allowed_widths = {"narrow", "wide", "max", "confluence-default"}
        if page_width not in allowed_widths:
            raise ValueError("--page-width must be narrow, wide, max or confluence-default")
        cf["page_width"] = page_width
    if render_mermaid is not None:
        cf["render_mermaid"] = render_mermaid
    chunking = runtime.setdefault("profiles", {}).setdefault("rag", {}).setdefault("chunking", {})
    if chunk_size is not None:
        chunking["max_characters"] = chunk_size
    if overlap is not None:
        chunking["overlap"] = overlap
    if copy_rag_assets is not None:
        runtime.setdefault("rag_export", {})["copy_assets"] = copy_rag_assets
    return runtime


def _batch_report(path: Path, kind: str, outcomes: list, **extra) -> None:
    report = {
        "kind": kind,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "documents": len(outcomes),
        "success": sum(1 for item in outcomes if not item.error),
        "failed": sum(1 for item in outcomes if item.error),
        "warnings": sum(len(item.warnings) for item in outcomes),
        "items": [
            {
                "source": str(item.source),
                "package": str(item.package_dir),
                "error": item.error,
                "warnings": item.warnings,
            }
            for item in outcomes
        ],
        **extra,
    }
    write_json(path, report)


@app.command("rag-export", help=_h("cli.rag_export.help"))
def rag_export_cmd(
    source: Annotated[Optional[Path], typer.Option("--source", "-s", help=_h("cli.opt.rag_source"))] = None,
    dest: Annotated[Optional[Path], typer.Option("--dest", "-d", help=_h("cli.opt.rag_dest"))] = None,
    copy_assets: Annotated[Optional[bool], typer.Option("--copy-assets/--no-copy-assets")] = None,
    overwrite: Annotated[Optional[bool], typer.Option("--overwrite/--no-overwrite")] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
) -> None:
    """Agrège les packages extraits en corpus RAG portable (sans vector-store spécifique)."""
    cfg = _runtime_config(_cfg(config), copy_rag_assets=copy_assets)
    source = source or Path(cfg["app"]["destination"])
    rag_cfg = cfg.get("rag_export") or {}
    dest = dest or Path(str(rag_cfg.get("destination") or "./rag"))
    result = export_rag_corpus(
        source, dest,
        copy_assets=bool(rag_cfg.get("copy_assets", True)),
        copy_document_json=bool(rag_cfg.get("copy_document_json", True)),
        overwrite=bool(rag_cfg.get("overwrite", True)) if overwrite is None else bool(overwrite),
    )
    console.print(f"[green]{tr(cfg, 'rag.export_done', documents=result.document_count, chunks=result.chunk_count, destination=result.destination)}[/green]")


@app.command("doc2wiki", help=_h("cli.doc2wiki.help"))
def doc2wiki_cmd(
    source: Annotated[Optional[Path], typer.Option("--source", "-s")] = None,
    dest: Annotated[Optional[Path], typer.Option("--dest", "-d", help=_h("cli.opt.work_dest"))] = None,
    instance: Annotated[Optional[str], typer.Option("--instance", "-i")] = None,
    space: Annotated[Optional[str], typer.Option("--space")] = None,
    parent: Annotated[Optional[str], typer.Option("--parent")] = None,
    recursive: Annotated[Optional[bool], typer.Option("--recursive/--no-recursive")] = None,
    extension: Annotated[Optional[list[str]], typer.Option("--extension", "-e", help="Extension répétable: -e docx -e pdf")] = None,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
    keep_hierarchy: Annotated[Optional[bool], typer.Option("--keep-hierarchy/--no-keep-hierarchy")] = None,
    overwrite_manual: Annotated[Optional[bool], typer.Option("--overwrite-manual/--protect-manual")] = None,
    comments: Annotated[Optional[str], typer.Option("--comments", help="remove | check-open")] = None,
    heading_anchors: Annotated[Optional[bool], typer.Option("--heading-anchors/--no-heading-anchors")] = None,
    write_page_id: Annotated[Optional[bool], typer.Option("--write-page-id/--no-write-page-id")] = None,
    image_max_width: Annotated[Optional[int], typer.Option("--image-max-width")] = None,
    table_mode: Annotated[Optional[str], typer.Option("--table-mode", help="responsive | fixed")] = None,
    page_width: Annotated[Optional[str], typer.Option("--page-width", help=_h("cli.opt.page_width"))] = None,
    render_mermaid: Annotated[Optional[bool], typer.Option("--render-mermaid/--no-render-mermaid")] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
) -> None:
    """Document(s) -> extraction -> Confluence. Sans option, utilise entièrement le YAML."""
    cfg = _runtime_config(
        _cfg(config), recursive=recursive, extensions=extension, overwrite=overwrite, keep_hierarchy=keep_hierarchy,
        comments=comments, heading_anchors=heading_anchors, write_page_id=write_page_id,
        image_max_width=image_max_width, table_mode=table_mode, page_width=page_width, render_mermaid=render_mermaid,
    )
    source = source or Path(cfg["app"]["source"])
    dest = dest or Path(cfg["app"]["destination"])
    outcomes = run_extract(cfg, source, dest)
    good = [item for item in outcomes if not item.error]
    if not good:
        _batch_report(dest / "doc2wiki-report.json", "doc2wiki", outcomes, published=0)
        raise typer.Exit(2)
    selected = instance or _select_instance(cfg)
    parent = _parent_value(parent)
    effective_hierarchy = bool(cfg.get("confluence", {}).get("keep_hierarchy", False)) if keep_hierarchy is None else bool(keep_hierarchy)
    published_count = 0
    started = time.perf_counter()
    try:
        with console.status(tr(cfg, "confluence.publish_progress", instance=selected), spinner="dots"):
            if effective_hierarchy:
                published_count = len(publish(
                    cfg, dest, space_key=space, root_page=parent, instance_name=selected,
                    keep_hierarchy=True, overwrite_manual_changes=overwrite_manual,
                ))
            else:
                for item in good:
                    published = publish(
                        cfg, item.package_dir, space_key=space, root_page=parent, instance_name=selected,
                        keep_hierarchy=False, overwrite_manual_changes=overwrite_manual,
                    )
                    published_count += len(published)
    except Exception as exc:
        duration = time.perf_counter() - started
        console.print(f"[red]{tr(cfg, 'confluence.publish_failed', duration=duration, error=exc)}[/red]")
        raise
    duration = time.perf_counter() - started
    report = dest / "doc2wiki-report.json"
    _batch_report(report, "doc2wiki", outcomes, published=published_count, instance=selected, space=space, parent=parent, duration_seconds=duration)
    console.print(f"[green]{tr(cfg, 'confluence.publish_done', count=published_count, instance=selected, duration=duration)}[/green]")
    console.print(f"[dim]report: {report}[/dim]")
    if any(item.error for item in outcomes):
        raise typer.Exit(2)


@app.command("doc2rag", help=_h("cli.doc2rag.help"))
def doc2rag_cmd(
    source: Annotated[Optional[Path], typer.Option("--source", "-s")] = None,
    dest: Annotated[Optional[Path], typer.Option("--dest", "-d", help=_h("cli.opt.work_dest"))] = None,
    rag_dest: Annotated[Optional[Path], typer.Option("--rag-dest", help=_h("cli.opt.rag_dest"))] = None,
    recursive: Annotated[Optional[bool], typer.Option("--recursive/--no-recursive")] = None,
    extension: Annotated[Optional[list[str]], typer.Option("--extension", "-e")] = None,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
    chunk_size: Annotated[Optional[int], typer.Option("--chunk-size")] = None,
    overlap: Annotated[Optional[int], typer.Option("--overlap")] = None,
    copy_assets: Annotated[Optional[bool], typer.Option("--copy-assets/--no-copy-assets")] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
) -> None:
    """Document(s) -> extraction -> corpus RAG portable. N'effectue pas d'embedding/vectorisation."""
    cfg = _runtime_config(
        _cfg(config), recursive=recursive, extensions=extension, overwrite=overwrite,
        chunk_size=chunk_size, overlap=overlap, copy_rag_assets=copy_assets,
    )
    source = source or Path(cfg["app"]["source"])
    dest = dest or Path(cfg["app"]["destination"])
    outcomes = run_extract(cfg, source, dest)
    good = [item for item in outcomes if not item.error]
    rag_cfg = cfg.get("rag_export") or {}
    if not good:
        rag_dest = rag_dest or Path(str(rag_cfg.get("destination") or "./rag"))
        rag_dest.mkdir(parents=True, exist_ok=True)
        _batch_report(rag_dest / "doc2rag-report.json", "doc2rag", outcomes, rag_documents=0, chunks=0)
        raise typer.Exit(2)
    rag_dest = rag_dest or Path(str(rag_cfg.get("destination") or "./rag"))
    result = export_rag_corpus(
        [item.package_dir for item in good], rag_dest,
        copy_assets=bool(rag_cfg.get("copy_assets", True)),
        copy_document_json=bool(rag_cfg.get("copy_document_json", True)),
        overwrite=bool(rag_cfg.get("overwrite", True)),
    )
    report = rag_dest / "doc2rag-report.json"
    _batch_report(report, "doc2rag", outcomes, rag_documents=result.document_count, chunks=result.chunk_count)
    console.print(f"[green]{tr(cfg, 'rag.doc2rag_done', documents=result.document_count, chunks=result.chunk_count, destination=rag_dest)}[/green]")
    if any(item.error for item in outcomes):
        raise typer.Exit(2)



@app.command("conf2md", help=_h("cli.conf2md.help"))
def conf2md_cmd(
    page_id: Annotated[str, typer.Option("--page-id", help=_h("cli.opt.page_id"))],
    dest: Annotated[Optional[Path], typer.Option("--dest", "-d", help=_h("cli.opt.dest"))] = None,
    instance: Annotated[Optional[str], typer.Option("--instance", "-i", help=_h("cli.opt.instance"))] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c", help=_h("cli.opt.config"))] = None,
) -> None:
    """Confluence Cloud page -> CanonicalDocument + readable Markdown/HTML/RAG."""
    cfg = _cfg(config)
    selected = instance or _select_instance(cfg)
    destination = dest or Path(cfg["app"]["destination"])
    package = export_page_to_package(cfg, page_id, destination, instance_name=selected)
    console.print(f"[green]{tr(cfg, 'confluence.export_done', package=package)}[/green]")


@app.command("jira2md", help=_h("cli.jira2md.help"))
def jira2md_cmd(
    issue: Annotated[str, typer.Option("--issue", help=_h("cli.opt.issue"))],
    dest: Annotated[Optional[Path], typer.Option("--dest", "-d", help=_h("cli.opt.dest"))] = None,
    instance: Annotated[Optional[str], typer.Option("--instance", "-i", help=_h("cli.opt.instance"))] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c", help=_h("cli.opt.config"))] = None,
) -> None:
    """Jira issue -> CanonicalDocument + readable Markdown/HTML/RAG."""
    cfg = _cfg(config)
    destination = dest or Path(cfg["app"]["destination"])
    package = export_issue(cfg, issue, destination, instance_name=instance)
    console.print(f"[green]{tr(cfg, 'jira.export_done', package=package)}[/green]")


@app.command("md2jira", help=_h("cli.md2jira.help"))
def md2jira_cmd(
    source: Annotated[Path, typer.Option("--source", "-s", help=_h("cli.opt.source"))],
    project: Annotated[str, typer.Option("--project", help=_h("cli.opt.project"))],
    issue_type: Annotated[str, typer.Option("--issue-type", help=_h("cli.opt.issue_type"))] = "Story",
    summary: Annotated[Optional[str], typer.Option("--summary", help=_h("cli.opt.summary"))] = None,
    parent: Annotated[Optional[str], typer.Option("--parent", help=_h("cli.opt.jira_parent"))] = None,
    instance: Annotated[Optional[str], typer.Option("--instance", "-i", help=_h("cli.opt.instance"))] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c", help=_h("cli.opt.config"))] = None,
) -> None:
    """Readable Markdown -> Jira issue using ADF."""
    cfg = _cfg(config)
    result = create_issue_from_markdown(
        cfg, source, project=project, issue_type=issue_type, summary=summary,
        parent=parent, instance_name=instance,
    )
    console.print(f"[green]{tr(cfg, 'jira.create_done', key=result.get('key', result.get('id', '')))}[/green]")


@app.command("web2md", help=_h("cli.web2md.help"))
def web2md_cmd(
    url: Annotated[str, typer.Option("--url", help=_h("cli.opt.url"))],
    dest: Annotated[Optional[Path], typer.Option("--dest", "-d", help=_h("cli.opt.dest"))] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c", help=_h("cli.opt.config"))] = None,
) -> None:
    """Web page -> CanonicalDocument + Markdown/HTML/RAG package."""
    from urllib.parse import urlparse
    cfg = _cfg(config)
    destination = dest or Path(cfg["app"]["destination"])
    parsed = urlparse(url)
    hint = safe_stem((Path(parsed.path).stem or parsed.hostname or "web-page"))
    package = destination / f"{hint}__html"
    if package.exists() and any(package.iterdir()) and cfg["app"].get("overwrite", False):
        import shutil
        shutil.rmtree(package)
    package.mkdir(parents=True, exist_ok=True)
    doc, warnings, _ = canonical_from_html_source(
        url, package_dir=package, fetch_config=(cfg.get("html") or {}).get("fetch") or {}
    )
    write_canonical_package(
        doc, package, stem=hint,
        rag_profile=((cfg.get("profiles") or {}).get("rag") or {}),
        publication_profile=((cfg.get("profiles") or {}).get("publication") or {}),
        warnings=warnings,
    )
    console.print(f"[green]{tr(cfg, 'html.export_done', package=package)}[/green]")


@app.command("html2md", help=_h("cli.html2md.help"))
def html2md_cmd(
    source: Annotated[Path, typer.Option("--source", "-s", help=_h("cli.opt.source"))],
    dest: Annotated[Optional[Path], typer.Option("--dest", "-d", help=_h("cli.opt.dest"))] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c", help=_h("cli.opt.config"))] = None,
    overwrite: Annotated[bool, typer.Option("--overwrite", help=_h("cli.opt.overwrite"))] = False,
) -> None:
    cfg = _cfg(config)
    cfg["app"]["overwrite"] = overwrite or bool(cfg["app"].get("overwrite", False))
    destination = dest or Path(cfg["app"]["destination"])
    outcomes = run_extract(cfg, source, destination)
    for item in outcomes:
        if item.error:
            console.print(f"[red]{item.error}[/red]")
        else:
            console.print(f"[green]{item.package_dir}[/green]")


@app.command("md2html", help=_h("cli.md2html.help"))
def md2html_cmd(
    source: Annotated[Path, typer.Option("--source", "-s", help=_h("cli.opt.source"))],
    output: Annotated[Optional[Path], typer.Option("--output", "-o", help=_h("cli.opt.output"))] = None,
) -> None:
    text = source.read_text(encoding="utf-8", errors="replace")
    doc = canonical_from_markdown(text, title=source.stem, source={"type": "markdown", "original_path": str(source.resolve())})
    target = output or source.with_suffix(".html")
    target.write_text(render_html(doc), encoding="utf-8")
    console.print(f"[green]{target}[/green]")

@app.command("config", help=_h("cli.config.help"))
def config_cmd(
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
) -> None:
    """Ouvre l'éditeur interactif du paramétrage DocSpecBridge."""
    config_menu(config)


@app.command(help=_h("cli.doctor.help"))
def doctor(
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
) -> None:
    """Affiche versions, proxy, répertoires et état des instances/token Confluence."""
    cfg = _cfg(config)
    table = Table(title="DocSpecBridge doctor")
    table.add_column(tr(cfg, "doctor.element"))
    table.add_column(tr(cfg, "doctor.value"))
    path = selected_config_path(config) if config or selected_config_path().exists() else None
    for key, value in doctor_info(cfg, config_path=path).items():
        table.add_row(key, value)
    console.print(table)


def _interactive_extract(cfg: dict) -> None:
    source = Path(Prompt.ask(tr(cfg, "interactive.source"), default=str(cfg["app"]["source"])))
    dest = Path(Prompt.ask(tr(cfg, "interactive.destination"), default=str(cfg["app"]["destination"])))
    outcomes = run_extract(cfg, source, dest)
    for item in outcomes:
        if item.error:
            console.print(f"[red]{tr(cfg, 'state.error')}[/red] {item.source}: {item.error}")
        else:
            console.print(f"[green]{tr(cfg, 'state.ok')}[/green] {item.source.name} -> {item.package_dir} ({tr(cfg, 'extract.image_count', count=len(item.images))})")
            for warning in item.warnings:
                console.print(f"[yellow]  ! {warning}[/yellow]")
    if not outcomes:
        console.print(f"[yellow]{tr(cfg, 'extract.no_document_dir', source=source)}[/yellow]")


def _interactive_import(cfg: dict) -> None:
    selected = _select_instance(cfg)
    source = Path(Prompt.ask(tr(cfg, "interactive.package"), default=str(cfg["app"]["destination"])))
    md_files = find_markdown_inputs(source)
    if not md_files:
        raise RuntimeError(tr(cfg, "confluence.no_publication_md", source=source))

    spaces = list_spaces(cfg, selected)
    if not spaces:
        raise RuntimeError(tr(cfg, "confluence.no_space"))
    default_space = str(confluence_instances(cfg)[selected].get("default_space") or "")
    default_idx = next((idx for idx, item in enumerate(spaces) if str(item.get("key")) == default_space), 0)
    space_id = select_option(
        tr(cfg, "interactive.space"),
        [(str(item.get("id")), f"{item.get('key', '')} - {item.get('name', '')}") for item in spaces],
        default_index=default_idx,
    )
    if space_id is None:
        return
    space_obj = next(item for item in spaces if str(item.get("id")) == space_id)
    space_key = str(space_obj.get("key"))

    publication_cfg = (cfg.get("confluence") or {}).get("publication") or {}
    configured_mode = str(publication_cfg.get("default_mode") or "replace")
    mode_values = ["replace", "add"]

    def choose_mode(current: str) -> str | None:
        return select_option(
            tr(cfg, "interactive.publication_mode"),
            [
                ("replace", tr(cfg, "publication.mode.replace")),
                ("add", tr(cfg, "publication.mode.add")),
            ],
            default_index=mode_values.index(current) if current in mode_values else 0,
        )

    mode = choose_mode(configured_mode)
    if mode is None:
        return

    pages = list_root_pages(cfg, selected, str(space_id))
    if not pages:
        raise RuntimeError(f"Aucune page racine trouvée pour l'espace {space_key}.")
    instance_cfg = confluence_instances(cfg)[selected]
    default_parent = str(instance_cfg.get("root_page") or space_obj.get("homepageId") or "")

    def choose_parent(current: str) -> str | None:
        default_parent_idx = next(
            (idx for idx, item in enumerate(pages) if str(item.get("id") or "") == current), 0
        )
        return select_option(
            tr(cfg, "interactive.parent_select"),
            [
                (
                    str(item.get("id") or ""),
                    f"{item.get('tree_label') or item.get('title') or ''} (ID {item.get('id') or ''})",
                )
                for item in pages
            ],
            default_index=default_parent_idx,
        )

    parent = choose_parent(default_parent)
    if parent is None:
        return

    title_override: str | None = None
    if len(md_files) == 1:
        suggested_title = publication_title(md_files[0], cfg)
        if mode == "add":
            suggested_title = suggest_add_title(
                cfg, instance_name=selected, space_id=str(space_id), requested=suggested_title
            )
        title_override = edit_text(tr(cfg, "interactive.page_title_edit"), suggested_title)
        if not title_override:
            raise RuntimeError("Le titre de page Confluence ne peut pas être vide.")

    while True:
        parent_obj = next((item for item in pages if str(item.get("id") or "") == str(parent)), None)
        parent_label = str((parent_obj or {}).get("tree_label") or (parent_obj or {}).get("title") or parent)
        console.print()
        summary = Table(title=tr(cfg, "interactive.publication_action"))
        summary.add_column(tr(cfg, "common.parameter"))
        summary.add_column(tr(cfg, "common.value"))
        summary.add_row("Instance", selected)
        summary.add_row(tr(cfg, "common.space"), f"{space_key} - {space_obj.get('name', '')}")
        summary.add_row("Mode", tr(cfg, f"publication.mode.{mode}"))
        summary.add_row("Parent", f"{parent_label} (ID {parent})")
        if title_override is not None:
            summary.add_row(tr(cfg, "common.title"), title_override)
        elif len(md_files) > 1:
            summary.add_row(tr(cfg, "common.title"), f"{len(md_files)} pages")
        console.print(summary)

        action = select_option(
            tr(cfg, "interactive.publication_action"),
            [
                ("publish", tr(cfg, "publication.action.publish")),
                ("title", tr(cfg, "publication.action.edit_title")),
                ("parent", tr(cfg, "publication.action.change_parent")),
                ("mode", tr(cfg, "publication.action.change_mode")),
                ("cancel", tr(cfg, "publication.action.cancel")),
            ],
            default_index=0,
        )
        if action in (None, "cancel"):
            return
        if action == "title":
            if len(md_files) != 1:
                console.print("[yellow]Le titre global n'est disponible que pour une publication d'une page.[/yellow]")
                continue
            title_override = edit_text(tr(cfg, "interactive.page_title_edit"), title_override or publication_title(md_files[0], cfg))
            continue
        if action == "parent":
            selected_parent = choose_parent(str(parent))
            if selected_parent is not None:
                parent = selected_parent
            continue
        if action == "mode":
            selected_mode = choose_mode(mode)
            if selected_mode is not None:
                mode = selected_mode
                if len(md_files) == 1 and mode == "add" and title_override:
                    title_override = suggest_add_title(
                        cfg, instance_name=selected, space_id=str(space_id), requested=title_override
                    )
            continue
        if action == "publish":
            break

    started = time.perf_counter()
    try:
        with console.status(tr(cfg, "confluence.publish_progress", instance=selected), spinner="dots"):
            published_pages = publish(
                cfg,
                source,
                space_key=space_key,
                root_page=str(parent),
                instance_name=selected,
                mode=mode,
                title=title_override,
            )
    except Exception as exc:
        duration = time.perf_counter() - started
        console.print(f"[red]{tr(cfg, 'confluence.publish_failed', duration=duration, error=exc)}[/red]")
        raise
    duration = time.perf_counter() - started
    console.print(
        f"[green]{tr(cfg, 'confluence.publish_done', count=len(published_pages), instance=selected, duration=duration)}[/green]"
    )
    for item in published_pages:
        console.print(
            f"[dim]{tr(cfg, 'publication.result', action=item.action, title=item.title, page_id=item.page_id)}[/dim]"
        )


def _interactive_spaces(cfg: dict) -> None:
    selected = _select_instance(cfg)
    _space_table(cfg, list_spaces(cfg, selected))


def _interactive_roots(cfg: dict) -> None:
    selected = _select_instance(cfg)
    changed = choose_space_root(cfg, selected, set_default=True)
    if changed:
        path = save_config(cfg)
        console.print(f"[green]{tr(cfg, 'confluence.saved_root', path=path)}[/green]")


def _confluence_menu(cfg: dict) -> None:
    while True:
        choice = select_option(
            tr(cfg, "main.confluence"),
            [
                ("import", tr(cfg, "confluence.import")),
                ("export", tr(cfg, "confluence.export")),
                ("spaces", tr(cfg, "confluence.spaces")),
                ("roots", tr(cfg, "confluence.roots")),
                ("back", tr(cfg, "confluence.back")),
            ],
        )
        if choice in (None, "back"):
            return
        try:
            if choice == "import":
                _interactive_import(cfg)
            elif choice == "export":
                selected = _select_instance(cfg)
                page_id = Prompt.ask(tr(cfg, "interactive.page_id")).strip()
                if page_id:
                    package = export_page_to_package(cfg, page_id, Path(cfg["app"]["destination"]), instance_name=selected)
                    console.print(f"[green]{tr(cfg, 'confluence.export_done', package=package)}[/green]")
            elif choice == "spaces":
                _interactive_spaces(cfg)
            elif choice == "roots":
                _interactive_roots(cfg)
        except Exception as exc:
            console.print(f"[red]{tr(cfg, 'common.error')}: {exc}[/red]")


def _interactive_doc2wiki(cfg: dict) -> None:
    source = Path(str(cfg["app"]["source"]))
    dest = Path(str(cfg["app"]["destination"]))
    selected = _select_instance(cfg)
    instance = confluence_instances(cfg)[selected]
    console.print(
        f"[cyan]Doc2Wiki[/cyan] source={source} -> work={dest} -> "
        f"{selected}/{instance.get('default_space') or '(space non défini)'} "
        f"parent={instance.get('root_page') or '(accueil espace)'}"
    )
    confirm = select_option(tr(cfg, "interactive.confirm"), [(True, tr(cfg, "common.yes")), (False, tr(cfg, "common.no"))], default_index=0)
    if confirm is not True:
        return
    outcomes = run_extract(cfg, source, dest)
    good = [item for item in outcomes if not item.error]
    started = time.perf_counter()
    try:
        with console.status(tr(cfg, "confluence.publish_progress", instance=selected), spinner="dots"):
            if bool(cfg.get("confluence", {}).get("keep_hierarchy", False)):
                published_count = len(publish(cfg, dest, instance_name=selected, keep_hierarchy=True))
            else:
                published_count = 0
                for item in good:
                    published_count += len(publish(cfg, item.package_dir, instance_name=selected, keep_hierarchy=False))
    except Exception as exc:
        duration = time.perf_counter() - started
        console.print(f"[red]{tr(cfg, 'confluence.publish_failed', duration=duration, error=exc)}[/red]")
        raise
    duration = time.perf_counter() - started
    _batch_report(dest / "doc2wiki-report.json", "doc2wiki", outcomes, published=published_count, instance=selected, duration_seconds=duration)
    console.print(f"[green]{tr(cfg, 'confluence.publish_done', count=published_count, instance=selected, duration=duration)}[/green]")


def _interactive_doc2rag(cfg: dict) -> None:
    source = Path(str(cfg["app"]["source"]))
    dest = Path(str(cfg["app"]["destination"]))
    rag_cfg = cfg.get("rag_export") or {}
    rag_dest = Path(str(rag_cfg.get("destination") or "./rag"))
    console.print(f"[cyan]Doc2RAG[/cyan] source={source} -> work={dest} -> corpus={rag_dest}")
    confirm = select_option(tr(cfg, "interactive.confirm"), [(True, tr(cfg, "common.yes")), (False, tr(cfg, "common.no"))], default_index=0)
    if confirm is not True:
        return
    outcomes = run_extract(cfg, source, dest)
    good = [item for item in outcomes if not item.error]
    result = export_rag_corpus(
        [item.package_dir for item in good], rag_dest,
        copy_assets=bool(rag_cfg.get("copy_assets", True)),
        copy_document_json=bool(rag_cfg.get("copy_document_json", True)),
        overwrite=bool(rag_cfg.get("overwrite", True)),
    )
    _batch_report(rag_dest / "doc2rag-report.json", "doc2rag", outcomes, rag_documents=result.document_count, chunks=result.chunk_count)
    console.print(f"[green]{tr(cfg, 'rag.doc2rag_done', documents=result.document_count, chunks=result.chunk_count, destination=rag_dest)}[/green]")



def _interactive_jira(cfg: dict) -> None:
    while True:
        choice = select_option(
            tr(cfg, "main.jira"),
            [
                ("export", tr(cfg, "jira.export")),
                ("create", tr(cfg, "jira.create")),
                ("back", tr(cfg, "common.back")),
            ],
        )
        if choice in (None, "back"):
            return
        if choice == "export":
            issue = Prompt.ask(tr(cfg, "interactive.issue")).strip()
            if issue:
                package = export_issue(cfg, issue, Path(cfg["app"]["destination"]))
                console.print(f"[green]{tr(cfg, 'jira.export_done', package=package)}[/green]")
        elif choice == "create":
            source = Path(Prompt.ask(tr(cfg, "interactive.markdown_source"))).expanduser()
            project = Prompt.ask(tr(cfg, "interactive.project")).strip()
            issue_type = Prompt.ask(tr(cfg, "interactive.issue_type"), default="Story").strip()
            summary = Prompt.ask(tr(cfg, "interactive.summary"), default="").strip() or None
            parent = Prompt.ask(tr(cfg, "interactive.jira_parent"), default="").strip() or None
            result = create_issue_from_markdown(
                cfg, source, project=project, issue_type=issue_type, summary=summary, parent=parent
            )
            console.print(f"[green]{tr(cfg, 'jira.create_done', key=result.get('key', result.get('id', '')))}[/green]")


def _interactive_web(cfg: dict) -> None:
    url = Prompt.ask(tr(cfg, "interactive.url")).strip()
    if not url:
        return
    from urllib.parse import urlparse
    parsed = urlparse(url)
    hint = safe_stem((Path(parsed.path).stem or parsed.hostname or "web-page"))
    package = Path(cfg["app"]["destination"]) / f"{hint}__html"
    package.mkdir(parents=True, exist_ok=True)
    doc, warnings, _ = canonical_from_html_source(
        url, package_dir=package, fetch_config=(cfg.get("html") or {}).get("fetch") or {}
    )
    write_canonical_package(
        doc, package, stem=hint,
        rag_profile=((cfg.get("profiles") or {}).get("rag") or {}),
        publication_profile=((cfg.get("profiles") or {}).get("publication") or {}),
        warnings=warnings,
    )
    console.print(f"[green]{tr(cfg, 'html.export_done', package=package)}[/green]")

def _help(cfg: dict) -> None:
    lang = config_language(cfg)
    text_by_lang = {
        "fr": """[bold]DocSpecBridge permet de :[/bold]

• Paramétrer la langue, les répertoires, les profils RAG/publication et plusieurs instances Confluence Cloud.
• Extraire DOCX, PDF et PPTX en package autonome : Markdown publication, RAG, chunks, JSON et images.
• Préserver autant que possible la taille d'affichage des images et diagnostiquer le vectoriel.
• Publier le Markdown dans Confluence Cloud avec images inline.
• Lister les espaces et pages racines Confluence et mémoriser un espace/page par défaut.
• Utiliser aussi les commandes CLI : extract, publish, doc2wiki, doc2rag, rag-export, spaces, root-pages, config, doctor.
• Doc2Wiki enchaîne extraction + publication ; Doc2RAG enchaîne extraction + export d'un corpus RAG portable.
• Les sommaires/outline fiables sont convertis en niveaux de titres et en TOC Confluence native lorsque possible.

Dans les listes : ↑/↓ pour naviguer, Entrée pour choisir, Esc pour revenir.""",
        "en": """[bold]DocSpecBridge can:[/bold]

• Configure language, working directories, RAG/publication profiles and multiple Confluence Cloud instances.
• Extract DOCX, PDF and PPTX into self-contained publication/RAG packages.
• Preserve image display size when possible and diagnose vector graphics.
• Publish Markdown to Confluence Cloud with inline images.
• List Confluence spaces/root pages and save defaults.
• Use CLI commands: extract, publish, doc2wiki, doc2rag, rag-export, spaces, root-pages, config, doctor.
• Doc2Wiki chains extraction + publishing; Doc2RAG chains extraction + portable RAG corpus export.

In lists: ↑/↓ navigate, Enter selects, Esc returns.""",
        "de": """[bold]DocSpecBridge:[/bold]

• Sprache, Verzeichnisse, RAG-/Publikationsprofile und mehrere Confluence-Cloud-Instanzen konfigurieren.
• DOCX, PDF und PPTX extrahieren.
• Bildanzeigegrößen erhalten und Vektorgrafiken diagnostizieren.
• Markdown mit Bildern nach Confluence Cloud veröffentlichen.
• Bereiche und Stammseiten auflisten und Standards speichern.

Listen: ↑/↓ navigieren, Enter auswählen, Esc zurück.""",
        "es": """[bold]DocSpecBridge permite:[/bold]

• Configurar idioma, directorios, perfiles RAG/publicación y varias instancias de Confluence Cloud.
• Extraer DOCX, PDF y PPTX.
• Conservar tamaños de visualización de imágenes y diagnosticar gráficos vectoriales.
• Publicar Markdown en Confluence Cloud con imágenes.
• Listar espacios y páginas raíz y guardar valores predeterminados.

Listas: ↑/↓ navegar, Enter seleccionar, Esc volver.""",
        "zh": """[bold]DocSpecBridge 功能：[/bold]

• 配置语言、目录、RAG/发布配置以及多个 Confluence Cloud 实例。
• 提取 DOCX、PDF、PPTX。
• 尽可能保留图片显示尺寸并诊断矢量图形。
• 将 Markdown 和内嵌图片发布到 Confluence Cloud。
• 列出空间和根页面并保存默认值。

列表中：↑/↓ 导航，Enter 选择，Esc 返回。""",
    }
    console.print(text_by_lang.get(lang, text_by_lang["en"]))


def menu() -> None:
    config_path = selected_config_path()
    if not config_path.exists():
        init_config(config_path)
    cfg = load_config(config_path)
    ensure_workdirs(cfg)

    console.print(f"\n[bold cyan]DocSpecBridge {__version__}[/bold cyan]")
    console.print(tr(cfg, "main.subtitle") + "\n")
    while True:
        cfg = load_config(config_path)  # pick up settings changes immediately
        choice = select_option(
            f"DocSpecBridge {__version__}",
            [
                ("settings", tr(cfg, "main.settings")),
                ("extract", tr(cfg, "main.extract")),
                ("confluence", tr(cfg, "main.confluence")),
                ("jira", tr(cfg, "main.jira")),
                ("web", tr(cfg, "main.web")),
                ("doc2wiki", tr(cfg, "main.doc2wiki")),
                ("doc2rag", tr(cfg, "main.doc2rag")),
                ("help", tr(cfg, "main.help")),
                ("quit", tr(cfg, "main.quit")),
            ],
        )
        if choice in (None, "quit"):
            return
        try:
            if choice == "settings":
                config_menu(config_path)
            elif choice == "extract":
                _interactive_extract(cfg)
            elif choice == "confluence":
                _confluence_menu(cfg)
            elif choice == "jira":
                _interactive_jira(cfg)
            elif choice == "web":
                _interactive_web(cfg)
            elif choice == "doc2wiki":
                _interactive_doc2wiki(cfg)
            elif choice == "doc2rag":
                _interactive_doc2rag(cfg)
            elif choice == "help":
                _help(cfg)
        except Exception as exc:
            console.print(f"[red]{tr(cfg, 'common.error')}: {exc}[/red]")
        console.print()

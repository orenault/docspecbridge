from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Annotated, Optional

import typer
import yaml
from rich.console import Console
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
from .ui import UserCancelled, edit_text, prompt_text, select_option
from .jira import export_issue, create_issue_from_markdown, list_projects, list_issue_types, list_issues, jira_instances
from .html_io import canonical_from_html_source, canonical_from_markdown
from .package_io import write_canonical_package
from .renderers import render_html

try:
    _HELP_CONFIG = load_config(None, auto_migrate=False)
except Exception:
    _HELP_CONFIG = {"app": {"language": "auto"}}

def _h(key: str) -> str:
    return tr(_HELP_CONFIG, key)


def _show_config_migration(cfg: dict) -> None:
    info = ((cfg.get("_runtime") or {}).get("config_migration") or {})
    if not info:
        return
    console.print(f"[yellow]{tr(cfg, 'config.migrated', old=info.get('from_schema'), new=info.get('to_schema'))}[/yellow]")
    console.print(f"[dim]{tr(cfg, 'config.backup_created', path=info.get('backup'))}[/dim]")

app = typer.Typer(add_completion=False, no_args_is_help=False, help=_h("cli.app.help"))
console = Console()
_CLI_SET_OVERRIDES: list[str] = []


def _parse_set_value(raw: str) -> tuple[str, Any]:
    if "=" not in raw:
        raise typer.BadParameter("--set expects dotted.path=value")
    key, value = raw.split("=", 1)
    key = key.strip()
    if not key:
        raise typer.BadParameter("--set key cannot be empty")
    try:
        parsed = yaml.safe_load(value)
    except Exception:
        parsed = value
    return key, parsed


def _apply_set_overrides(cfg: dict) -> dict:
    for raw in _CLI_SET_OVERRIDES:
        key, value = _parse_set_value(raw)
        cursor: dict = cfg
        parts = [part for part in key.split(".") if part]
        for part in parts[:-1]:
            child = cursor.get(part)
            if not isinstance(child, dict):
                child = {}
                cursor[part] = child
            cursor = child
        cursor[parts[-1]] = value
    return cfg


def _cfg(config: Optional[Path], *, apply_overrides: bool = True):
    cfg = load_config(config)
    _show_config_migration(cfg)
    if apply_overrides:
        _apply_set_overrides(cfg)
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
        console.print(f"[dim]{tr(cfg, 'interactive.instance')}: {names[0]}[/dim]")
        return names[0]
    default_name = default or str(cfg.get("confluence", {}).get("default_instance") or names[0])
    default_idx = names.index(default_name) if default_name in names else 0
    selected = select_option(tr(cfg, "settings.instances"), [(name, name) for name in names], default_index=default_idx)
    if selected is None:
        raise UserCancelled()
    return selected


def _select_jira_instance(cfg: dict, default: str | None = None) -> str:
    names = list(jira_instances(cfg))
    if not names:
        raise RuntimeError(tr(cfg, "jira.no_instance"))
    if len(names) == 1:
        console.print(f"[dim]{tr(cfg, 'interactive.instance')}: {names[0]}[/dim]")
        return names[0]
    default_name = default or str((cfg.get("jira") or {}).get("default_instance") or (cfg.get("confluence") or {}).get("default_instance") or names[0])
    default_idx = names.index(default_name) if default_name in names else 0
    selected = select_option(tr(cfg, "jira.settings.default_instance"), [(name, name) for name in names], default_index=default_idx)
    if selected is None:
        raise UserCancelled()
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


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"DocSpecBridge {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True, help=_h("cli.main.help"))
def main(
    ctx: typer.Context,
    version: Annotated[Optional[bool], typer.Option(
        "--version", "-V", callback=_version_callback, is_eager=True, help=_h("cli.opt.version")
    )] = None,
    set_value: Annotated[Optional[list[str]], typer.Option(
        "--set", help=_h("cli.opt.set")
    )] = None,
) -> None:
    """Without a subcommand, open the interactive menu. --set can override every YAML key."""
    global _CLI_SET_OVERRIDES
    _CLI_SET_OVERRIDES = list(set_value or [])
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
    force_extract: Annotated[bool, typer.Option("--force-extract", help=_h("cli.opt.force_extract"))] = False,
) -> None:
    """Extrait DOCX/PDF/PPTX vers publication Markdown + RAG + images."""
    cfg = _runtime_config(
        _cfg(config, apply_overrides=False), recursive=recursive, extensions=extension, overwrite=overwrite,
        force_extract=force_extract, chunk_size=chunk_size, overlap=overlap,
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
        _cfg(config, apply_overrides=False), keep_hierarchy=keep_hierarchy, comments=comments, heading_anchors=heading_anchors,
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
    force_extract: bool = False,
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
        runtime.setdefault("_runtime", {})["extensions"] = [e if e.startswith(".") else f".{e}" for e in extensions]
    if overwrite:
        runtime["app"]["overwrite"] = True
    if force_extract:
        runtime.setdefault("_runtime", {})["force_extract"] = True
        # Force Extract is its own transactional replacement mode. Do not delegate
        # deletion to the legacy overwrite path.
        runtime["app"]["overwrite"] = False
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
        cf.setdefault("converter", {})["render_mermaid"] = render_mermaid
    chunking = runtime.setdefault("profiles", {}).setdefault("rag", {}).setdefault("chunking", {})
    if chunk_size is not None:
        chunking["max_characters"] = chunk_size
    if overlap is not None:
        chunking["overlap"] = overlap
    if copy_rag_assets is not None:
        runtime.setdefault("rag_export", {})["copy_assets"] = copy_rag_assets
    return _apply_set_overrides(runtime)


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
    cfg = _runtime_config(_cfg(config, apply_overrides=False), copy_rag_assets=copy_assets)
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
        _cfg(config, apply_overrides=False), recursive=recursive, extensions=extension, overwrite=overwrite, keep_hierarchy=keep_hierarchy,
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
        _cfg(config, apply_overrides=False), recursive=recursive, extensions=extension, overwrite=overwrite,
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
    attachments: Annotated[Optional[str], typer.Option("--attachments", help="none | images | all")] = None,
    zip_package: Annotated[Optional[bool], typer.Option("--zip-package/--no-zip-package")] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c", help=_h("cli.opt.config"))] = None,
) -> None:
    """Confluence Cloud page -> CanonicalDocument + readable Markdown/HTML/RAG."""
    cfg = _cfg(config)
    selected = instance or _select_instance(cfg)
    destination = dest or Path(cfg["app"]["destination"])
    package = export_page_to_package(cfg, page_id, destination, instance_name=selected, attachment_mode=attachments, zip_package=zip_package)
    console.print(f"[green]{tr(cfg, 'confluence.export_done', package=package)}[/green]")


app.command("extract-confluence")(conf2md_cmd)


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


app.command("extract-jira")(jira2md_cmd)


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


app.command("import-jira")(md2jira_cmd)


@app.command("jira-projects")
def jira_projects_cmd(
    query: Annotated[Optional[str], typer.Option("--query", "-q", help="Filter project key/name")] = None,
    instance: Annotated[Optional[str], typer.Option("--instance", "-i")] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
) -> None:
    """List/search Jira projects visible to the configured account."""
    cfg = _cfg(config)
    rows = list_projects(cfg, instance, query=query)
    table = Table(title="Jira projects")
    table.add_column("Key"); table.add_column("Name"); table.add_column("ID")
    for row in rows:
        table.add_row(str(row.get("key") or ""), str(row.get("name") or ""), str(row.get("id") or ""))
    console.print(table)


@app.command("jira-issues")
def jira_issues_cmd(
    project: Annotated[str, typer.Option("--project", "-p")],
    issue_type: Annotated[Optional[str], typer.Option("--type", "-t", help="Filter by Jira issue type")] = None,
    query: Annotated[Optional[str], typer.Option("--query", "-q", help="Filter issue key/summary/text")] = None,
    limit: Annotated[int, typer.Option("--limit", "-l", min=1, max=100)] = 50,
    instance: Annotated[Optional[str], typer.Option("--instance", "-i")] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
) -> None:
    """List the first discovery page of Jira issues for a project."""
    cfg = _cfg(config)
    page = list_issues(cfg, project, instance, issue_type=issue_type, query=query, max_results=limit)
    rows = page.get("issues") or []
    table = Table(title=tr(cfg, "jira.discovery.title", project=project, count=len(rows)))
    table.add_column("Key"); table.add_column(tr(cfg, "common.title")); table.add_column("Type"); table.add_column("Status"); table.add_column("Updated")
    for row in rows:
        fields = row.get("fields") or {}
        table.add_row(
            str(row.get("key") or ""), str(fields.get("summary") or ""),
            str(((fields.get("issuetype") or {}).get("name") if isinstance(fields.get("issuetype"), dict) else "") or ""),
            str(((fields.get("status") or {}).get("name") if isinstance(fields.get("status"), dict) else "") or ""),
            str(fields.get("updated") or ""),
        )
    console.print(table)
    if page.get("next_page_token"):
        console.print(f"[dim]{tr(cfg, 'jira.discovery.more_available')}[/dim]")


@app.command("jira-issue-types")
def jira_issue_types_cmd(
    project: Annotated[str, typer.Option("--project", "-p")],
    instance: Annotated[Optional[str], typer.Option("--instance", "-i")] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
) -> None:
    """List issue types available in a selected Jira project."""
    cfg = _cfg(config)
    rows = list_issue_types(cfg, project, instance)
    table = Table(title=f"Jira issue types - {project}")
    table.add_column("Name"); table.add_column("ID"); table.add_column("Subtask")
    for row in rows:
        table.add_row(str(row.get("name") or ""), str(row.get("id") or ""), str(bool(row.get("subtask"))))
    console.print(table)


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
    cfg = _cfg(config, apply_overrides=False)
    cfg["app"]["overwrite"] = overwrite or bool(cfg["app"].get("overwrite", False))
    _apply_set_overrides(cfg)
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


@app.command("config-keys")
def config_keys_cmd(
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
) -> None:
    """List every effective YAML key that can be overridden with --set."""
    cfg = _cfg(config)
    table = Table(title="DocSpecBridge configuration keys (--set dotted.path=value)")
    table.add_column("Key"); table.add_column("Current value")
    def walk(value, prefix=""):
        if isinstance(value, dict):
            for key in sorted(value):
                yield from walk(value[key], f"{prefix}.{key}" if prefix else str(key))
        else:
            yield prefix, value
    for key, value in walk(cfg):
        table.add_row(key, json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value)
    console.print(table)


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


def _cancelled(cfg: dict) -> None:
    console.print(f"[dim]{tr(cfg, 'interactive.cancelled')}[/dim]")


def _interactive_extract(cfg: dict, *, force_extract: bool = False) -> None:
    source = Path(prompt_text(tr(cfg, "interactive.source"), str(cfg["app"]["source"])))
    dest = Path(prompt_text(tr(cfg, "interactive.destination"), str(cfg["app"]["destination"])))
    runtime_cfg = deepcopy(cfg)
    if force_extract:
        runtime_cfg.setdefault("_runtime", {})["force_extract"] = True
        runtime_cfg["app"]["overwrite"] = False
        console.print(f"[yellow]{tr(cfg, 'extract.force_notice')}[/yellow]")
    outcomes = run_extract(runtime_cfg, source, dest)
    for item in outcomes:
        if item.error:
            console.print(f"[red]{tr(cfg, 'state.error')}[/red] {item.source}: {item.error}")
        else:
            console.print(f"[green]{tr(cfg, 'state.ok')}[/green] {item.source.name} -> {item.package_dir} ({tr(cfg, 'extract.image_count', count=len(item.images))})")
            for warning in item.warnings:
                console.print(f"[yellow]  ! {warning}[/yellow]")
    if not outcomes:
        console.print(f"[yellow]{tr(cfg, 'extract.no_document_dir', source=source)}[/yellow]")


def _jira_defaults(cfg: dict, instance_name: str) -> dict:
    jira = cfg.setdefault("jira", {})
    defaults = jira.setdefault("defaults", {})
    value = defaults.setdefault(instance_name, {})
    return value if isinstance(value, dict) else {}


def _select_confluence_location(cfg: dict, instance_name: str) -> tuple[dict, dict, list[dict]]:
    """Always discover and display Confluence space/page choices.

    Configured defaults are used only to pre-position the selector; they never bypass
    discovery or hide the selected location from the user.
    """
    spaces = list_spaces(cfg, instance_name)
    if not spaces:
        raise RuntimeError(tr(cfg, "confluence.no_space"))
    instance_cfg = confluence_instances(cfg)[instance_name]
    default_space = str(instance_cfg.get("default_space") or "")
    if default_space:
        console.print(f"[dim]{tr(cfg, 'confluence.current_default', value=default_space)}[/dim]")
    default_idx = next((idx for idx, item in enumerate(spaces) if str(item.get("key") or "") == default_space), 0)
    space_id = select_option(
        tr(cfg, "interactive.space_select"),
        [(str(item.get("id") or ""), f"{item.get('key', '')} - {item.get('name', '')}") for item in spaces],
        default_index=default_idx,
    )
    if space_id is None:
        raise UserCancelled()
    space_obj = next(item for item in spaces if str(item.get("id") or "") == str(space_id))

    max_depth = int(((cfg.get("confluence") or {}).get("page_selector") or {}).get("max_depth", 0))
    pages = list_root_pages(cfg, instance_name, str(space_id), max_depth=max_depth)
    if not pages:
        raise RuntimeError(tr(cfg, "confluence.no_pages"))

    same_default_space = str(space_obj.get("key") or "") == default_space
    default_page = str(instance_cfg.get("root_page") or "") if same_default_space else ""
    default_page = default_page or str(space_obj.get("homepageId") or "")
    if default_page:
        default_obj = next((p for p in pages if str(p.get("id") or "") == default_page), None)
        default_label = str((default_obj or {}).get("tree_label") or (default_obj or {}).get("title") or default_page)
        console.print(f"[dim]{tr(cfg, 'confluence.current_default', value=default_label)}[/dim]")
    default_page_idx = next((idx for idx, item in enumerate(pages) if str(item.get("id") or "") == default_page), 0)
    page_id = select_option(
        tr(cfg, "interactive.page_select"),
        [
            (
                str(item.get("id") or ""),
                f"{item.get('tree_label') or item.get('title') or ''} (ID {item.get('id') or ''})",
            )
            for item in pages
        ],
        default_index=default_page_idx,
    )
    if page_id is None:
        raise UserCancelled()
    page_obj = next(item for item in pages if str(item.get("id") or "") == str(page_id))
    return space_obj, page_obj, pages


def _select_jira_project(cfg: dict, instance_name: str, *, ask_filter: bool = False) -> dict:
    query = None
    if ask_filter:
        query = prompt_text(tr(cfg, "interactive.project_filter"), "").strip() or None
    projects = list_projects(cfg, instance_name, query=query)
    if not projects:
        raise RuntimeError(tr(cfg, "jira.no_project"))
    defaults = _jira_defaults(cfg, instance_name)
    default_project = str(defaults.get("project") or "")
    if default_project:
        console.print(f"[dim]{tr(cfg, 'jira.current_default', value=default_project)}[/dim]")
    default_idx = next(
        (idx for idx, row in enumerate(projects) if str(row.get("key") or row.get("id") or "") == default_project),
        0,
    )
    project_key = select_option(
        tr(cfg, "interactive.project_select"),
        [
            (str(row.get("key") or row.get("id") or ""), f"{row.get('key', '')} — {row.get('name', '')}")
            for row in projects
        ],
        default_index=default_idx,
    )
    if project_key is None:
        raise UserCancelled()
    return next(row for row in projects if str(row.get("key") or row.get("id") or "") == str(project_key))


def _select_jira_issue_type(cfg: dict, instance_name: str, project_key: str) -> str:
    types = list_issue_types(cfg, project_key, instance_name)
    if not types:
        raise RuntimeError(tr(cfg, "jira.no_issue_type", project=project_key))
    defaults = _jira_defaults(cfg, instance_name)
    default_type = str(defaults.get("issue_type") or "") if str(defaults.get("project") or "") == project_key else ""
    if default_type:
        console.print(f"[dim]{tr(cfg, 'jira.current_default', value=default_type)}[/dim]")
    default_idx = next(
        (idx for idx, row in enumerate(types) if str(row.get("name") or row.get("id") or "") == default_type),
        0,
    )
    issue_type = select_option(
        tr(cfg, "interactive.issue_type_select"),
        [(str(row.get("name") or row.get("id") or ""), str(row.get("name") or row.get("id") or "")) for row in types],
        default_index=default_idx,
    )
    if issue_type is None:
        raise UserCancelled()
    return str(issue_type)



def _jira_issue_label(issue: dict) -> str:
    fields = issue.get("fields") or {}
    key = str(issue.get("key") or issue.get("id") or "")
    summary = str(fields.get("summary") or "")
    status = str(((fields.get("status") or {}).get("name") if isinstance(fields.get("status"), dict) else "") or "")
    issue_type = str(((fields.get("issuetype") or {}).get("name") if isinstance(fields.get("issuetype"), dict) else "") or "")
    updated = str(fields.get("updated") or "")[:16].replace("T", " ")
    details = " · ".join(x for x in (issue_type, status, updated) if x)
    return f"{key} — {summary}" + (f" [{details}]" if details else "")


def _select_jira_issue(cfg: dict, instance_name: str, project_key: str, issue_type: str) -> str:
    discovery = (cfg.get("jira") or {}).get("discovery") or {}
    page_size = max(1, min(int(discovery.get("page_size", 50)), 100))
    query: str | None = None
    next_token: str | None = None
    while True:
        page = list_issues(
            cfg, project_key, instance_name, issue_type=issue_type, query=query,
            next_page_token=next_token, max_results=page_size,
        )
        issues = page.get("issues") or []
        options: list[tuple[object, str]] = [
            (("issue", str(row.get("key") or row.get("id") or "")), _jira_issue_label(row))
            for row in issues
        ]
        options.append((("search", None), tr(cfg, "jira.discovery.search")))
        if query:
            options.append((("reset", None), tr(cfg, "jira.discovery.reset")))
        if page.get("next_page_token"):
            options.append((("next", str(page.get("next_page_token"))), tr(cfg, "jira.discovery.next")))
        options.append((("back", None), tr(cfg, "common.back")))
        title = tr(cfg, "jira.discovery.title_typed", project=project_key, issue_type=issue_type, count=len(issues))
        choice = select_option(title, options)
        if choice is None or choice[0] == "back":
            raise UserCancelled()
        action, value = choice
        if action == "issue":
            return str(value)
        if action == "search":
            query = prompt_text(tr(cfg, "jira.discovery.search_prompt"), query or "").strip() or None
            next_token = None
        elif action == "reset":
            query = None
            next_token = None
        elif action == "next":
            next_token = str(value or "") or None


def _interactive_import(cfg: dict) -> None:
    selected = _select_instance(cfg)
    source = Path(prompt_text(tr(cfg, "interactive.package"), str(cfg["app"]["destination"])))
    md_files = find_markdown_inputs(source)
    if not md_files:
        raise RuntimeError(tr(cfg, "confluence.no_publication_md", source=source))

    space_obj, parent_obj, pages = _select_confluence_location(cfg, selected)
    space_id = str(space_obj.get("id") or "")
    space_key = str(space_obj.get("key") or "")
    parent = str(parent_obj.get("id") or "")

    publication_cfg = (cfg.get("confluence") or {}).get("publication") or {}
    configured_mode = str(publication_cfg.get("default_mode") or "replace")
    mode_values = ["replace", "add"]

    def choose_mode(current: str) -> str:
        value = select_option(
            tr(cfg, "interactive.publication_mode"),
            [
                ("replace", tr(cfg, "publication.mode.replace")),
                ("add", tr(cfg, "publication.mode.add")),
            ],
            default_index=mode_values.index(current) if current in mode_values else 0,
        )
        if value is None:
            raise UserCancelled()
        return str(value)

    mode = choose_mode(configured_mode)

    def choose_parent(current: str) -> str:
        default_parent_idx = next(
            (idx for idx, item in enumerate(pages) if str(item.get("id") or "") == current), 0
        )
        value = select_option(
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
        if value is None:
            raise UserCancelled()
        return str(value)

    title_override: str | None = None
    if len(md_files) == 1:
        suggested_title = publication_title(md_files[0], cfg)
        if mode == "add":
            suggested_title = suggest_add_title(
                cfg, instance_name=selected, space_id=space_id, requested=suggested_title
            )
        title_override = edit_text(tr(cfg, "interactive.page_title_edit"), suggested_title)
        if not title_override:
            raise RuntimeError(tr(cfg, "confluence.empty_title"))

    while True:
        parent_obj = next((item for item in pages if str(item.get("id") or "") == str(parent)), None)
        parent_label = str((parent_obj or {}).get("tree_label") or (parent_obj or {}).get("title") or parent)
        console.print()
        summary = Table(title=tr(cfg, "interactive.publication_action"))
        summary.add_column(tr(cfg, "common.parameter"))
        summary.add_column(tr(cfg, "common.value"))
        summary.add_row(tr(cfg, "interactive.instance"), selected)
        summary.add_row(tr(cfg, "common.space"), f"{space_key} - {space_obj.get('name', '')}")
        summary.add_row(tr(cfg, "interactive.publication_mode"), tr(cfg, f"publication.mode.{mode}"))
        summary.add_row(tr(cfg, "interactive.parent_select"), f"{parent_label} (ID {parent})")
        if title_override is not None:
            summary.add_row(tr(cfg, "common.title"), title_override)
        elif len(md_files) > 1:
            summary.add_row(tr(cfg, "common.title"), str(len(md_files)))
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
            raise UserCancelled()
        if action == "title":
            if len(md_files) != 1:
                console.print(f"[yellow]{tr(cfg, 'confluence.global_title_single')}[/yellow]")
                continue
            title_override = edit_text(tr(cfg, "interactive.page_title_edit"), title_override or publication_title(md_files[0], cfg))
            continue
        if action == "parent":
            parent = choose_parent(str(parent))
            continue
        if action == "mode":
            mode = choose_mode(mode)
            if len(md_files) == 1 and mode == "add" and title_override:
                title_override = suggest_add_title(
                    cfg, instance_name=selected, space_id=space_id, requested=title_override
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
                _interactive_extract_confluence(cfg)
            elif choice == "spaces":
                _interactive_spaces(cfg)
            elif choice == "roots":
                _interactive_roots(cfg)
        except UserCancelled:
            _cancelled(cfg)
        except Exception as exc:
            console.print(f"[red]{tr(cfg, 'common.error')}: {exc}[/red]")


def _interactive_doc2wiki(cfg: dict) -> None:
    source = Path(str(cfg["app"]["source"]))
    dest = Path(str(cfg["app"]["destination"]))
    selected = _select_instance(cfg)
    space_obj, parent_obj, _ = _select_confluence_location(cfg, selected)
    space_key = str(space_obj.get("key") or "")
    parent = str(parent_obj.get("id") or "")
    console.print(
        f"[cyan]Doc2Wiki[/cyan] {tr(cfg, 'interactive.source')}={source} -> "
        f"{tr(cfg, 'interactive.destination')}={dest} -> {selected}/{space_key} parent={parent}"
    )
    confirm = select_option(tr(cfg, "interactive.confirm"), [(True, tr(cfg, "common.yes")), (False, tr(cfg, "common.no"))], default_index=0)
    if confirm is not True:
        raise UserCancelled()
    outcomes = run_extract(cfg, source, dest)
    good = [item for item in outcomes if not item.error]
    started = time.perf_counter()
    try:
        with console.status(tr(cfg, "confluence.publish_progress", instance=selected), spinner="dots"):
            if bool(cfg.get("confluence", {}).get("keep_hierarchy", False)):
                published_count = len(publish(cfg, dest, instance_name=selected, space_key=space_key, root_page=parent, keep_hierarchy=True))
            else:
                published_count = 0
                for item in good:
                    published_count += len(publish(cfg, item.package_dir, instance_name=selected, space_key=space_key, root_page=parent, keep_hierarchy=False))
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
    console.print(f"[cyan]Doc2RAG[/cyan] {tr(cfg, 'interactive.source')}={source} -> {tr(cfg, 'interactive.destination')}={dest} -> {rag_dest}")
    confirm = select_option(tr(cfg, "interactive.confirm"), [(True, tr(cfg, "common.yes")), (False, tr(cfg, "common.no"))], default_index=0)
    if confirm is not True:
        raise UserCancelled()
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
        try:
            if choice == "export":
                _interactive_extract_jira(cfg)
            elif choice == "create":
                _interactive_import_jira(cfg)
        except UserCancelled:
            _cancelled(cfg)


def _interactive_web(cfg: dict) -> None:
    url = prompt_text(tr(cfg, "interactive.url"), "").strip()
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


def _interactive_extract_confluence(cfg: dict) -> None:
    selected = _select_instance(cfg)
    _, page_obj, _ = _select_confluence_location(cfg, selected)
    page_id = str(page_obj.get("id") or "")
    attachment_mode = select_option(
        tr(cfg, "interactive.attachment_mode"),
        [
            ("all", tr(cfg, "interactive.attach_all")),
            ("images", tr(cfg, "interactive.attach_images")),
            ("none", tr(cfg, "interactive.attach_none")),
        ],
        default_index=0,
    )
    if attachment_mode is None:
        raise UserCancelled()
    zip_package = select_option(
        tr(cfg, "interactive.zip_package"),
        [(False, tr(cfg, "common.no")), (True, tr(cfg, "common.yes"))],
        default_index=0,
    )
    if zip_package is None:
        raise UserCancelled()
    package = export_page_to_package(
        cfg, page_id, Path(cfg["app"]["destination"]), instance_name=selected,
        attachment_mode=str(attachment_mode), zip_package=bool(zip_package),
    )
    console.print(f"[green]{tr(cfg, 'confluence.export_done', package=package)}[/green]")


def _interactive_extract_jira(cfg: dict) -> None:
    selected = _select_jira_instance(cfg)
    mode = select_option(
        tr(cfg, "jira.discovery.mode_title"),
        [
            ("manual", tr(cfg, "jira.discovery.mode_manual")),
            ("browse", tr(cfg, "jira.discovery.mode_browse")),
        ],
        default_index=1,
    )
    if mode is None:
        raise UserCancelled()

    if mode == "manual":
        issue = prompt_text(tr(cfg, "jira.discovery.manual_prompt"), "").strip()
        if not issue:
            raise UserCancelled()
    else:
        project = _select_jira_project(cfg, selected)
        project_key = str(project.get("key") or project.get("id") or "")
        issue_type = _select_jira_issue_type(cfg, selected, project_key)
        issue = _select_jira_issue(cfg, selected, project_key, issue_type)

    package = export_issue(cfg, issue, Path(cfg["app"]["destination"]), instance_name=selected)
    console.print(f"[green]{tr(cfg, 'jira.export_done', package=package)}[/green]")


def _extract_menu(cfg: dict) -> None:
    while True:
        choice = select_option(
            tr(cfg, "extract.title"),
            [
                ("local", tr(cfg, "extract.local")),
                ("force_local", tr(cfg, "extract.force_local")),
                ("confluence", tr(cfg, "extract.confluence")),
                ("jira", tr(cfg, "extract.jira")),
                ("web", tr(cfg, "extract.web")),
                ("back", tr(cfg, "common.back")),
            ],
        )
        if choice in (None, "back"):
            return
        try:
            if choice == "local":
                _interactive_extract(cfg)
            elif choice == "force_local":
                _interactive_extract(cfg, force_extract=True)
            elif choice == "confluence":
                _interactive_extract_confluence(cfg)
            elif choice == "jira":
                _interactive_extract_jira(cfg)
            elif choice == "web":
                _interactive_web(cfg)
        except UserCancelled:
            _cancelled(cfg)
        except Exception as exc:
            console.print(f"[red]{tr(cfg, 'common.error')}: {exc}[/red]")


def _interactive_import_jira(cfg: dict) -> None:
    selected = _select_jira_instance(cfg)
    source = Path(prompt_text(tr(cfg, "interactive.package_jira"), str(cfg["app"]["destination"]))).expanduser()
    project = _select_jira_project(cfg, selected, ask_filter=True)
    project_key = str(project.get("key") or project.get("id") or "")
    issue_type = _select_jira_issue_type(cfg, selected, project_key)
    summary = prompt_text(tr(cfg, "interactive.summary_default"), "").strip() or None
    parent = prompt_text(tr(cfg, "interactive.parent_optional"), "").strip() or None
    result = create_issue_from_markdown(
        cfg, source, project=project_key, issue_type=issue_type, summary=summary, parent=parent, instance_name=selected
    )
    console.print(f"[green]Jira: {result.get('key', result.get('id', ''))}[/green]")
    console.print(f"[dim]state: {result.get('state', '')}[/dim]")


def _import_menu(cfg: dict) -> None:
    while True:
        choice = select_option(
            tr(cfg, "import.title"),
            [("confluence", tr(cfg, "import.confluence")), ("jira", tr(cfg, "import.jira")), ("back", tr(cfg, "common.back"))],
        )
        if choice in (None, "back"):
            return
        try:
            if choice == "confluence":
                _interactive_import(cfg)
            elif choice == "jira":
                _interactive_import_jira(cfg)
        except UserCancelled:
            _cancelled(cfg)
        except Exception as exc:
            console.print(f"[red]{tr(cfg, 'common.error')}: {exc}[/red]")


def _rag_menu(cfg: dict) -> None:
    while True:
        choice = select_option(
            tr(cfg, "rag.title"),
            [("export", tr(cfg, "rag.export_existing")), ("doc2rag", tr(cfg, "rag.extract_export")), ("back", tr(cfg, "common.back"))],
        )
        if choice in (None, "back"):
            return
        try:
            if choice == "doc2rag":
                _interactive_doc2rag(cfg)
            elif choice == "export":
                source = Path(prompt_text(tr(cfg, "rag.source_packages"), str(cfg["app"]["destination"])))
                rag_cfg = cfg.get("rag_export") or {}
                dest = Path(prompt_text(tr(cfg, "rag.destination"), str(rag_cfg.get("destination") or "./rag")))
                result = export_rag_corpus(
                    source, dest, copy_assets=bool(rag_cfg.get("copy_assets", True)),
                    copy_document_json=bool(rag_cfg.get("copy_document_json", True)), overwrite=bool(rag_cfg.get("overwrite", True)),
                )
                console.print(f"[green]{result.document_count} document(s), {result.chunk_count} chunk(s) -> {dest}[/green]")
        except UserCancelled:
            _cancelled(cfg)
        except Exception as exc:
            console.print(f"[red]{tr(cfg, 'common.error')}: {exc}[/red]")

def _help(cfg: dict) -> None:
    lang = config_language(cfg)
    text_by_lang = {
        "en": """[bold]DocSpecBridge can:[/bold]

• Extract DOCX, PDF, PPTX, XLSX, HTML, Markdown, Web, Confluence and Jira into portable canonical packages.
• Import packages into Confluence or Jira with images, attachments and persistent publication state.
• Browse Confluence by instance → space → page and Jira by instance → project → issue type → issue.
• Force re-extract local packages while preserving Confluence/Jira publication identity.
• Build human Markdown, HTML and portable RAG corpora from the same CanonicalDocument.
• Automatically migrate older YAML configuration schemas after creating a backup.
• Use CLI commands for extraction, publication, discovery, RAG, configuration and diagnostics.

In lists: ↑/↓ navigate, Enter selects, Esc cancels the current action and returns.""",
        "fr": """[bold]DocSpecBridge permet de :[/bold]

• Extraire DOCX, PDF, PPTX, XLSX, HTML, Markdown, Web, Confluence et Jira vers des packages canoniques portables.
• Importer les packages vers Confluence ou Jira avec images, pièces jointes et état de publication persistant.
• Parcourir Confluence par instance → espace → page et Jira par instance → projet → type de ticket → ticket.
• Forcer la ré-extraction des packages locaux tout en préservant l'identité de publication Confluence/Jira.
• Générer Markdown lisible, HTML et corpus RAG portable depuis le même CanonicalDocument.
• Migrer automatiquement les anciens schémas YAML après création d'une sauvegarde.
• Utiliser les commandes CLI pour l'extraction, la publication, la découverte, le RAG, la configuration et le diagnostic.

Dans les listes : ↑/↓ pour naviguer, Entrée pour choisir, Esc pour annuler l'action courante et revenir.""",
        "de": """[bold]DocSpecBridge kann:[/bold]

• DOCX, PDF, PPTX, XLSX, HTML, Markdown, Web, Confluence und Jira in portable kanonische Pakete extrahieren.
• Pakete mit Bildern, Anhängen und dauerhaftem Veröffentlichungsstatus nach Confluence oder Jira importieren.
• Confluence über Instanz → Bereich → Seite und Jira über Instanz → Projekt → Vorgangstyp → Vorgang durchsuchen.
• Lokale Pakete zwangsweise neu extrahieren und dabei die Confluence-/Jira-Veröffentlichungsidentität beibehalten.
• Lesbares Markdown, HTML und portable RAG-Korpora aus demselben CanonicalDocument erzeugen.
• Ältere YAML-Konfigurationsschemata nach einer Sicherung automatisch migrieren.
• CLI-Befehle für Extraktion, Veröffentlichung, Suche, RAG, Konfiguration und Diagnose verwenden.

In Listen: ↑/↓ navigieren, Enter auswählen, Esc bricht die aktuelle Aktion ab und kehrt zurück.""",
        "es": """[bold]DocSpecBridge permite:[/bold]

• Extraer DOCX, PDF, PPTX, XLSX, HTML, Markdown, Web, Confluence y Jira a paquetes canónicos portátiles.
• Importar paquetes a Confluence o Jira con imágenes, adjuntos y estado de publicación persistente.
• Explorar Confluence por instancia → espacio → página y Jira por instancia → proyecto → tipo de incidencia → incidencia.
• Forzar la reextracción de paquetes locales conservando la identidad de publicación de Confluence/Jira.
• Generar Markdown legible, HTML y corpus RAG portátiles desde el mismo CanonicalDocument.
• Migrar automáticamente esquemas YAML antiguos después de crear una copia de seguridad.
• Usar comandos CLI para extracción, publicación, exploración, RAG, configuración y diagnóstico.

En las listas: ↑/↓ navegan, Enter selecciona y Esc cancela la acción actual y vuelve.""",
        "zh": """[bold]DocSpecBridge 可以：[/bold]

• 将 DOCX、PDF、PPTX、XLSX、HTML、Markdown、Web、Confluence 和 Jira 提取为可移植的规范包。
• 将包导入 Confluence 或 Jira，并保留图片、附件和持久发布状态。
• 按 实例 → 空间 → 页面 浏览 Confluence，按 实例 → 项目 → 事项类型 → 事项 浏览 Jira。
• 强制重新提取本地包，同时保留 Confluence/Jira 发布身份。
• 从同一个 CanonicalDocument 生成可读 Markdown、HTML 和可移植 RAG 语料。
• 在创建备份后自动迁移旧版 YAML 配置架构。
• 使用 CLI 完成提取、发布、浏览、RAG、配置和诊断。

列表中：↑/↓ 导航，Enter 选择，Esc 取消当前操作并返回。""",
    }
    console.print(text_by_lang.get(lang, text_by_lang["en"]))


def menu() -> None:
    config_path = selected_config_path()
    if not config_path.exists():
        init_config(config_path)
    cfg = _apply_set_overrides(load_config(config_path))
    _show_config_migration(cfg)
    ensure_workdirs(cfg)

    console.print(f"\n[bold cyan]DocSpecBridge {__version__}[/bold cyan]")
    console.print(tr(cfg, "main.subtitle") + "\n")
    while True:
        cfg = _apply_set_overrides(load_config(config_path))  # pick up settings changes immediately
        choice = select_option(
            f"DocSpecBridge {__version__}",
            [
                ("settings", tr(cfg, "main.settings")),
                ("extract", tr(cfg, "main.extract_v050")),
                ("import", tr(cfg, "main.import_v050")),
                ("rag", tr(cfg, "main.rag_v050")),
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
                _extract_menu(cfg)
            elif choice == "import":
                _import_menu(cfg)
            elif choice == "rag":
                _rag_menu(cfg)
            elif choice == "help":
                _help(cfg)
        except UserCancelled:
            _cancelled(cfg)
        except Exception as exc:
            console.print(f"[red]{tr(cfg, 'common.error')}: {exc}[/red]")
        console.print()

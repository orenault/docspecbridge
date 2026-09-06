from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
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
from .confluence import list_root_pages, list_spaces, publish
from .doctor import doctor_info
from .extractor import run_extract
from .rag_export import export_rag_corpus
from .utils import write_json
from .i18n import config_language, tr
from .ui import select_option

app = typer.Typer(add_completion=False, no_args_is_help=False, help="DocSpecBridge - document ETL bridge")
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
        raise RuntimeError("Aucune instance Confluence configurée. Utiliser Paramétrage > Instances Confluence Cloud.")
    if len(names) == 1:
        return names[0]
    default_name = default or str(cfg.get("confluence", {}).get("default_instance") or names[0])
    default_idx = names.index(default_name) if default_name in names else 0
    selected = select_option("Instance Confluence", [(name, name) for name in names], default_index=default_idx)
    if selected is None:
        raise RuntimeError("Sélection annulée.")
    return selected


def _space_table(spaces: list[dict]) -> None:
    table = Table(title="Espaces Confluence")
    table.add_column("#")
    table.add_column("Key")
    table.add_column("Name")
    table.add_column("ID")
    for idx, space in enumerate(spaces, 1):
        table.add_row(str(idx), str(space.get("key", "")), str(space.get("name", "")), str(space.get("id", "")))
    console.print(table)


def _root_table(space: dict, pages: list[dict]) -> None:
    table = Table(title=f"Pages racines - {space.get('key', '')} - {space.get('name', '')}")
    table.add_column("#")
    table.add_column("Titre")
    table.add_column("ID")
    for idx, page in enumerate(pages, 1):
        table.add_row(str(idx), str(page.get("title", "")), str(page.get("id", "")))
    console.print(table)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Sans sous-commande, ouvre le menu interactif."""
    if ctx.invoked_subcommand is None:
        menu()


@app.command()
def extract(
    source: Annotated[Optional[Path], typer.Option("--source", "-s", help="Fichier ou répertoire source")] = None,
    dest: Annotated[Optional[Path], typer.Option("--dest", "-d", help="Répertoire de destination")] = None,
    recursive: Annotated[Optional[bool], typer.Option("--recursive/--no-recursive")] = None,
    extension: Annotated[Optional[list[str]], typer.Option("--extension", "-e")] = None,
    chunk_size: Annotated[Optional[int], typer.Option("--chunk-size")] = None,
    overlap: Annotated[Optional[int], typer.Option("--overlap")] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c", help="YAML de configuration")] = None,
    overwrite: Annotated[bool, typer.Option("--overwrite", help="Autoriser la réécriture des packages existants")] = False,
) -> None:
    """Extrait DOCX/PDF/PPTX vers publication Markdown + RAG + images."""
    cfg = _runtime_config(
        _cfg(config), recursive=recursive, extensions=extension, overwrite=overwrite,
        chunk_size=chunk_size, overlap=overlap,
    )
    source = source or Path(cfg["app"]["source"])
    dest = dest or Path(cfg["app"]["destination"])
    outcomes = run_extract(cfg, source, dest)

    table = Table(title="Extraction DocSpecBridge")
    table.add_column("Source")
    table.add_column("Etat")
    table.add_column("Images", justify="right")
    table.add_column("Package")
    for item in outcomes:
        state = f"ERROR: {item.error}" if item.error else ("WARNING" if item.warnings else "OK")
        table.add_row(item.source.name, state, str(len(item.images)), str(item.package_dir))
        for warning in item.warnings:
            console.print(f"[yellow]  ! {item.source.name}: {warning}[/yellow]")
    console.print(table)
    if not outcomes:
        console.print("[yellow]Aucun fichier correspondant aux extensions configurées.[/yellow]")
    if any(item.error for item in outcomes):
        raise typer.Exit(2)


@app.command("spaces")
def spaces_cmd(
    instance: Annotated[Optional[str], typer.Option("--instance", "-i", help="Nom d'instance Confluence")] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
) -> None:
    """Liste les espaces Confluence Cloud visibles pour une instance."""
    cfg = _cfg(config)
    selected = instance or _select_instance(cfg)
    _space_table(list_spaces(cfg, selected))


@app.command("root-pages")
def root_pages_cmd(
    space_id: Annotated[str, typer.Option("--space-id", help="ID numérique Confluence du space")],
    instance: Annotated[Optional[str], typer.Option("--instance", "-i")] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
) -> None:
    """Liste les pages racines d'un espace Confluence Cloud."""
    cfg = _cfg(config)
    selected = instance or _select_instance(cfg)
    pages = list_root_pages(cfg, selected, space_id)
    _root_table({"key": space_id, "name": ""}, pages)


@app.command("publish")
def publish_cmd(
    source: Annotated[Optional[Path], typer.Option("--source", "-s", help="Package, corpus ou fichier .md")] = None,
    instance: Annotated[Optional[str], typer.Option("--instance", "-i", help="Nom d'instance Confluence")] = None,
    space: Annotated[Optional[str], typer.Option("--space", help="Clé espace Confluence")] = None,
    parent: Annotated[Optional[str], typer.Option("--parent", help="Page ID parent; -, none ou home = accueil espace")] = None,
    keep_hierarchy: Annotated[Optional[bool], typer.Option("--keep-hierarchy/--no-keep-hierarchy")] = None,
    overwrite_manual: Annotated[Optional[bool], typer.Option("--overwrite-manual/--protect-manual")] = None,
    comments: Annotated[Optional[str], typer.Option("--comments", help="remove | check-open")] = None,
    heading_anchors: Annotated[Optional[bool], typer.Option("--heading-anchors/--no-heading-anchors")] = None,
    write_page_id: Annotated[Optional[bool], typer.Option("--write-page-id/--no-write-page-id")] = None,
    image_max_width: Annotated[Optional[int], typer.Option("--image-max-width")] = None,
    table_mode: Annotated[Optional[str], typer.Option("--table-mode", help="responsive | fixed")] = None,
    render_mermaid: Annotated[Optional[bool], typer.Option("--render-mermaid/--no-render-mermaid")] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
) -> None:
    """Publie les Markdown publication dans Confluence Cloud avec leurs images inline."""
    cfg = _runtime_config(
        _cfg(config), keep_hierarchy=keep_hierarchy, comments=comments, heading_anchors=heading_anchors,
        write_page_id=write_page_id, image_max_width=image_max_width, table_mode=table_mode,
        render_mermaid=render_mermaid,
    )
    selected = instance or _select_instance(cfg)
    source = source or Path(cfg["app"]["destination"])
    parent = _parent_value(parent)
    published = publish(
        cfg, source, space_key=space, root_page=parent, instance_name=selected,
        keep_hierarchy=keep_hierarchy, overwrite_manual_changes=overwrite_manual,
    )
    console.print(f"[green]{len(published)} page(s) traitée(s) par md2conf sur {selected}.[/green]")


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
            raise ValueError("--table-mode doit valoir responsive ou fixed")
        cf.setdefault("layout", {})["table_display_mode"] = table_mode
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


@app.command("rag-export")
def rag_export_cmd(
    source: Annotated[Optional[Path], typer.Option("--source", "-s", help="Répertoire de packages DocSpecBridge")] = None,
    dest: Annotated[Optional[Path], typer.Option("--dest", "-d", help="Corpus RAG cible")] = None,
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
    console.print(f"[green]Corpus RAG: {result.document_count} document(s), {result.chunk_count} chunk(s) -> {result.destination}[/green]")


@app.command("doc2wiki")
def doc2wiki_cmd(
    source: Annotated[Optional[Path], typer.Option("--source", "-s")] = None,
    dest: Annotated[Optional[Path], typer.Option("--dest", "-d", help="Répertoire de travail/extraction")] = None,
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
    render_mermaid: Annotated[Optional[bool], typer.Option("--render-mermaid/--no-render-mermaid")] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
) -> None:
    """Document(s) -> extraction -> Confluence. Sans option, utilise entièrement le YAML."""
    cfg = _runtime_config(
        _cfg(config), recursive=recursive, extensions=extension, overwrite=overwrite, keep_hierarchy=keep_hierarchy,
        comments=comments, heading_anchors=heading_anchors, write_page_id=write_page_id,
        image_max_width=image_max_width, table_mode=table_mode, render_mermaid=render_mermaid,
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
    report = dest / "doc2wiki-report.json"
    _batch_report(report, "doc2wiki", outcomes, published=published_count, instance=selected, space=space, parent=parent)
    console.print(f"[green]Doc2Wiki: {published_count} page(s) publiée(s), rapport: {report}[/green]")
    if any(item.error for item in outcomes):
        raise typer.Exit(2)


@app.command("doc2rag")
def doc2rag_cmd(
    source: Annotated[Optional[Path], typer.Option("--source", "-s")] = None,
    dest: Annotated[Optional[Path], typer.Option("--dest", "-d", help="Répertoire de travail/extraction")] = None,
    rag_dest: Annotated[Optional[Path], typer.Option("--rag-dest", help="Corpus RAG cible")] = None,
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
    console.print(f"[green]Doc2RAG: {result.document_count} document(s), {result.chunk_count} chunk(s) -> {rag_dest}[/green]")
    if any(item.error for item in outcomes):
        raise typer.Exit(2)


@app.command("config")
def config_cmd(
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
) -> None:
    """Ouvre l'éditeur interactif du paramétrage DocSpecBridge."""
    config_menu(config)


@app.command()
def doctor(
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
) -> None:
    """Affiche versions, proxy, répertoires et état des instances/token Confluence."""
    cfg = _cfg(config)
    table = Table(title="DocSpecBridge doctor")
    table.add_column("Elément")
    table.add_column("Valeur")
    path = selected_config_path(config) if config or selected_config_path().exists() else None
    for key, value in doctor_info(cfg, config_path=path).items():
        table.add_row(key, value)
    console.print(table)


def _interactive_extract(cfg: dict) -> None:
    source = Path(Prompt.ask("Source", default=str(cfg["app"]["source"])))
    dest = Path(Prompt.ask("Destination", default=str(cfg["app"]["destination"])))
    outcomes = run_extract(cfg, source, dest)
    for item in outcomes:
        if item.error:
            console.print(f"[red]ERROR[/red] {item.source}: {item.error}")
        else:
            console.print(f"[green]OK[/green] {item.source.name} -> {item.package_dir} ({len(item.images)} image(s))")
            for warning in item.warnings:
                console.print(f"[yellow]  ! {warning}[/yellow]")
    if not outcomes:
        console.print(f"[yellow]Aucun document dans {source}. Le répertoire source est créé automatiquement.[/yellow]")


def _interactive_import(cfg: dict) -> None:
    selected = _select_instance(cfg)
    source = Path(Prompt.ask("Package/corpus à publier", default=str(cfg["app"]["destination"])))
    spaces = list_spaces(cfg, selected)
    if not spaces:
        raise RuntimeError("Aucun espace Confluence accessible.")
    default_space = str(confluence_instances(cfg)[selected].get("default_space") or "")
    default_idx = next((idx for idx, item in enumerate(spaces) if str(item.get("key")) == default_space), 0)
    space_id = select_option(
        "Espace Confluence",
        [(str(item.get("id")), f"{item.get('key', '')} - {item.get('name', '')}") for item in spaces],
        default_index=default_idx,
    )
    if space_id is None:
        return
    space_obj = next(item for item in spaces if str(item.get("id")) == space_id)
    space_key = str(space_obj.get("key"))
    instance_cfg = confluence_instances(cfg)[selected]
    default_parent = str(instance_cfg.get("root_page") or "")
    parent_raw = Prompt.ask("Page ID parent (Entrée = valeur par défaut ; '-' = accueil espace)", default=default_parent)
    parent = _parent_value(parent_raw)
    pages = publish(cfg, source, space_key=space_key, root_page=parent, instance_name=selected)
    console.print(f"[green]{len(pages)} page(s) publiée(s)/synchronisée(s) sur {selected}.[/green]")


def _interactive_spaces(cfg: dict) -> None:
    selected = _select_instance(cfg)
    _space_table(list_spaces(cfg, selected))


def _interactive_roots(cfg: dict) -> None:
    selected = _select_instance(cfg)
    changed = choose_space_root(cfg, selected, set_default=True)
    if changed:
        path = save_config(cfg)
        console.print(f"[green]Espace/page racine par défaut sauvegardés dans {path}.[/green]")


def _confluence_menu(cfg: dict) -> None:
    while True:
        choice = select_option(
            tr(cfg, "main.confluence"),
            [
                ("import", tr(cfg, "confluence.import")),
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
            elif choice == "spaces":
                _interactive_spaces(cfg)
            elif choice == "roots":
                _interactive_roots(cfg)
        except Exception as exc:
            console.print(f"[red]Erreur: {exc}[/red]")


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
    confirm = select_option("Lancer le traitement avec ces valeurs ?", [(True, "Oui"), (False, "Non")], default_index=0)
    if confirm is not True:
        return
    outcomes = run_extract(cfg, source, dest)
    good = [item for item in outcomes if not item.error]
    if bool(cfg.get("confluence", {}).get("keep_hierarchy", False)):
        published_count = len(publish(cfg, dest, instance_name=selected, keep_hierarchy=True))
    else:
        published_count = 0
        for item in good:
            published_count += len(publish(cfg, item.package_dir, instance_name=selected, keep_hierarchy=False))
    _batch_report(dest / "doc2wiki-report.json", "doc2wiki", outcomes, published=published_count, instance=selected)
    console.print(f"[green]Doc2Wiki terminé: {published_count} page(s) publiée(s).[/green]")


def _interactive_doc2rag(cfg: dict) -> None:
    source = Path(str(cfg["app"]["source"]))
    dest = Path(str(cfg["app"]["destination"]))
    rag_cfg = cfg.get("rag_export") or {}
    rag_dest = Path(str(rag_cfg.get("destination") or "./rag"))
    console.print(f"[cyan]Doc2RAG[/cyan] source={source} -> work={dest} -> corpus={rag_dest}")
    confirm = select_option("Lancer le traitement avec ces valeurs ?", [(True, "Oui"), (False, "Non")], default_index=0)
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
    console.print(f"[green]Doc2RAG terminé: {result.document_count} document(s), {result.chunk_count} chunk(s).[/green]")


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
            elif choice == "doc2wiki":
                _interactive_doc2wiki(cfg)
            elif choice == "doc2rag":
                _interactive_doc2rag(cfg)
            elif choice == "help":
                _help(cfg)
        except Exception as exc:
            console.print(f"[red]Erreur: {exc}[/red]")
        console.print()

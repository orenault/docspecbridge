from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from . import __version__
from .config import confluence_instances, load_config
from .config_ui import config_menu
from .confluence import list_spaces, publish
from .doctor import doctor_info
from .extractor import run_extract


app = typer.Typer(add_completion=False, no_args_is_help=False, help="DocSpecBridge - document ETL bridge")
console = Console()


def _cfg(config: Optional[Path]):
    return load_config(config)


def _apply_overwrite(cfg: dict, overwrite: bool) -> None:
    if overwrite:
        cfg["app"]["overwrite"] = True


def _select_instance(cfg: dict, default: str | None = None) -> str:
    names = list(confluence_instances(cfg))
    if not names:
        raise RuntimeError("Aucune instance Confluence configurée. Utiliser le menu Configuration YAML.")
    if len(names) == 1:
        return names[0]
    default_name = default or str(cfg.get("confluence", {}).get("default_instance") or names[0])
    for idx, name in enumerate(names, 1):
        marker = " *" if name == default_name else ""
        console.print(f"[{idx}] {name}{marker}")
    default_idx = str(names.index(default_name) + 1) if default_name in names else "1"
    idx = int(Prompt.ask("Instance Confluence", default=default_idx))
    return names[idx - 1]


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Sans sous-commande, ouvre le menu interactif."""
    if ctx.invoked_subcommand is None:
        menu()


@app.command()
def extract(
    source: Annotated[Optional[Path], typer.Option("--source", "-s", help="Fichier ou répertoire source")] = None,
    dest: Annotated[Optional[Path], typer.Option("--dest", "-d", help="Répertoire de destination")] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c", help="YAML de configuration")] = None,
    overwrite: Annotated[bool, typer.Option("--overwrite", help="Autoriser la réécriture des packages existants")] = False,
) -> None:
    """Extrait DOCX/PDF/PPTX vers publication Markdown + RAG + images."""
    cfg = _cfg(config)
    _apply_overwrite(cfg, overwrite)
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
    instance: Annotated[Optional[str], typer.Option("--instance", "-i", help="Nom d'instance Confluence YAML")] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
) -> None:
    """Liste les espaces Confluence Cloud visibles pour une instance."""
    cfg = _cfg(config)
    selected = instance or _select_instance(cfg)
    spaces = list_spaces(cfg, selected)
    table = Table(title=f"Espaces Confluence - {selected}")
    table.add_column("Key")
    table.add_column("Name")
    table.add_column("ID")
    for space in spaces:
        table.add_row(str(space.get("key", "")), str(space.get("name", "")), str(space.get("id", "")))
    console.print(table)


@app.command("publish")
def publish_cmd(
    source: Annotated[Path, typer.Option("--source", "-s", help="Package, corpus ou fichier .md")],
    instance: Annotated[Optional[str], typer.Option("--instance", "-i", help="Nom d'instance Confluence YAML")] = None,
    space: Annotated[Optional[str], typer.Option("--space", help="Clé espace Confluence")] = None,
    parent: Annotated[Optional[str], typer.Option("--parent", help="Page ID parent/root")] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
) -> None:
    """Publie les Markdown publication dans Confluence Cloud avec leurs images inline."""
    cfg = _cfg(config)
    selected = instance or _select_instance(cfg)
    published = publish(cfg, source, space_key=space, root_page=parent, instance_name=selected)
    console.print(f"[green]{len(published)} page(s) traitée(s) par md2conf sur {selected}.[/green]")


app.command("import-confluence")(publish_cmd)


@app.command("config")
def config_cmd(
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
) -> None:
    """Ouvre le petit éditeur interactif du YAML DocSpecBridge."""
    config_menu(config)


@app.command()
def doctor(
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
) -> None:
    """Affiche versions, proxy et instances/token Confluence."""
    cfg = _cfg(config)
    table = Table(title="DocSpecBridge doctor")
    table.add_column("Elément")
    table.add_column("Valeur")
    for key, value in doctor_info(cfg).items():
        table.add_row(key, value)
    console.print(table)


def menu() -> None:
    console.print(f"\n[bold cyan]DocSpecBridge {__version__}[/bold cyan]")
    console.print("Document ETL: Office/PDF -> publication + RAG -> Confluence Cloud\n")
    while True:
        console.print("[1] Extract")
        console.print("[2] Import Confluence")
        console.print("[3] Lister les espaces Confluence")
        console.print("[4] Doctor")
        console.print("[5] Configuration YAML")
        console.print("[0] Quitter")
        choice = Prompt.ask("Action", choices=["0", "1", "2", "3", "4", "5"], default="1")
        try:
            if choice == "0":
                return
            if choice == "1":
                cfg = load_config()
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
            elif choice == "2":
                cfg = load_config()
                selected = _select_instance(cfg)
                source = Path(Prompt.ask("Package/corpus à publier", default=str(cfg["app"]["destination"])))
                spaces = list_spaces(cfg, selected)
                for idx, item in enumerate(spaces, start=1):
                    console.print(f"[{idx}] {item.get('key', '')} - {item.get('name', '')}")
                selected_space = int(Prompt.ask("Numéro espace"))
                space = str(spaces[selected_space - 1].get("key"))
                instance_cfg = confluence_instances(cfg)[selected]
                default_parent = str(instance_cfg.get("root_page") or "")
                parent = Prompt.ask("Page ID parent (vide = accueil espace)", default=default_parent).strip() or None
                pages = publish(cfg, source, space_key=space, root_page=parent, instance_name=selected)
                console.print(f"[green]{len(pages)} page(s) publiée(s)/synchronisée(s) sur {selected}.[/green]")
            elif choice == "3":
                cfg = load_config()
                selected = _select_instance(cfg)
                for item in list_spaces(cfg, selected):
                    console.print(f"{item.get('key', ''):15} {item.get('name', '')}")
            elif choice == "4":
                cfg = load_config()
                for key, value in doctor_info(cfg).items():
                    console.print(f"{key:30} {value}")
            elif choice == "5":
                config_menu()
        except Exception as exc:
            console.print(f"[red]Erreur: {exc}[/red]")
        console.print()

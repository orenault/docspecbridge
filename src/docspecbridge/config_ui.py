from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from .config import confluence_instances, init_config, load_config, save_config, selected_config_path


console = Console()


def _show_summary(cfg: dict[str, Any], path: Path) -> None:
    table = Table(title=f"Configuration DocSpecBridge - {path}")
    table.add_column("Clé")
    table.add_column("Valeur")
    app = cfg["app"]
    pub = cfg["profiles"]["publication"]
    rag = cfg["profiles"]["rag"]
    cf = cfg["confluence"]
    table.add_row("app.source", str(app.get("source")))
    table.add_row("app.destination", str(app.get("destination")))
    table.add_row("app.extensions", ", ".join(app.get("extensions") or []))
    table.add_row("app.recursive", str(bool(app.get("recursive"))))
    table.add_row("publication.preserve_image_display_size", str(bool(pub.get("preserve_image_display_size"))))
    table.add_row("rag.enabled", str(bool(rag.get("enabled"))))
    table.add_row("rag.chunking.enabled", str(bool((rag.get("chunking") or {}).get("enabled"))))
    table.add_row("confluence.default_instance", str(cf.get("default_instance") or ""))
    table.add_row("confluence.instances", ", ".join(confluence_instances(cfg)) or "(aucune)")
    console.print(table)


def _edit_app(cfg: dict[str, Any]) -> None:
    app = cfg["app"]
    app["source"] = Prompt.ask("Source par défaut", default=str(app.get("source") or "./input"))
    app["destination"] = Prompt.ask("Destination par défaut", default=str(app.get("destination") or "./output"))
    ext_default = ",".join(app.get("extensions") or [".docx", ".pdf", ".pptx"])
    extensions = Prompt.ask("Extensions (séparées par des virgules)", default=ext_default)
    app["extensions"] = [e.strip().lower() if e.strip().startswith(".") else "." + e.strip().lower() for e in extensions.split(",") if e.strip()]
    app["recursive"] = Confirm.ask("Recherche récursive", default=bool(app.get("recursive", True)))
    app["preserve_source_tree"] = Confirm.ask(
        "Conserver l'arborescence source", default=bool(app.get("preserve_source_tree", True))
    )
    app["copy_source"] = Confirm.ask("Copier le fichier source dans le package", default=bool(app.get("copy_source", True)))


def _edit_profiles(cfg: dict[str, Any]) -> None:
    profiles = cfg["profiles"]
    pub = profiles["publication"]
    rag = profiles["rag"]
    pub["enabled"] = Confirm.ask("Générer le Markdown publication", default=bool(pub.get("enabled", True)))
    pub["preserve_image_display_size"] = Confirm.ask(
        "Préserver la taille d'affichage des images", default=bool(pub.get("preserve_image_display_size", True))
    )
    rag["enabled"] = Confirm.ask("Générer le profil RAG", default=bool(rag.get("enabled", True)))
    rag["keep_image_references"] = Confirm.ask(
        "Conserver les références images dans rag.md", default=bool(rag.get("keep_image_references", True))
    )
    rag["include_header_images"] = Confirm.ask(
        "Conserver les images de header dans rag.md", default=bool(rag.get("include_header_images", False))
    )
    chunking = rag.setdefault("chunking", {})
    chunking["enabled"] = Confirm.ask("Générer chunks.jsonl", default=bool(chunking.get("enabled", True)))
    if chunking["enabled"]:
        chunking["max_characters"] = int(Prompt.ask("Taille max chunk (caractères)", default=str(chunking.get("max_characters", 1600))))
        chunking["overlap"] = int(Prompt.ask("Overlap (caractères)", default=str(chunking.get("overlap", 150))))


def _list_instances(cfg: dict[str, Any]) -> None:
    instances = confluence_instances(cfg)
    default_name = str((cfg.get("confluence") or {}).get("default_instance") or "")
    table = Table(title="Instances Confluence Cloud")
    table.add_column("Nom")
    table.add_column("Défaut")
    table.add_column("Domaine")
    table.add_column("Utilisateur")
    table.add_column("Token env")
    table.add_column("Espace")
    for name, item in instances.items():
        table.add_row(
            name,
            "*" if name == default_name else "",
            str(item.get("domain") or item.get("api_url") or ""),
            str(item.get("user_name") or "Bearer"),
            str(item.get("token_env") or "ATLASSIAN_API_TOKEN"),
            str(item.get("default_space") or ""),
        )
    console.print(table)


def _edit_instance(cfg: dict[str, Any]) -> None:
    cf = cfg.setdefault("confluence", {})
    instances = cf.setdefault("instances", {})
    current_names = list(instances)
    default_name = current_names[0] if current_names else "production"
    name = Prompt.ask("Nom logique de l'instance", default=default_name).strip()
    current = dict(instances.get(name) or {})
    current["domain"] = Prompt.ask("Domaine Cloud (ex: company.atlassian.net)", default=str(current.get("domain") or ""))
    current["base_path"] = "/wiki/"
    current["api_url"] = Prompt.ask(
        "API URL Atlassian scoped token (optionnel, vide pour domaine classique)",
        default=str(current.get("api_url") or ""),
    ).strip()
    current["user_name"] = Prompt.ask(
        "Email Atlassian (vide = Bearer/scoped token)", default=str(current.get("user_name") or "")
    ).strip()
    current["token_env"] = Prompt.ask("Variable d'environnement du token", default=str(current.get("token_env") or "ATLASSIAN_API_TOKEN")).strip()
    current["api_version"] = "v2"
    current["default_space"] = Prompt.ask("Espace par défaut (optionnel)", default=str(current.get("default_space") or "")).strip()
    current["root_page"] = Prompt.ask("Page racine ID (optionnel)", default=str(current.get("root_page") or "")).strip()
    instances[name] = current
    if not cf.get("default_instance"):
        cf["default_instance"] = name


def _instances_menu(cfg: dict[str, Any]) -> None:
    while True:
        console.print("\n[1] Lister")
        console.print("[2] Ajouter / modifier")
        console.print("[3] Définir l'instance par défaut")
        console.print("[4] Supprimer")
        console.print("[0] Retour")
        choice = Prompt.ask("Action Confluence", choices=["0", "1", "2", "3", "4"], default="1")
        if choice == "0":
            return
        if choice == "1":
            _list_instances(cfg)
        elif choice == "2":
            _edit_instance(cfg)
        elif choice == "3":
            names = list(confluence_instances(cfg))
            if not names:
                console.print("[yellow]Aucune instance définie.[/yellow]")
                continue
            for idx, name in enumerate(names, 1):
                console.print(f"[{idx}] {name}")
            idx = int(Prompt.ask("Numéro", default="1"))
            cfg["confluence"]["default_instance"] = names[idx - 1]
        elif choice == "4":
            names = list(confluence_instances(cfg))
            if not names:
                continue
            for idx, name in enumerate(names, 1):
                console.print(f"[{idx}] {name}")
            idx = int(Prompt.ask("Numéro à supprimer"))
            name = names[idx - 1]
            if Confirm.ask(f"Supprimer {name} ?", default=False):
                cfg["confluence"]["instances"].pop(name, None)
                if cfg["confluence"].get("default_instance") == name:
                    cfg["confluence"]["default_instance"] = next(iter(cfg["confluence"]["instances"]), "")


def config_menu(path: Path | None = None) -> Path:
    config_path = selected_config_path(path)
    if not config_path.exists():
        if Confirm.ask(f"Créer {config_path} ?", default=True):
            init_config(config_path)
    cfg = load_config(config_path) if config_path.exists() else load_config(None)

    while True:
        console.print("\n[bold cyan]Configuration YAML[/bold cyan]")
        console.print("[1] Afficher le résumé")
        console.print("[2] Source / destination / extensions")
        console.print("[3] Profils publication / RAG")
        console.print("[4] Instances Confluence Cloud")
        console.print("[5] Sauvegarder")
        console.print("[0] Sauvegarder et retour")
        choice = Prompt.ask("Action", choices=["0", "1", "2", "3", "4", "5"], default="1")
        if choice == "1":
            _show_summary(cfg, config_path)
        elif choice == "2":
            _edit_app(cfg)
        elif choice == "3":
            _edit_profiles(cfg)
        elif choice == "4":
            _instances_menu(cfg)
        elif choice == "5":
            save_config(cfg, config_path)
            console.print(f"[green]Sauvegardé: {config_path}[/green]")
        elif choice == "0":
            save_config(cfg, config_path)
            console.print(f"[green]Sauvegardé: {config_path}[/green]")
            return config_path

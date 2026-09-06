from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from .config import (
    confluence_instances,
    ensure_workdirs,
    init_config,
    load_config,
    normalize_domain,
    save_config,
    selected_config_path,
)
from .confluence import list_root_pages, list_spaces
from .doctor import doctor_info
from .i18n import SUPPORTED_LANGUAGES, config_language, tr
from .ui import select_option

console = Console()

_FIELD_TEXT: dict[str, dict[str, str]] = {
    "fr": {
        "source": "Source par défaut",
        "destination": "Destination par défaut",
        "extensions": "Extensions (séparées par des virgules)",
        "recursive": "Recherche récursive",
        "tree": "Conserver l'arborescence source",
        "copy": "Copier le fichier source dans le package",
        "pub": "Générer le Markdown publication",
        "image_size": "Préserver la taille d'affichage des images",
        "rag": "Générer le profil RAG",
        "rag_images": "Conserver les références images dans rag.md",
        "header_images": "Conserver les images de header dans rag.md",
        "chunks": "Générer chunks.jsonl",
        "chunk_size": "Taille max chunk (caractères)",
        "overlap": "Overlap (caractères)",
        "raw": "Conserver le Markdown brut Xberg (diagnostic)",
        "toc": "Remplacer un sommaire source détecté par une TOC Confluence native",
        "rag_export_destination": "Destination du corpus RAG",
        "instance_name": "Nom logique de l'instance",
        "domain": "Domaine Confluence Cloud (ex. company.atlassian.net)",
        "email": "Email du compte Atlassian",
        "token_env": "Variable d'environnement contenant le token",
        "cloud_id": "Cloud ID Atlassian",
        "space": "Espace par défaut (optionnel)",
        "root": "Page racine ID (optionnel)",
        "classic": "API token classique (Basic : email + token)",
        "scoped": "API token scoped (Cloud ID + email + token)",
        "auth": "Type de token Atlassian",
        "rename": "Nouveau nom logique",
        "delete_confirm": "Confirmer la suppression",
        "set_root": "Définir une page comme défaut pour l'import ?",
        "select_space": "Choisir l'espace",
        "select_page": "Choisir la page racine",
        "keep_hierarchy": "Préserver l’arborescence en mode batch",
        "overwrite_manual": "Écraser les modifications manuelles Confluence",
        "page_id": "Mémoriser le page_id Confluence dans le Markdown généré",
        "heading_anchors": "Ajouter des ancres sur les titres",
        "comments": "Gestion des commentaires inline",
        "alignment": "Alignement global",
        "image_max_width": "Largeur max image (px)",
        "table_mode": "Mode des tableaux",
        "table_width": "Largeur max tableau (px, - pour aucune)",
    },
    "en": {
        "source": "Default source",
        "destination": "Default destination",
        "extensions": "Extensions (comma-separated)",
        "recursive": "Recursive search",
        "tree": "Preserve source directory tree",
        "copy": "Copy source file into package",
        "pub": "Generate publication Markdown",
        "image_size": "Preserve image display size",
        "rag": "Generate RAG profile",
        "rag_images": "Keep image references in rag.md",
        "header_images": "Keep header images in rag.md",
        "chunks": "Generate chunks.jsonl",
        "chunk_size": "Maximum chunk size (characters)",
        "overlap": "Overlap (characters)",
        "raw": "Keep raw Xberg Markdown (diagnostics)",
        "toc": "Replace a detected source TOC with a native Confluence TOC",
        "rag_export_destination": "RAG corpus destination",
        "instance_name": "Logical instance name",
        "domain": "Confluence Cloud domain (e.g. company.atlassian.net)",
        "email": "Atlassian account email",
        "token_env": "Environment variable containing the token",
        "cloud_id": "Atlassian Cloud ID",
        "space": "Default space (optional)",
        "root": "Root page ID (optional)",
        "classic": "Classic API token (Basic: email + token)",
        "scoped": "Scoped API token (Cloud ID + email + token)",
        "auth": "Atlassian token type",
        "rename": "New logical name",
        "delete_confirm": "Confirm deletion",
        "set_root": "Set a page as the default import root?",
        "select_space": "Select space",
        "select_page": "Select root page",
        "keep_hierarchy": "Preserve hierarchy in batch mode",
        "overwrite_manual": "Overwrite manual Confluence changes",
        "page_id": "Persist Confluence page_id in generated Markdown",
        "heading_anchors": "Add anchors to headings",
        "comments": "Inline comment handling",
        "alignment": "Global alignment",
        "image_max_width": "Maximum image width (px)",
        "table_mode": "Table display mode",
        "table_width": "Maximum table width (px, - for none)",
    },
    "de": {
        "source": "Standardquelle", "destination": "Standardziel", "extensions": "Erweiterungen (kommagetrennt)",
        "recursive": "Rekursive Suche", "tree": "Quellstruktur beibehalten", "copy": "Quelldatei in Paket kopieren",
        "pub": "Veröffentlichungs-Markdown erzeugen", "image_size": "Bildanzeigegröße beibehalten", "rag": "RAG-Profil erzeugen",
        "rag_images": "Bildreferenzen in rag.md behalten", "header_images": "Kopfbilder in rag.md behalten", "chunks": "chunks.jsonl erzeugen",
        "chunk_size": "Maximale Chunk-Größe (Zeichen)", "overlap": "Überlappung (Zeichen)", "raw": "Rohes Xberg-Markdown behalten (Diagnose)", "toc": "Erkanntes Inhaltsverzeichnis durch native Confluence-TOC ersetzen", "rag_export_destination": "RAG-Korpus-Ziel",
        "instance_name": "Logischer Instanzname", "domain": "Confluence-Cloud-Domain", "email": "Atlassian-Konto-E-Mail",
        "token_env": "Umgebungsvariable mit Token", "cloud_id": "Atlassian Cloud ID", "space": "Standardbereich (optional)",
        "root": "Stammseiten-ID (optional)", "classic": "Klassisches API-Token (Basic: E-Mail + Token)",
        "scoped": "Scoped API-Token (Cloud ID + E-Mail + Token)", "auth": "Atlassian-Token-Typ", "rename": "Neuer logischer Name",
        "delete_confirm": "Löschen bestätigen", "set_root": "Seite als Standard-Importwurzel festlegen?", "select_space": "Bereich wählen", "select_page": "Stammseite wählen",
    },
    "es": {
        "source": "Origen predeterminado", "destination": "Destino predeterminado", "extensions": "Extensiones (separadas por comas)",
        "recursive": "Búsqueda recursiva", "tree": "Conservar árbol de origen", "copy": "Copiar archivo fuente al paquete",
        "pub": "Generar Markdown de publicación", "image_size": "Conservar tamaño de visualización de imágenes", "rag": "Generar perfil RAG",
        "rag_images": "Conservar referencias de imágenes en rag.md", "header_images": "Conservar imágenes de cabecera en rag.md", "chunks": "Generar chunks.jsonl",
        "chunk_size": "Tamaño máximo del chunk (caracteres)", "overlap": "Solapamiento (caracteres)", "raw": "Conservar Markdown bruto de Xberg (diagnóstico)", "toc": "Reemplazar índice detectado por TOC nativa de Confluence", "rag_export_destination": "Destino del corpus RAG",
        "instance_name": "Nombre lógico de la instancia", "domain": "Dominio de Confluence Cloud", "email": "Correo de la cuenta Atlassian",
        "token_env": "Variable de entorno con el token", "cloud_id": "Cloud ID de Atlassian", "space": "Espacio predeterminado (opcional)",
        "root": "ID de página raíz (opcional)", "classic": "Token API clásico (Basic: correo + token)",
        "scoped": "Token API scoped (Cloud ID + correo + token)", "auth": "Tipo de token Atlassian", "rename": "Nuevo nombre lógico",
        "delete_confirm": "Confirmar eliminación", "set_root": "¿Definir una página como raíz predeterminada de importación?", "select_space": "Elegir espacio", "select_page": "Elegir página raíz",
    },
    "zh": {
        "source": "默认源目录", "destination": "默认目标目录", "extensions": "扩展名（逗号分隔）", "recursive": "递归搜索",
        "tree": "保留源目录结构", "copy": "将源文件复制到包中", "pub": "生成发布 Markdown", "image_size": "保留图片显示尺寸",
        "rag": "生成 RAG 配置", "rag_images": "在 rag.md 中保留图片引用", "header_images": "在 rag.md 中保留页眉图片", "chunks": "生成 chunks.jsonl",
        "chunk_size": "最大块大小（字符）", "overlap": "重叠（字符）", "raw": "保留 Xberg 原始 Markdown（诊断）", "toc": "将检测到的目录替换为 Confluence 原生目录", "rag_export_destination": "RAG 语料目标目录",
        "instance_name": "实例逻辑名称", "domain": "Confluence Cloud 域名", "email": "Atlassian 账户邮箱", "token_env": "保存 token 的环境变量",
        "cloud_id": "Atlassian Cloud ID", "space": "默认空间（可选）", "root": "根页面 ID（可选）", "classic": "经典 API token（Basic：邮箱 + token）",
        "scoped": "Scoped API token（Cloud ID + 邮箱 + token）", "auth": "Atlassian token 类型", "rename": "新逻辑名称",
        "delete_confirm": "确认删除", "set_root": "将某页面设为默认导入根页面？", "select_space": "选择空间", "select_page": "选择根页面",
    },
}


def _field(cfg: dict[str, Any], key: str) -> str:
    lang = config_language(cfg)
    return _FIELD_TEXT.get(lang, _FIELD_TEXT["en"]).get(key, _FIELD_TEXT["en"].get(key, key))


def _confirm(cfg: dict[str, Any], label: str, default: bool = True) -> bool | None:
    options = [(True, tr(cfg, "common.yes")), (False, tr(cfg, "common.no"))]
    return select_option(label, options, default_index=0 if default else 1)


def _edit_text(cfg: dict[str, Any], label: str, current: str = "", *, allow_clear: bool = True) -> str:
    hint = tr(cfg, "common.keep_clear_hint") if allow_clear else ""
    prompt = f"{label} [{hint}]" if hint else label
    value = Prompt.ask(prompt, default=current).strip()
    if allow_clear and value == "-":
        return ""
    return value


def _show_summary(cfg: dict[str, Any], path: Path) -> None:
    app = cfg["app"]
    pub = cfg["profiles"]["publication"]
    rag = cfg["profiles"]["rag"]
    extract = cfg["extract"]
    cf = cfg["confluence"]

    table = Table(title=f"{tr(cfg, 'settings.summary')} - {path}")
    table.add_column("Paramètre")
    table.add_column("Valeur")
    table.add_row("language", f"{app.get('language')} - {SUPPORTED_LANGUAGES.get(config_language(cfg), '')}")
    source = Path(str(app.get("source")))
    dest = Path(str(app.get("destination")))
    table.add_row("source", f"{source} ({'OK' if source.exists() else 'absent'})")
    table.add_row("destination", f"{dest} ({'OK' if dest.exists() else 'absent'})")
    table.add_row("extensions", ", ".join(app.get("extensions") or []))
    table.add_row("recursive", str(bool(app.get("recursive"))))
    table.add_row("publication", str(bool(pub.get("enabled"))))
    table.add_row("publication.image_display_size", str(bool(pub.get("preserve_image_display_size"))))
    toc_cfg = pub.get("table_of_contents") or {}
    table.add_row("publication.toc", f"{toc_cfg.get('enabled', 'auto')} / replace_source={bool(toc_cfg.get('replace_source_toc', True))}")
    table.add_row("rag", str(bool(rag.get("enabled"))))
    table.add_row("rag.chunks", str(bool((rag.get("chunking") or {}).get("enabled"))))
    table.add_row("rag.export.destination", str((cfg.get("rag_export") or {}).get("destination") or "./rag"))
    table.add_row("diagnostics.raw_xberg_md", str(bool((extract.get("diagnostics") or {}).get("keep_raw_xberg_markdown"))))
    table.add_row("confluence.default_instance", str(cf.get("default_instance") or tr(cfg, "common.none")))
    table.add_row("confluence.keep_hierarchy", str(bool(cf.get("keep_hierarchy", False))))
    table.add_row("confluence.heading_anchors", str(bool(cf.get("heading_anchors", True))))
    table.add_row("confluence.write_page_id", str(bool(cf.get("write_page_id_to_markdown", True))))
    table.add_row("confluence.comments", str(cf.get("comments") or "remove"))
    console.print(table)

    instances = confluence_instances(cfg)
    instance_table = Table(title="Confluence Cloud")
    instance_table.add_column("Nom")
    instance_table.add_column("Défaut")
    instance_table.add_column("Auth")
    instance_table.add_column("Domaine")
    instance_table.add_column("Utilisateur")
    instance_table.add_column("Token env")
    instance_table.add_column("Espace")
    instance_table.add_column("Page racine")
    for name, item in instances.items():
        instance_table.add_row(
            name,
            "*" if name == cf.get("default_instance") else "",
            str(item.get("auth_type") or "classic"),
            str(item.get("domain") or ""),
            str(item.get("user_name") or ""),
            str(item.get("token_env") or "ATLASSIAN_API_TOKEN"),
            str(item.get("default_space") or ""),
            str(item.get("root_page") or ""),
        )
    if instances:
        console.print(instance_table)
    else:
        console.print(f"Confluence: {tr(cfg, 'common.none')}")


def _edit_language(cfg: dict[str, Any]) -> bool:
    current = config_language(cfg)
    codes = list(SUPPORTED_LANGUAGES)
    default_idx = codes.index(current) if current in codes else 0
    selected = select_option(
        tr(cfg, "settings.language"),
        [(code, f"{label} ({code.upper()})") for code, label in SUPPORTED_LANGUAGES.items()],
        default_index=default_idx,
    )
    if selected is None:
        return False
    cfg["app"]["language"] = selected
    console.print(f"[green]{tr(cfg, 'settings.language_saved', language=SUPPORTED_LANGUAGES[selected])}[/green]")
    return True


def _edit_app(cfg: dict[str, Any]) -> bool:
    app = cfg["app"]
    app["source"] = _edit_text(cfg, _field(cfg, "source"), str(app.get("source") or "./input"), allow_clear=False)
    app["destination"] = _edit_text(cfg, _field(cfg, "destination"), str(app.get("destination") or "./output"), allow_clear=False)
    ext_default = ",".join(app.get("extensions") or [".docx", ".pdf", ".pptx"])
    extensions = _edit_text(cfg, _field(cfg, "extensions"), ext_default, allow_clear=False)
    app["extensions"] = [
        e.strip().lower() if e.strip().startswith(".") else "." + e.strip().lower()
        for e in extensions.split(",") if e.strip()
    ]
    recursive = _confirm(cfg, _field(cfg, "recursive"), bool(app.get("recursive", True)))
    if recursive is None:
        return True
    app["recursive"] = recursive
    tree = _confirm(cfg, _field(cfg, "tree"), bool(app.get("preserve_source_tree", True)))
    if tree is not None:
        app["preserve_source_tree"] = tree
    copy_source = _confirm(cfg, _field(cfg, "copy"), bool(app.get("copy_source", True)))
    if copy_source is not None:
        app["copy_source"] = copy_source
    ensure_workdirs(cfg)
    return True


def _edit_profiles(cfg: dict[str, Any]) -> bool:
    profiles = cfg["profiles"]
    pub = profiles["publication"]
    rag = profiles["rag"]
    extract_diag = cfg["extract"].setdefault("diagnostics", {})

    value = _confirm(cfg, _field(cfg, "pub"), bool(pub.get("enabled", True)))
    if value is not None:
        pub["enabled"] = value
    value = _confirm(cfg, _field(cfg, "image_size"), bool(pub.get("preserve_image_display_size", True)))
    if value is not None:
        pub["preserve_image_display_size"] = value
    toc = pub.setdefault("table_of_contents", {})
    value = _confirm(cfg, _field(cfg, "toc"), bool(toc.get("replace_source_toc", True)))
    if value is not None:
        toc["enabled"] = "auto" if value else False
        toc["replace_source_toc"] = value
    value = _confirm(cfg, _field(cfg, "rag"), bool(rag.get("enabled", True)))
    if value is not None:
        rag["enabled"] = value
    value = _confirm(cfg, _field(cfg, "rag_images"), bool(rag.get("keep_image_references", True)))
    if value is not None:
        rag["keep_image_references"] = value
    value = _confirm(cfg, _field(cfg, "header_images"), bool(rag.get("include_header_images", False)))
    if value is not None:
        rag["include_header_images"] = value
    chunking = rag.setdefault("chunking", {})
    value = _confirm(cfg, _field(cfg, "chunks"), bool(chunking.get("enabled", True)))
    if value is not None:
        chunking["enabled"] = value
    if chunking.get("enabled", True):
        chunking["max_characters"] = int(Prompt.ask(_field(cfg, "chunk_size"), default=str(chunking.get("max_characters", 1600))))
        chunking["overlap"] = int(Prompt.ask(_field(cfg, "overlap"), default=str(chunking.get("overlap", 150))))
    value = _confirm(cfg, _field(cfg, "raw"), bool(extract_diag.get("keep_raw_xberg_markdown", False)))
    if value is not None:
        extract_diag["keep_raw_xberg_markdown"] = value
    rag_export = cfg.setdefault("rag_export", {})
    rag_export["destination"] = _edit_text(
        cfg, _field(cfg, "rag_export_destination"), str(rag_export.get("destination") or "./rag"), allow_clear=False
    )
    ensure_workdirs(cfg)
    return True


def _list_instances(cfg: dict[str, Any]) -> None:
    instances = confluence_instances(cfg)
    default_name = str((cfg.get("confluence") or {}).get("default_instance") or "")
    table = Table(title="Confluence Cloud")
    for col in ("Nom", "Défaut", "Auth", "Domaine", "Utilisateur", "Token env", "Cloud ID", "Espace", "Page racine"):
        table.add_column(col)
    for name, item in instances.items():
        table.add_row(
            name,
            "*" if name == default_name else "",
            str(item.get("auth_type") or "classic"),
            str(item.get("domain") or ""),
            str(item.get("user_name") or ""),
            str(item.get("token_env") or "ATLASSIAN_API_TOKEN"),
            str(item.get("cloud_id") or ""),
            str(item.get("default_space") or ""),
            str(item.get("root_page") or ""),
        )
    console.print(table)


def _select_instance_name(cfg: dict[str, Any], title: str) -> str | None:
    names = list(confluence_instances(cfg))
    if not names:
        console.print("[yellow]Aucune instance Confluence configurée.[/yellow]")
        return None
    default_name = str((cfg.get("confluence") or {}).get("default_instance") or names[0])
    default_idx = names.index(default_name) if default_name in names else 0
    return select_option(title, [(name, name) for name in names], default_index=default_idx)


def _edit_auth_fields(cfg: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    auth_current = str(item.get("auth_type") or "classic")
    auth = select_option(
        _field(cfg, "auth"),
        [("classic", _field(cfg, "classic")), ("scoped", _field(cfg, "scoped"))],
        default_index=1 if auth_current == "scoped" else 0,
    )
    if auth is not None:
        item["auth_type"] = auth
    item["domain"] = normalize_domain(_edit_text(cfg, _field(cfg, "domain"), str(item.get("domain") or ""), allow_clear=True))
    item["user_name"] = _edit_text(cfg, _field(cfg, "email"), str(item.get("user_name") or ""), allow_clear=True)
    item["token_env"] = _edit_text(cfg, _field(cfg, "token_env"), str(item.get("token_env") or "ATLASSIAN_API_TOKEN"), allow_clear=False)
    if item.get("auth_type") == "scoped":
        item["cloud_id"] = _edit_text(cfg, _field(cfg, "cloud_id"), str(item.get("cloud_id") or ""), allow_clear=True)
        item["api_url"] = f"https://api.atlassian.com/ex/confluence/{item['cloud_id']}" if item.get("cloud_id") else ""
    else:
        item["cloud_id"] = ""
        item["api_url"] = ""
    item["base_path"] = "/wiki/"
    item["api_version"] = "v2"
    item["default_space"] = _edit_text(cfg, _field(cfg, "space"), str(item.get("default_space") or ""), allow_clear=True)
    item["root_page"] = _edit_text(cfg, _field(cfg, "root"), str(item.get("root_page") or ""), allow_clear=True)
    return item


def _add_instance(cfg: dict[str, Any]) -> bool:
    cf = cfg.setdefault("confluence", {})
    instances = cf.setdefault("instances", {})
    suggested = "production" if "production" not in instances else "confluence"
    name = Prompt.ask(_field(cfg, "instance_name"), default=suggested).strip()
    if not name:
        return False
    if name in instances:
        console.print(f"[red]L'instance '{name}' existe déjà. Utiliser Modifier.[/red]")
        return False
    item = _edit_auth_fields(cfg, {})
    instances[name] = item
    if not cf.get("default_instance"):
        cf["default_instance"] = name
    return True


def _modify_instance(cfg: dict[str, Any]) -> bool:
    cf = cfg.setdefault("confluence", {})
    instances = cf.setdefault("instances", {})
    old_name = _select_instance_name(cfg, tr(cfg, "instances.modify"))
    if not old_name:
        return False
    current = dict(instances.get(old_name) or {})
    new_name = _edit_text(cfg, _field(cfg, "rename"), old_name, allow_clear=False)
    if not new_name:
        new_name = old_name
    if new_name != old_name and new_name in instances:
        console.print(f"[red]L'instance '{new_name}' existe déjà.[/red]")
        return False
    current = _edit_auth_fields(cfg, current)
    if new_name != old_name:
        instances.pop(old_name, None)
        instances[new_name] = current
        if cf.get("default_instance") == old_name:
            cf["default_instance"] = new_name
    else:
        instances[old_name] = current
    return True


def _set_default_instance(cfg: dict[str, Any]) -> bool:
    name = _select_instance_name(cfg, tr(cfg, "instances.default"))
    if not name:
        return False
    cfg["confluence"]["default_instance"] = name
    return True


def _delete_instance(cfg: dict[str, Any]) -> bool:
    name = _select_instance_name(cfg, tr(cfg, "instances.delete"))
    if not name:
        return False
    confirm = _confirm(cfg, f"{_field(cfg, 'delete_confirm')} : {name}", False)
    if confirm is not True:
        return False
    cfg["confluence"]["instances"].pop(name, None)
    if cfg["confluence"].get("default_instance") == name:
        cfg["confluence"]["default_instance"] = next(iter(cfg["confluence"]["instances"]), "")
    return True


def choose_space_root(cfg: dict[str, Any], instance_name: str, *, set_default: bool = True) -> bool:
    spaces = list_spaces(cfg, instance_name)
    if not spaces:
        console.print("[yellow]Aucun espace accessible.[/yellow]")
        return False
    selected_space_id = select_option(
        _field(cfg, "select_space"),
        [
            (str(space.get("id", "")), f"{space.get('key', '')} - {space.get('name', '')} (ID {space.get('id', '')})")
            for space in spaces
        ],
    )
    if selected_space_id is None:
        return False
    selected_space = next(space for space in spaces if str(space.get("id", "")) == selected_space_id)
    pages = list_root_pages(cfg, instance_name, selected_space_id)
    table = Table(title=f"Pages racines - {selected_space.get('key', '')}")
    table.add_column("#")
    table.add_column("Titre")
    table.add_column("ID")
    for idx, page in enumerate(pages, 1):
        table.add_row(str(idx), str(page.get("title", "")), str(page.get("id", "")))
    console.print(table)
    if not pages or not set_default:
        return False
    confirm = _confirm(cfg, _field(cfg, "set_root"), False)
    if confirm is not True:
        return False
    selected_page_id = select_option(
        _field(cfg, "select_page"),
        [(str(page.get("id", "")), f"{page.get('title', '')} (ID {page.get('id', '')})") for page in pages],
    )
    if selected_page_id is None:
        return False
    item = cfg["confluence"]["instances"][instance_name]
    item["default_space"] = str(selected_space.get("key", ""))
    item["root_page"] = selected_page_id
    return True


def _instances_menu(cfg: dict[str, Any]) -> bool:
    dirty = False
    while True:
        choice = select_option(
            tr(cfg, "settings.instances"),
            [
                ("list", tr(cfg, "instances.list")),
                ("add", tr(cfg, "instances.add")),
                ("modify", tr(cfg, "instances.modify")),
                ("default", tr(cfg, "instances.default")),
                ("delete", tr(cfg, "instances.delete")),
                ("root", tr(cfg, "instances.root")),
                ("back", tr(cfg, "instances.back")),
            ],
        )
        if choice in (None, "back"):
            return dirty
        if choice == "list":
            _list_instances(cfg)
        elif choice == "add":
            dirty = _add_instance(cfg) or dirty
        elif choice == "modify":
            dirty = _modify_instance(cfg) or dirty
        elif choice == "default":
            dirty = _set_default_instance(cfg) or dirty
        elif choice == "delete":
            dirty = _delete_instance(cfg) or dirty
        elif choice == "root":
            name = _select_instance_name(cfg, tr(cfg, "instances.root"))
            if name:
                dirty = choose_space_root(cfg, name, set_default=True) or dirty


def _edit_confluence_publish(cfg: dict[str, Any]) -> bool:
    cf = cfg.setdefault("confluence", {})
    layout = cf.setdefault("layout", {})
    value = _confirm(cfg, _field(cfg, "keep_hierarchy"), bool(cf.get("keep_hierarchy", False)))
    if value is not None:
        cf["keep_hierarchy"] = value
    value = _confirm(cfg, _field(cfg, "overwrite_manual"), bool(cf.get("overwrite_manual_changes", False)))
    if value is not None:
        cf["overwrite_manual_changes"] = value
    value = _confirm(cfg, _field(cfg, "page_id"), bool(cf.get("write_page_id_to_markdown", True)))
    if value is not None:
        cf["write_page_id_to_markdown"] = value
    value = _confirm(cfg, _field(cfg, "heading_anchors"), bool(cf.get("heading_anchors", True)))
    if value is not None:
        cf["heading_anchors"] = value

    comments = select_option(
        _field(cfg, "comments"),
        [("remove", "remove"), ("check-open", "check-open")],
        default_index=1 if str(cf.get("comments") or "remove") == "check-open" else 0,
    )
    if comments is not None:
        cf["comments"] = comments

    alignment_values = ["center", "left", "right"]
    current_alignment = str(layout.get("alignment") or "center")
    alignment = select_option(
        _field(cfg, "alignment"),
        [(value, value) for value in alignment_values],
        default_index=alignment_values.index(current_alignment) if current_alignment in alignment_values else 0,
    )
    if alignment is not None:
        layout["alignment"] = alignment
        layout["image_alignment"] = alignment
    layout["image_max_width"] = int(Prompt.ask(_field(cfg, "image_max_width"), default=str(layout.get("image_max_width") or 1600)))
    table_modes = ["responsive", "fixed"]
    current_mode = str(layout.get("table_display_mode") or "responsive")
    mode = select_option(
        _field(cfg, "table_mode"),
        [(value, value) for value in table_modes],
        default_index=table_modes.index(current_mode) if current_mode in table_modes else 0,
    )
    if mode is not None:
        layout["table_display_mode"] = mode
    table_width = _edit_text(cfg, _field(cfg, "table_width"), str(layout.get("table_width") or ""), allow_clear=True)
    layout["table_width"] = int(table_width) if table_width else None
    return True


def _show_doctor(cfg: dict[str, Any], config_path: Path) -> None:
    table = Table(title="DocSpecBridge doctor")
    table.add_column("Elément")
    table.add_column("Valeur")
    for key, value in doctor_info(cfg, config_path=config_path).items():
        table.add_row(key, value)
    console.print(table)


def config_menu(path: Path | None = None) -> Path:
    config_path = selected_config_path(path)
    if not config_path.exists():
        init_config(config_path)
    cfg = load_config(config_path)
    original = deepcopy(cfg)
    dirty = False
    ensure_workdirs(cfg)

    while True:
        choice = select_option(
            tr(cfg, "settings.title"),
            [
                ("summary", tr(cfg, "settings.summary")),
                ("language", tr(cfg, "settings.language")),
                ("app", tr(cfg, "settings.app")),
                ("profiles", tr(cfg, "settings.profiles")),
                ("instances", tr(cfg, "settings.instances")),
                ("confluence_publish", tr(cfg, "settings.confluence_publish")),
                ("doctor", tr(cfg, "settings.doctor")),
                ("save", tr(cfg, "settings.save")),
                ("save_return", tr(cfg, "settings.save_return")),
                ("cancel", tr(cfg, "settings.cancel_return")),
            ],
        )
        if choice is None:
            choice = "cancel"

        changed = False
        if choice == "summary":
            _show_summary(cfg, config_path)
        elif choice == "language":
            changed = _edit_language(cfg)
        elif choice == "app":
            changed = _edit_app(cfg)
        elif choice == "profiles":
            changed = _edit_profiles(cfg)
        elif choice == "instances":
            changed = _instances_menu(cfg)
        elif choice == "confluence_publish":
            changed = _edit_confluence_publish(cfg)
        elif choice == "doctor":
            _show_doctor(cfg, config_path)
        elif choice == "save":
            save_config(cfg, config_path)
            original = deepcopy(cfg)
            dirty = False
            console.print(f"[green]{tr(cfg, 'settings.saved', path=config_path)}[/green]")
        elif choice == "save_return":
            save_config(cfg, config_path)
            console.print(f"[green]{tr(cfg, 'settings.saved', path=config_path)}[/green]")
            return config_path
        elif choice == "cancel":
            if dirty:
                confirm = _confirm(cfg, tr(cfg, "settings.cancel_return"), False)
                if confirm is not True:
                    continue
                cfg = original
                console.print(f"[yellow]{tr(cfg, 'settings.discard')}[/yellow]")
            return config_path

        if changed:
            dirty = True
            console.print(f"[yellow]{tr(cfg, 'settings.pending')}[/yellow]")

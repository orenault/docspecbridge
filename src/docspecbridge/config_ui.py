from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from rich.console import Console
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
from .jira import jira_instances, list_issue_types, list_projects
from .doctor import doctor_info
from .i18n import SUPPORTED_LANGUAGES, config_language, tr
from .ui import UserCancelled, prompt_text, select_option

console = Console()

_FIELD_TEXT: dict[str, dict[str, str]] = {
    "fr": {
        "source": "Source par défaut",
        "destination": "Destination par défaut",
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
        "source": "Standardquelle", "destination": "Standardziel",
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
        "source": "Origen predeterminado", "destination": "Destino predeterminado",
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
        "source": "默认源目录", "destination": "默认目标目录", "recursive": "递归搜索",
        "tree": "保留源目录结构", "copy": "将源文件复制到包中", "pub": "生成发布 Markdown", "image_size": "保留图片显示尺寸",
        "rag": "生成 RAG 配置", "rag_images": "在 rag.md 中保留图片引用", "header_images": "在 rag.md 中保留页眉图片", "chunks": "生成 chunks.jsonl",
        "chunk_size": "最大块大小（字符）", "overlap": "重叠（字符）", "raw": "保留 Xberg 原始 Markdown（诊断）", "toc": "将检测到的目录替换为 Confluence 原生目录", "rag_export_destination": "RAG 语料目标目录",
        "instance_name": "实例逻辑名称", "domain": "Confluence Cloud 域名", "email": "Atlassian 账户邮箱", "token_env": "保存 token 的环境变量",
        "cloud_id": "Atlassian Cloud ID", "space": "默认空间（可选）", "root": "根页面 ID（可选）", "classic": "经典 API token（Basic：邮箱 + token）",
        "scoped": "Scoped API token（Cloud ID + 邮箱 + token）", "auth": "Atlassian token 类型", "rename": "新逻辑名称",
        "delete_confirm": "确认删除", "set_root": "将某页面设为默认导入根页面？", "select_space": "选择空间", "select_page": "选择根页面",
    },
}

# 0.3 additions shared by the settings UI. Keeping them here (rather than hard-coded
# prompts) ensures the selected application language is used for every interaction.
_FIELD_TEXT["fr"].update({"page_depth": "Niveau de pages listable (0/1/2)", "page_width": "Largeur de page Confluence"})
_FIELD_TEXT["en"].update({"page_depth": "Selectable page depth (0/1/2)", "page_width": "Confluence page width"})
_FIELD_TEXT["de"].update({"page_depth": "Auflistbare Seitentiefe (0/1/2)", "page_width": "Confluence-Seitenbreite"})
_FIELD_TEXT["es"].update({"page_depth": "Profundidad de páginas listables (0/1/2)", "page_width": "Ancho de página Confluence"})
_FIELD_TEXT["zh"].update({"page_depth": "可列出的页面深度 (0/1/2)", "page_width": "Confluence 页面宽度"})

# 0.4.2 publication policy/title settings.
_FIELD_TEXT["fr"].update({
    "publication_mode": "Mode de publication par défaut",
    "title_source": "Nom de page Confluence",
    "title_source_document": "Titre du document (fallback : nom du fichier)",
    "title_source_filename": "Nom du fichier",
    "verify_publish": "Vérifier la publication après envoi",
    "add_suffix": "Suffixe automatique en mode Ajouter ({n} = compteur)",
})
_FIELD_TEXT["en"].update({
    "publication_mode": "Default publication mode",
    "title_source": "Confluence page name",
    "title_source_document": "Document title (fallback: filename)",
    "title_source_filename": "Filename",
    "verify_publish": "Verify publication after upload",
    "add_suffix": "Automatic suffix in Add mode ({n} = counter)",
})
_FIELD_TEXT["de"].update({
    "publication_mode": "Standard-Veröffentlichungsmodus",
    "title_source": "Confluence-Seitenname",
    "title_source_document": "Dokumenttitel (Fallback: Dateiname)",
    "title_source_filename": "Dateiname",
    "verify_publish": "Veröffentlichung nach Upload prüfen",
    "add_suffix": "Automatisches Suffix im Hinzufügen-Modus ({n} = Zähler)",
})
_FIELD_TEXT["es"].update({
    "publication_mode": "Modo de publicación predeterminado",
    "title_source": "Nombre de página Confluence",
    "title_source_document": "Título del documento (fallback: nombre de archivo)",
    "title_source_filename": "Nombre del archivo",
    "verify_publish": "Verificar la publicación tras el envío",
    "add_suffix": "Sufijo automático en modo Añadir ({n} = contador)",
})
_FIELD_TEXT["zh"].update({
    "publication_mode": "默认发布模式",
    "title_source": "Confluence 页面名称",
    "title_source_document": "文档标题（回退：文件名）",
    "title_source_filename": "文件名",
    "verify_publish": "上传后验证发布结果",
    "add_suffix": "添加模式自动后缀（{n} = 计数器）",
})


def _field(cfg: dict[str, Any], key: str) -> str:
    lang = config_language(cfg)
    return _FIELD_TEXT.get(lang, _FIELD_TEXT["en"]).get(key, _FIELD_TEXT["en"].get(key, key))


def _select_action(title: str, options, *, default_index: int = 0):
    value = select_option(title, options, default_index=default_index)
    if value is None:
        raise UserCancelled()
    return value


def _confirm(cfg: dict[str, Any], label: str, default: bool = True) -> bool:
    options = [(True, tr(cfg, "common.yes")), (False, tr(cfg, "common.no"))]
    return bool(_select_action(label, options, default_index=0 if default else 1))


def _edit_text(cfg: dict[str, Any], label: str, current: str = "", *, allow_clear: bool = True) -> str:
    hint = tr(cfg, "common.keep_clear_hint") if allow_clear else ""
    prompt = f"{label} [{hint}]" if hint else label
    value = prompt_text(prompt, current).strip()
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
    table.add_column(tr(cfg, "common.parameter"))
    table.add_column(tr(cfg, "common.value"))
    table.add_row("language", f"{app.get('language')} - {SUPPORTED_LANGUAGES.get(config_language(cfg), '')}")
    source = Path(str(app.get("source")))
    dest = Path(str(app.get("destination")))
    table.add_row("source", f"{source} ({'OK' if source.exists() else 'absent'})")
    table.add_row("destination", f"{dest} ({'OK' if dest.exists() else 'absent'})")
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
    publication_policy = cf.get("publication") or {}
    table.add_row("confluence.publication.mode", str(publication_policy.get("default_mode") or "replace"))
    table.add_row("confluence.publication.title_source", str(publication_policy.get("page_title_source") or "document_title"))
    table.add_row("confluence.publication.verify", str(bool(publication_policy.get("verify_after_publish", True))))
    table.add_row("confluence.comments", str(cf.get("comments") or "remove"))
    table.add_row("confluence.page_width", str(cf.get("page_width") or "max"))
    table.add_row("confluence.page_selector.max_depth", str((cf.get("page_selector") or {}).get("max_depth", 0)))
    console.print(table)

    instances = confluence_instances(cfg)
    instance_table = Table(title="Confluence Cloud")
    instance_table.add_column(tr(cfg, "common.name"))
    instance_table.add_column(tr(cfg, "common.default"))
    instance_table.add_column("Auth")
    instance_table.add_column(tr(cfg, "common.domain"))
    instance_table.add_column(tr(cfg, "common.user"))
    instance_table.add_column("Token env")
    instance_table.add_column(tr(cfg, "common.space"))
    instance_table.add_column(tr(cfg, "common.root_page"))
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
    selected = _select_action(
        tr(cfg, "settings.language"),
        [(code, f"{label} ({code.upper()})") for code, label in SUPPORTED_LANGUAGES.items()],
        default_index=default_idx,
    )
    cfg["app"]["language"] = selected
    console.print(f"[green]{tr(cfg, 'settings.language_saved', language=SUPPORTED_LANGUAGES[selected])}[/green]")
    return True


def _edit_app(cfg: dict[str, Any]) -> bool:
    app = cfg["app"]
    app["source"] = _edit_text(cfg, _field(cfg, "source"), str(app.get("source") or "./input"), allow_clear=False)
    app["destination"] = _edit_text(cfg, _field(cfg, "destination"), str(app.get("destination") or "./output"), allow_clear=False)
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
        chunking["max_characters"] = int(prompt_text(_field(cfg, "chunk_size"), str(chunking.get("max_characters", 1600))))
        chunking["overlap"] = int(prompt_text(_field(cfg, "overlap"), str(chunking.get("overlap", 150))))
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
    for col in (
        tr(cfg, "common.name"), tr(cfg, "common.default"), "Auth", tr(cfg, "common.domain"),
        tr(cfg, "common.user"), "Token env", "Cloud ID", tr(cfg, "common.space"), tr(cfg, "common.root_page")
    ):
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
        console.print(f"[yellow]{tr(cfg, 'instances.none')}[/yellow]")
        return None
    default_name = str((cfg.get("confluence") or {}).get("default_instance") or names[0])
    default_idx = names.index(default_name) if default_name in names else 0
    return select_option(title, [(name, name) for name in names], default_index=default_idx)


def _edit_auth_fields(cfg: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    auth_current = str(item.get("auth_type") or "classic")
    auth = _select_action(
        _field(cfg, "auth"),
        [("classic", _field(cfg, "classic")), ("scoped", _field(cfg, "scoped"))],
        default_index=1 if auth_current == "scoped" else 0,
    )
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
    name = prompt_text(_field(cfg, "instance_name"), suggested).strip()
    if not name:
        return False
    if name in instances:
        console.print(f"[red]{tr(cfg, 'instances.exists', name=name)}[/red]")
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
        console.print(f"[red]{tr(cfg, 'instances.exists', name=new_name)}[/red]")
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
        console.print(f"[yellow]{tr(cfg, 'spaces.none')}[/yellow]")
        return False
    item = cfg.setdefault("confluence", {}).setdefault("instances", {}).get(instance_name) or {}
    default_space = str(item.get("default_space") or "")
    if default_space:
        console.print(f"[dim]{tr(cfg, 'confluence.current_default', value=default_space)}[/dim]")
    default_space_idx = next((idx for idx, space in enumerate(spaces) if str(space.get("key") or "") == default_space), 0)
    selected_space_id = _select_action(
        _field(cfg, "select_space"),
        [
            (str(space.get("id", "")), f"{space.get('key', '')} - {space.get('name', '')} (ID {space.get('id', '')})")
            for space in spaces
        ],
        default_index=default_space_idx,
    )
    selected_space = next(space for space in spaces if str(space.get("id", "")) == selected_space_id)
    max_depth = int(((cfg.get("confluence") or {}).get("page_selector") or {}).get("max_depth", 0))
    pages = list_root_pages(cfg, instance_name, selected_space_id, max_depth=max_depth)
    if not pages:
        console.print(f"[yellow]{tr(cfg, 'confluence.no_pages')}[/yellow]")
        return False
    if not set_default:
        table = Table(title=tr(cfg, "confluence.pages_title", space=selected_space.get("key", ""), depth=max_depth))
        table.add_column("#")
        table.add_column(tr(cfg, "common.title"))
        table.add_column(tr(cfg, "common.level"))
        table.add_column("ID")
        for idx, page in enumerate(pages, 1):
            table.add_row(
                str(idx),
                str(page.get("tree_label") or page.get("title", "")),
                str(page.get("level", page.get("depth", 0))),
                str(page.get("id", "")),
            )
        console.print(table)
        return False

    same_space = str(selected_space.get("key") or "") == default_space
    default_page = str(item.get("root_page") or "") if same_space else ""
    default_page = default_page or str(selected_space.get("homepageId") or "")
    if default_page:
        default_obj = next((page for page in pages if str(page.get("id") or "") == default_page), None)
        default_label = str((default_obj or {}).get("tree_label") or (default_obj or {}).get("title") or default_page)
        console.print(f"[dim]{tr(cfg, 'confluence.current_default', value=default_label)}[/dim]")
    default_page_idx = next((idx for idx, page in enumerate(pages) if str(page.get("id") or "") == default_page), 0)
    selected_page_id = _select_action(
        _field(cfg, "select_page"),
        [(
            str(page.get("id", "")),
            f"{page.get('tree_label') or page.get('title', '')} (ID {page.get('id', '')})",
        ) for page in pages],
        default_index=default_page_idx,
    )
    confirm = _confirm(cfg, _field(cfg, "set_root"), True)
    if confirm is not True:
        return False
    target = cfg["confluence"]["instances"][instance_name]
    target["default_space"] = str(selected_space.get("key", ""))
    target["root_page"] = selected_page_id
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



def _jira_defaults(cfg: dict[str, Any], instance_name: str) -> dict[str, Any]:
    jira = cfg.setdefault("jira", {})
    defaults = jira.setdefault("defaults", {})
    value = defaults.setdefault(instance_name, {})
    if not isinstance(value, dict):
        value = {}
        defaults[instance_name] = value
    return value


def _select_jira_instance_name(cfg: dict[str, Any], title: str) -> str | None:
    names = list(jira_instances(cfg))
    if not names:
        console.print(f"[yellow]{tr(cfg, 'jira.no_instance')}[/yellow]")
        return None
    configured = str((cfg.get("jira") or {}).get("default_instance") or "")
    inherited_default = str((cfg.get("confluence") or {}).get("default_instance") or "")
    default_name = configured or inherited_default or names[0]
    default_idx = names.index(default_name) if default_name in names else 0
    return select_option(title, [(name, name) for name in names], default_index=default_idx)


def _list_jira_settings(cfg: dict[str, Any]) -> None:
    effective = jira_instances(cfg)
    explicit = (cfg.get("jira") or {}).get("instances") or {}
    default_name = str((cfg.get("jira") or {}).get("default_instance") or (cfg.get("confluence") or {}).get("default_instance") or "")
    table = Table(title=tr(cfg, "jira.settings.summary"))
    table.add_column(tr(cfg, "common.name"))
    table.add_column(tr(cfg, "common.default"))
    table.add_column("Source")
    table.add_column(tr(cfg, "common.domain"))
    table.add_column(tr(cfg, "common.user"))
    table.add_column(tr(cfg, "interactive.project_select"))
    table.add_column(tr(cfg, "interactive.issue_type_select"))
    for name, item in effective.items():
        defaults = _jira_defaults(cfg, name)
        source = "Jira" if name in explicit else tr(cfg, "jira.settings.inherited")
        table.add_row(
            name,
            "*" if name == default_name else "",
            source,
            str(item.get("domain") or ""),
            str(item.get("user_name") or ""),
            str(defaults.get("project") or ""),
            str(defaults.get("issue_type") or ""),
        )
    console.print(table)


def _edit_jira_auth_fields(cfg: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    item["domain"] = normalize_domain(_edit_text(cfg, _field(cfg, "domain"), str(item.get("domain") or ""), allow_clear=True))
    item["user_name"] = _edit_text(cfg, _field(cfg, "email"), str(item.get("user_name") or ""), allow_clear=True)
    item["token_env"] = _edit_text(cfg, _field(cfg, "token_env"), str(item.get("token_env") or "ATLASSIAN_API_TOKEN"), allow_clear=False)
    item["auth_type"] = "classic"
    return item


def _add_jira_instance(cfg: dict[str, Any]) -> bool:
    jira = cfg.setdefault("jira", {})
    explicit = jira.setdefault("instances", {})
    effective = jira_instances(cfg)
    suggested = "production" if "production" not in effective else "jira"
    name = prompt_text(_field(cfg, "instance_name"), suggested).strip()
    if not name:
        return False
    if name in explicit:
        console.print(f"[red]{tr(cfg, 'instances.exists', name=name)}[/red]")
        return False
    base = dict(effective.get(name) or {})
    explicit[name] = _edit_jira_auth_fields(cfg, base)
    if not jira.get("default_instance"):
        jira["default_instance"] = name
    return True


def _modify_jira_instance(cfg: dict[str, Any]) -> bool:
    name = _select_jira_instance_name(cfg, tr(cfg, "instances.modify"))
    if not name:
        return False
    jira = cfg.setdefault("jira", {})
    explicit = jira.setdefault("instances", {})
    current = dict(jira_instances(cfg).get(name) or {})
    explicit[name] = _edit_jira_auth_fields(cfg, current)
    return True


def _delete_jira_instance(cfg: dict[str, Any]) -> bool:
    name = _select_jira_instance_name(cfg, tr(cfg, "instances.delete"))
    if not name:
        return False
    jira = cfg.setdefault("jira", {})
    explicit = jira.setdefault("instances", {})
    if name not in explicit:
        console.print(f"[yellow]{tr(cfg, 'jira.settings.inherited')}[/yellow]")
        return False
    confirm = _confirm(cfg, f"{_field(cfg, 'delete_confirm')} : {name}", False)
    if confirm is not True:
        return False
    explicit.pop(name, None)
    if jira.get("default_instance") == name and name not in jira_instances(cfg):
        jira["default_instance"] = next(iter(jira_instances(cfg)), "")
    return True


def _set_jira_default_instance(cfg: dict[str, Any]) -> bool:
    name = _select_jira_instance_name(cfg, tr(cfg, "jira.settings.default_instance"))
    if not name:
        return False
    cfg.setdefault("jira", {})["default_instance"] = name
    return True


def _set_jira_default_project(cfg: dict[str, Any]) -> bool:
    name = _select_jira_instance_name(cfg, tr(cfg, "jira.settings.default_project"))
    if not name:
        return False
    projects = list_projects(cfg, name)
    if not projects:
        console.print(f"[yellow]{tr(cfg, 'jira.no_project')}[/yellow]")
        return False
    defaults = _jira_defaults(cfg, name)
    current = str(defaults.get("project") or "")
    if current:
        console.print(f"[dim]{tr(cfg, 'jira.current_default', value=current)}[/dim]")
    default_idx = next((idx for idx, row in enumerate(projects) if str(row.get("key") or row.get("id") or "") == current), 0)
    project = _select_action(
        tr(cfg, "interactive.project_select"),
        [(str(row.get("key") or row.get("id") or ""), f"{row.get('key', '')} — {row.get('name', '')}") for row in projects],
        default_index=default_idx,
    )
    defaults["project"] = str(project)
    # Issue types are project-scoped; clear a stale type when the project changes.
    if str(project) != current:
        defaults["issue_type"] = ""
    console.print(f"[green]{tr(cfg, 'jira.settings.saved_project', project=project)}[/green]")
    return True


def _set_jira_default_issue_type(cfg: dict[str, Any]) -> bool:
    name = _select_jira_instance_name(cfg, tr(cfg, "jira.settings.default_issue_type"))
    if not name:
        return False
    defaults = _jira_defaults(cfg, name)
    project = str(defaults.get("project") or "")
    if not project:
        projects = list_projects(cfg, name)
        if not projects:
            console.print(f"[yellow]{tr(cfg, 'jira.no_project')}[/yellow]")
            return False
        project = str(_select_action(
            tr(cfg, "interactive.project_select"),
            [(str(row.get("key") or row.get("id") or ""), f"{row.get('key', '')} — {row.get('name', '')}") for row in projects],
            default_index=0,
        ))
        defaults["project"] = project
    types = list_issue_types(cfg, project, name)
    if not types:
        console.print(f"[yellow]{tr(cfg, 'jira.no_issue_type', project=project)}[/yellow]")
        return False
    current = str(defaults.get("issue_type") or "")
    default_idx = next((idx for idx, row in enumerate(types) if str(row.get("name") or row.get("id") or "") == current), 0)
    issue_type = _select_action(
        tr(cfg, "interactive.issue_type_select"),
        [(str(row.get("name") or row.get("id") or ""), str(row.get("name") or row.get("id") or "")) for row in types],
        default_index=default_idx,
    )
    defaults["issue_type"] = str(issue_type)
    console.print(f"[green]{tr(cfg, 'jira.settings.saved_issue_type', issue_type=issue_type)}[/green]")
    return True


def _jira_settings_menu(cfg: dict[str, Any]) -> bool:
    dirty = False
    while True:
        snapshot = deepcopy(cfg)
        choice = select_option(
            tr(cfg, "jira.settings"),
            [
                ("summary", tr(cfg, "jira.settings.summary")),
                ("add", tr(cfg, "instances.add")),
                ("modify", tr(cfg, "instances.modify")),
                ("delete", tr(cfg, "instances.delete")),
                ("default_instance", tr(cfg, "jira.settings.default_instance")),
                ("default_project", tr(cfg, "jira.settings.default_project")),
                ("default_issue_type", tr(cfg, "jira.settings.default_issue_type")),
                ("back", tr(cfg, "common.back")),
            ],
        )
        if choice in (None, "back"):
            return dirty
        try:
            changed = False
            if choice == "summary":
                _list_jira_settings(cfg)
            elif choice == "add":
                changed = _add_jira_instance(cfg)
            elif choice == "modify":
                changed = _modify_jira_instance(cfg)
            elif choice == "delete":
                changed = _delete_jira_instance(cfg)
            elif choice == "default_instance":
                changed = _set_jira_default_instance(cfg)
            elif choice == "default_project":
                changed = _set_jira_default_project(cfg)
            elif choice == "default_issue_type":
                changed = _set_jira_default_issue_type(cfg)
            dirty = changed or dirty
        except UserCancelled:
            cfg.clear()
            cfg.update(snapshot)
            console.print(f"[dim]{tr(cfg, 'interactive.cancelled')}[/dim]")

def _edit_confluence_publish(cfg: dict[str, Any]) -> bool:
    cf = cfg.setdefault("confluence", {})
    layout = cf.setdefault("layout", {})
    selector = cf.setdefault("page_selector", {})
    publication = cf.setdefault("publication", {})

    modes = ["replace", "add"]
    current_publication_mode = str(publication.get("default_mode") or "replace")
    selected_mode = _select_action(
        _field(cfg, "publication_mode"),
        [("replace", "replace"), ("add", "add")],
        default_index=modes.index(current_publication_mode) if current_publication_mode in modes else 0,
    )
    if selected_mode is not None:
        publication["default_mode"] = selected_mode

    title_sources = ["document_title", "filename"]
    current_title_source = str(publication.get("page_title_source") or "document_title")
    selected_title_source = _select_action(
        _field(cfg, "title_source"),
        [
            ("document_title", _field(cfg, "title_source_document")),
            ("filename", _field(cfg, "title_source_filename")),
        ],
        default_index=title_sources.index(current_title_source) if current_title_source in title_sources else 0,
    )
    if selected_title_source is not None:
        publication["page_title_source"] = selected_title_source

    publication["add_title_suffix"] = _edit_text(
        cfg, _field(cfg, "add_suffix"), str(publication.get("add_title_suffix") or " ({n})"), allow_clear=False
    )
    verify = _confirm(cfg, _field(cfg, "verify_publish"), bool(publication.get("verify_after_publish", True)))
    if verify is not None:
        publication["verify_after_publish"] = verify

    depths = [0, 1, 2]
    current_depth = max(0, min(2, int(selector.get("max_depth", 0))))
    depth = _select_action(
        _field(cfg, "page_depth"),
        [(value, str(value)) for value in depths],
        default_index=current_depth,
    )
    if depth is not None:
        selector["max_depth"] = int(depth)

    widths = ["narrow", "wide", "max", "confluence-default"]
    current_width = str(cf.get("page_width") or "max")
    width_labels = {
        "narrow": tr(cfg, "page_width.narrow"),
        "wide": tr(cfg, "page_width.wide"),
        "max": tr(cfg, "page_width.max"),
        "confluence-default": tr(cfg, "page_width.default"),
    }
    width = _select_action(
        _field(cfg, "page_width"),
        [(value, width_labels[value]) for value in widths],
        default_index=widths.index(current_width) if current_width in widths else 2,
    )
    if width is not None:
        cf["page_width"] = width
    value = _confirm(cfg, _field(cfg, "keep_hierarchy"), bool(cf.get("keep_hierarchy", False)))
    if value is not None:
        cf["keep_hierarchy"] = value
    value = _confirm(cfg, _field(cfg, "overwrite_manual"), bool(cf.get("overwrite_manual_changes", False)))
    if value is not None:
        cf["overwrite_manual_changes"] = value
    value = _confirm(cfg, _field(cfg, "heading_anchors"), bool(cf.get("heading_anchors", True)))
    if value is not None:
        cf["heading_anchors"] = value

    comments = _select_action(
        _field(cfg, "comments"),
        [("remove", "remove"), ("check-open", "check-open")],
        default_index=1 if str(cf.get("comments") or "remove") == "check-open" else 0,
    )
    if comments is not None:
        cf["comments"] = comments

    alignment_values = ["center", "left", "right"]
    current_alignment = str(layout.get("alignment") or "center")
    alignment = _select_action(
        _field(cfg, "alignment"),
        [(value, value) for value in alignment_values],
        default_index=alignment_values.index(current_alignment) if current_alignment in alignment_values else 0,
    )
    if alignment is not None:
        layout["alignment"] = alignment
        layout["image_alignment"] = alignment
    layout["image_max_width"] = int(prompt_text(_field(cfg, "image_max_width"), str(layout.get("image_max_width") or 1600)))
    table_modes = ["responsive", "fixed"]
    current_mode = str(layout.get("table_display_mode") or "responsive")
    mode = _select_action(
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
    table.add_column(tr(cfg, "doctor.element"))
    table.add_column(tr(cfg, "doctor.value"))
    for key, value in doctor_info(cfg, config_path=config_path).items():
        table.add_row(key, value)
    console.print(table)


def config_menu(path: Path | None = None) -> Path:
    config_path = selected_config_path(path)
    if not config_path.exists():
        init_config(config_path)
    cfg = load_config(config_path)
    migration = ((cfg.get("_runtime") or {}).get("config_migration") or {})
    if migration:
        console.print(f"[yellow]{tr(cfg, 'config.migrated', old=migration.get('from_schema'), new=migration.get('to_schema'))}[/yellow]")
        console.print(f"[dim]{tr(cfg, 'config.backup_created', path=migration.get('backup'))}[/dim]")
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
                ("jira", tr(cfg, "jira.settings")),
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
        snapshot = deepcopy(cfg)
        try:
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
            elif choice == "jira":
                changed = _jira_settings_menu(cfg)
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
        except UserCancelled:
            cfg.clear()
            cfg.update(snapshot)
            console.print(f"[dim]{tr(cfg, 'interactive.cancelled')}[/dim]")
            continue

        if changed:
            dirty = True
            console.print(f"[yellow]{tr(cfg, 'settings.pending')}[/yellow]")

# Keep every settings field available in all five UI languages.
_FIELD_TEXT["de"].update({
    "keep_hierarchy": "Hierarchie im Batch-Modus beibehalten",
    "overwrite_manual": "Manuelle Confluence-Änderungen überschreiben",
    "page_id": "Confluence page_id im erzeugten Markdown speichern",
    "heading_anchors": "Anker zu Überschriften hinzufügen",
    "comments": "Behandlung von Inline-Kommentaren",
    "alignment": "Globale Ausrichtung",
    "image_max_width": "Maximale Bildbreite (px)",
    "table_mode": "Tabellenanzeigemodus",
    "table_width": "Maximale Tabellenbreite (px, - für keine)",
})
_FIELD_TEXT["es"].update({
    "keep_hierarchy": "Conservar jerarquía en modo por lotes",
    "overwrite_manual": "Sobrescribir cambios manuales de Confluence",
    "page_id": "Guardar page_id de Confluence en el Markdown generado",
    "heading_anchors": "Añadir anclas a los encabezados",
    "comments": "Gestión de comentarios en línea",
    "alignment": "Alineación global",
    "image_max_width": "Ancho máximo de imagen (px)",
    "table_mode": "Modo de visualización de tablas",
    "table_width": "Ancho máximo de tabla (px, - para ninguno)",
})
_FIELD_TEXT["zh"].update({
    "keep_hierarchy": "批量模式下保留层级结构",
    "overwrite_manual": "覆盖 Confluence 中的手工修改",
    "page_id": "在生成的 Markdown 中保存 Confluence page_id",
    "heading_anchors": "为标题添加锚点",
    "comments": "行内评论处理",
    "alignment": "全局对齐方式",
    "image_max_width": "图片最大宽度（px）",
    "table_mode": "表格显示模式",
    "table_width": "表格最大宽度（px，- 表示不限制）",
})

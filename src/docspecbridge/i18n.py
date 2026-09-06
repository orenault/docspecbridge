from __future__ import annotations

import locale
import os
from typing import Any

SUPPORTED_LANGUAGES = {
    "fr": "Français",
    "en": "English",
    "de": "Deutsch",
    "es": "Español",
    "zh": "中文",
}

ALIASES = {"sp": "es", "fr_fr": "fr", "en_us": "en", "en_gb": "en", "de_de": "de", "es_es": "es", "zh_cn": "zh", "zh_tw": "zh"}

_TRANSLATIONS: dict[str, dict[str, str]] = {
    "fr": {
        "main.subtitle": "ETL documentaire : Office/PDF -> publication + RAG -> Confluence Cloud",
        "main.settings": "Paramétrage",
        "main.extract": "Extract",
        "main.confluence": "Import Confluence",
        "main.doc2wiki": "Doc2Wiki - Extract + Import Confluence",
        "main.doc2rag": "Doc2RAG - Extract + Export RAG",
        "main.help": "Aide",
        "main.quit": "Quitter",
        "settings.title": "Paramétrage",
        "settings.summary": "Résumé du paramétrage",
        "settings.language": "Définir la langue",
        "settings.app": "Source / destination / extensions",
        "settings.profiles": "Profils publication / RAG",
        "settings.instances": "Instances Confluence Cloud",
        "settings.confluence_publish": "Options de publication Confluence",
        "settings.doctor": "Diagnostic (Doctor)",
        "settings.save": "Sauvegarder",
        "settings.save_return": "Sauvegarder et retour",
        "settings.cancel_return": "Retour sans sauvegarder",
        "settings.pending": "Modifications en attente de sauvegarde.",
        "settings.saved": "Paramétrage sauvegardé : {path}",
        "settings.discard": "Modifications abandonnées.",
        "settings.language_saved": "Langue sélectionnée : {language}",
        "instances.list": "Lister les instances",
        "instances.add": "Ajouter une instance",
        "instances.modify": "Modifier / renommer une instance",
        "instances.default": "Définir l'instance par défaut",
        "instances.delete": "Supprimer une instance",
        "instances.root": "Définir espace / page racine par défaut",
        "instances.back": "Retour",
        "confluence.import": "Importer un document / corpus",
        "confluence.spaces": "Lister les espaces",
        "confluence.roots": "Lister les pages racines / définir le défaut",
        "confluence.back": "Retour",
        "common.back": "Retour",
        "common.none": "(aucun)",
        "common.yes": "Oui",
        "common.no": "Non",
        "common.select": "Sélectionner",
        "common.keep_clear_hint": "Entrée = conserver ; '-' = effacer",
        "help.title": "Aide DocSpecBridge",
    },
    "en": {
        "main.subtitle": "Document ETL: Office/PDF -> publication + RAG -> Confluence Cloud",
        "main.settings": "Settings",
        "main.extract": "Extract",
        "main.confluence": "Confluence import",
        "main.doc2wiki": "Doc2Wiki - Extract + Confluence import",
        "main.doc2rag": "Doc2RAG - Extract + RAG export",
        "main.help": "Help",
        "main.quit": "Quit",
        "settings.title": "Settings",
        "settings.summary": "Settings summary",
        "settings.language": "Set language",
        "settings.app": "Source / destination / extensions",
        "settings.profiles": "Publication / RAG profiles",
        "settings.instances": "Confluence Cloud instances",
        "settings.confluence_publish": "Confluence publishing options",
        "settings.doctor": "Diagnostics (Doctor)",
        "settings.save": "Save",
        "settings.save_return": "Save and return",
        "settings.cancel_return": "Return without saving",
        "settings.pending": "Changes are pending save.",
        "settings.saved": "Settings saved: {path}",
        "settings.discard": "Changes discarded.",
        "settings.language_saved": "Selected language: {language}",
        "instances.list": "List instances",
        "instances.add": "Add instance",
        "instances.modify": "Modify / rename instance",
        "instances.default": "Set default instance",
        "instances.delete": "Delete instance",
        "instances.root": "Set default space / root page",
        "instances.back": "Back",
        "confluence.import": "Import document / corpus",
        "confluence.spaces": "List spaces",
        "confluence.roots": "List root pages / set default",
        "confluence.back": "Back",
        "common.back": "Back",
        "common.none": "(none)",
        "common.yes": "Yes",
        "common.no": "No",
        "common.select": "Select",
        "common.keep_clear_hint": "Enter = keep; '-' = clear",
        "help.title": "DocSpecBridge help",
    },
    "de": {
        "main.subtitle": "Dokument-ETL: Office/PDF -> Veröffentlichung + RAG -> Confluence Cloud",
        "main.settings": "Einstellungen",
        "main.extract": "Extrahieren",
        "main.confluence": "Confluence-Import",
        "main.doc2wiki": "Doc2Wiki - Extraktion + Confluence",
        "main.doc2rag": "Doc2RAG - Extraktion + RAG-Export",
        "main.help": "Hilfe",
        "main.quit": "Beenden",
        "settings.title": "Einstellungen",
        "settings.summary": "Zusammenfassung",
        "settings.language": "Sprache festlegen",
        "settings.app": "Quelle / Ziel / Erweiterungen",
        "settings.profiles": "Veröffentlichungs- / RAG-Profile",
        "settings.instances": "Confluence-Cloud-Instanzen",
        "settings.confluence_publish": "Confluence-Veröffentlichungsoptionen",
        "settings.doctor": "Diagnose (Doctor)",
        "settings.save": "Speichern",
        "settings.save_return": "Speichern und zurück",
        "settings.cancel_return": "Zurück ohne Speichern",
        "settings.pending": "Änderungen sind noch nicht gespeichert.",
        "settings.saved": "Einstellungen gespeichert: {path}",
        "settings.discard": "Änderungen verworfen.",
        "settings.language_saved": "Gewählte Sprache: {language}",
        "instances.list": "Instanzen auflisten",
        "instances.add": "Instanz hinzufügen",
        "instances.modify": "Instanz ändern / umbenennen",
        "instances.default": "Standardinstanz festlegen",
        "instances.delete": "Instanz löschen",
        "instances.root": "Standardbereich / Stammseite festlegen",
        "instances.back": "Zurück",
        "confluence.import": "Dokument / Korpus importieren",
        "confluence.spaces": "Bereiche auflisten",
        "confluence.roots": "Stammseiten auflisten / Standard festlegen",
        "confluence.back": "Zurück",
        "common.back": "Zurück",
        "common.none": "(keine)",
        "common.yes": "Ja",
        "common.no": "Nein",
        "common.select": "Auswählen",
        "common.keep_clear_hint": "Enter = behalten; '-' = löschen",
        "help.title": "DocSpecBridge Hilfe",
    },
    "es": {
        "main.subtitle": "ETL documental: Office/PDF -> publicación + RAG -> Confluence Cloud",
        "main.settings": "Configuración",
        "main.extract": "Extraer",
        "main.confluence": "Importar a Confluence",
        "main.doc2wiki": "Doc2Wiki - Extraer + importar a Confluence",
        "main.doc2rag": "Doc2RAG - Extraer + exportar RAG",
        "main.help": "Ayuda",
        "main.quit": "Salir",
        "settings.title": "Configuración",
        "settings.summary": "Resumen de configuración",
        "settings.language": "Definir idioma",
        "settings.app": "Origen / destino / extensiones",
        "settings.profiles": "Perfiles publicación / RAG",
        "settings.instances": "Instancias de Confluence Cloud",
        "settings.confluence_publish": "Opciones de publicación Confluence",
        "settings.doctor": "Diagnóstico (Doctor)",
        "settings.save": "Guardar",
        "settings.save_return": "Guardar y volver",
        "settings.cancel_return": "Volver sin guardar",
        "settings.pending": "Hay cambios pendientes de guardar.",
        "settings.saved": "Configuración guardada: {path}",
        "settings.discard": "Cambios descartados.",
        "settings.language_saved": "Idioma seleccionado: {language}",
        "instances.list": "Listar instancias",
        "instances.add": "Añadir instancia",
        "instances.modify": "Modificar / renombrar instancia",
        "instances.default": "Definir instancia predeterminada",
        "instances.delete": "Eliminar instancia",
        "instances.root": "Definir espacio / página raíz predeterminados",
        "instances.back": "Volver",
        "confluence.import": "Importar documento / corpus",
        "confluence.spaces": "Listar espacios",
        "confluence.roots": "Listar páginas raíz / definir predeterminada",
        "confluence.back": "Volver",
        "common.back": "Volver",
        "common.none": "(ninguno)",
        "common.yes": "Sí",
        "common.no": "No",
        "common.select": "Seleccionar",
        "common.keep_clear_hint": "Enter = conservar; '-' = borrar",
        "help.title": "Ayuda DocSpecBridge",
    },
    "zh": {
        "main.subtitle": "文档 ETL：Office/PDF -> 发布 + RAG -> Confluence Cloud",
        "main.settings": "设置",
        "main.extract": "提取",
        "main.confluence": "Confluence 导入",
        "main.doc2wiki": "Doc2Wiki - 提取 + 导入 Confluence",
        "main.doc2rag": "Doc2RAG - 提取 + 导出 RAG",
        "main.help": "帮助",
        "main.quit": "退出",
        "settings.title": "设置",
        "settings.summary": "设置摘要",
        "settings.language": "设置语言",
        "settings.app": "源 / 目标 / 扩展名",
        "settings.profiles": "发布 / RAG 配置",
        "settings.instances": "Confluence Cloud 实例",
        "settings.confluence_publish": "Confluence 发布选项",
        "settings.doctor": "诊断 (Doctor)",
        "settings.save": "保存",
        "settings.save_return": "保存并返回",
        "settings.cancel_return": "不保存返回",
        "settings.pending": "有尚未保存的修改。",
        "settings.saved": "设置已保存：{path}",
        "settings.discard": "修改已放弃。",
        "settings.language_saved": "已选择语言：{language}",
        "instances.list": "列出实例",
        "instances.add": "添加实例",
        "instances.modify": "修改 / 重命名实例",
        "instances.default": "设置默认实例",
        "instances.delete": "删除实例",
        "instances.root": "设置默认空间 / 根页面",
        "instances.back": "返回",
        "confluence.import": "导入文档 / 语料",
        "confluence.spaces": "列出空间",
        "confluence.roots": "列出根页面 / 设置默认",
        "confluence.back": "返回",
        "common.back": "返回",
        "common.none": "（无）",
        "common.yes": "是",
        "common.no": "否",
        "common.select": "选择",
        "common.keep_clear_hint": "回车 = 保留；'-' = 清空",
        "help.title": "DocSpecBridge 帮助",
    },
}


def normalize_language(value: str | None) -> str:
    if not value:
        return "en"
    key = value.strip().lower().replace("-", "_")
    key = ALIASES.get(key, key)
    if key in SUPPORTED_LANGUAGES:
        return key
    prefix = key.split("_", 1)[0]
    prefix = ALIASES.get(prefix, prefix)
    return prefix if prefix in SUPPORTED_LANGUAGES else "en"


def detect_os_language() -> str:
    candidates: list[str] = []
    for env_name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if os.getenv(env_name):
            candidates.append(str(os.getenv(env_name)))
    try:
        current = locale.getlocale()[0]
        if current:
            candidates.append(current)
    except Exception:
        pass
    for candidate in candidates:
        lang = normalize_language(candidate)
        if lang in SUPPORTED_LANGUAGES:
            return lang
    return "en"


def config_language(config: dict[str, Any] | None) -> str:
    if not config:
        return detect_os_language()
    value = str((config.get("app") or {}).get("language") or "auto").strip().lower()
    if value in {"", "auto"}:
        return detect_os_language()
    return normalize_language(value)


def tr(config: dict[str, Any] | None, key: str, **kwargs: Any) -> str:
    lang = config_language(config)
    text = _TRANSLATIONS.get(lang, _TRANSLATIONS["en"]).get(key)
    if text is None:
        text = _TRANSLATIONS["en"].get(key, key)
    return text.format(**kwargs) if kwargs else text

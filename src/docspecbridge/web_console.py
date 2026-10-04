from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import traceback
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse
import webbrowser

import yaml

from . import __version__
from .config import (
    SUPPORTED_SOURCE_EXTENSIONS,
    confluence_instances,
    ensure_workdirs,
    init_config,
    load_config,
    save_config,
    selected_config_path,
)
from .confluence import export_page_to_package, list_root_pages, list_spaces, publish
from .doctor import doctor_info
from .extractor import run_extract
from .git_io import run_extract_git_repository
from .html_io import canonical_from_html_source
from .i18n import SUPPORTED_LANGUAGES, normalize_language
from .jira import (
    create_issue_from_markdown,
    export_issue,
    jira_instances,
    list_issue_types,
    list_issues,
    list_projects,
)
from .package_io import write_canonical_package
from .rag_export import export_rag_corpus
from .utils import safe_stem


WEB_TEXT: dict[str, dict[str, str]] = {
    "fr": {
        "tab.home": "Accueil", "tab.settings": "Paramétrage", "tab.extract": "Extract",
        "tab.import": "Import", "tab.rag": "RAG", "tab.help": "Aide",
        "home.title": "DocSpecBridge — console Web", "home.intro": "Une interface locale pour utiliser les mêmes fonctions principales que le menu CLI, sans afficher la sortie brute d'un processus.",
        "home.what": "Ce que vous pouvez faire", "home.extract": "Extraire des documents locaux, des dépôts Git, des pages Web, Confluence ou Jira vers des packages portables.",
        "home.import": "Publier les packages vers Confluence ou Jira en conservant les images, pièces jointes et états de publication.",
        "home.rag": "Construire un corpus RAG portable à partir des mêmes CanonicalDocument.",
        "home.security": "La console écoute uniquement sur 127.0.0.1. Les tokens restent dans les variables d'environnement et ne sont pas affichés dans le navigateur.",
        "settings.title": "Paramétrage", "settings.quick": "Paramètres courants", "settings.advanced": "YAML avancé",
        "settings.web_language": "Langue de la console Web", "settings.browser": "Automatique (langue du navigateur)",
        "settings.source": "Répertoire source", "settings.destination": "Répertoire output", "settings.recursive": "Recherche locale récursive",
        "settings.git_recursive": "Recherche Markdown récursive dans Git", "settings.depth": "Niveau d'arborescence Confluence à découvrir",
        "settings.rag_destination": "Répertoire RAG", "settings.save": "Sauvegarder", "settings.reload": "Recharger",
        "settings.yaml_hint": "Éditeur complet du fichier YAML. Les secrets doivent rester dans des variables d'environnement.",
        "extract.title": "Extract — sources vers packages", "extract.local": "Documents locaux", "extract.confluence": "Confluence",
        "extract.jira": "Jira", "extract.web": "Web", "extract.git": "Dépôt Git", "extract.force": "Force Extract",
        "import.title": "Import — packages vers cibles", "import.confluence": "Vers Confluence", "import.jira": "Vers Jira",
        "rag.title": "RAG", "rag.export": "Exporter les packages existants", "rag.doc2rag": "Extraire puis exporter RAG",
        "common.run": "Lancer", "common.source": "Source", "common.destination": "Destination", "common.url": "URL",
        "common.instance": "Instance", "common.space": "Espace", "common.page": "Page", "common.project": "Projet",
        "common.issue_type": "Type de ticket", "common.issue": "Ticket", "common.mode": "Mode", "common.title": "Titre",
        "common.parent": "Parent", "common.summary": "Summary", "common.optional": "optionnel", "common.refresh": "Actualiser",
        "common.loading": "Chargement…", "common.none": "Aucun", "common.error": "Erreur", "common.done": "Terminé",
        "common.running": "Traitement en cours", "common.queued": "En attente", "common.warning": "Avertissement",
        "common.result": "Résultat", "common.next": "Suivant", "common.stop": "Arrêter la console", "common.config": "Configuration",
        "jira.manual": "Clé Jira directe", "jira.browse": "Découvrir projet / type / ticket", "jira.search": "Filtre texte",
        "confluence.attachments": "Pièces jointes", "confluence.zip": "Créer aussi un ZIP", "confluence.keep_hierarchy": "Conserver la hiérarchie",
        "git.ref": "Branche / tag / commit", "git.recursive": "Récursif", "help.title": "Aide",
        "help.text": "La console Web appelle directement les services Python de DocSpecBridge : elle ne lance pas le CLI dans un sous-processus. Les résultats sont donc structurés en cartes, tableaux et messages plutôt qu'en texte de terminal.",
        "doctor.title": "Diagnostic", "doctor.refresh": "Actualiser le diagnostic", "status.config_saved": "Paramétrage sauvegardé.",
        "status.server_stopped": "Le serveur DocSpecBridge a été arrêté.",
    },
    "en": {
        "tab.home": "Home", "tab.settings": "Settings", "tab.extract": "Extract", "tab.import": "Import", "tab.rag": "RAG", "tab.help": "Help",
        "home.title": "DocSpecBridge — Web console", "home.intro": "A local interface for the same main workflows as the CLI menu, without displaying raw subprocess output.",
        "home.what": "What you can do", "home.extract": "Extract local documents, Git repositories, Web pages, Confluence or Jira into portable packages.",
        "home.import": "Publish packages to Confluence or Jira while preserving images, attachments and publication state.",
        "home.rag": "Build a portable RAG corpus from the same CanonicalDocument.",
        "home.security": "The console listens only on 127.0.0.1. Tokens stay in environment variables and are not displayed in the browser.",
        "settings.title": "Settings", "settings.quick": "Common settings", "settings.advanced": "Advanced YAML",
        "settings.web_language": "Web console language", "settings.browser": "Automatic (browser language)",
        "settings.source": "Source directory", "settings.destination": "Output directory", "settings.recursive": "Recursive local discovery",
        "settings.git_recursive": "Recursive Markdown discovery in Git", "settings.depth": "Confluence tree depth to discover",
        "settings.rag_destination": "RAG directory", "settings.save": "Save", "settings.reload": "Reload",
        "settings.yaml_hint": "Full YAML file editor. Secrets should remain in environment variables.",
        "extract.title": "Extract — sources to packages", "extract.local": "Local documents", "extract.confluence": "Confluence", "extract.jira": "Jira", "extract.web": "Web", "extract.git": "Git repository", "extract.force": "Force Extract",
        "import.title": "Import — packages to targets", "import.confluence": "To Confluence", "import.jira": "To Jira",
        "rag.title": "RAG", "rag.export": "Export existing packages", "rag.doc2rag": "Extract then export RAG",
        "common.run": "Run", "common.source": "Source", "common.destination": "Destination", "common.url": "URL", "common.instance": "Instance", "common.space": "Space", "common.page": "Page", "common.project": "Project", "common.issue_type": "Issue type", "common.issue": "Issue", "common.mode": "Mode", "common.title": "Title", "common.parent": "Parent", "common.summary": "Summary", "common.optional": "optional", "common.refresh": "Refresh", "common.loading": "Loading…", "common.none": "None", "common.error": "Error", "common.done": "Done", "common.running": "Processing", "common.queued": "Queued", "common.warning": "Warning", "common.result": "Result", "common.next": "Next", "common.stop": "Stop console", "common.config": "Configuration",
        "jira.manual": "Direct Jira key", "jira.browse": "Browse project / type / issue", "jira.search": "Text filter", "confluence.attachments": "Attachments", "confluence.zip": "Also create ZIP", "confluence.keep_hierarchy": "Keep hierarchy", "git.ref": "Branch / tag / commit", "git.recursive": "Recursive",
        "help.title": "Help", "help.text": "The Web console calls DocSpecBridge Python services directly. It does not run the CLI as a subprocess, so results are structured as cards, tables and messages instead of terminal text.", "doctor.title": "Diagnostics", "doctor.refresh": "Refresh diagnostics", "status.config_saved": "Settings saved.", "status.server_stopped": "The DocSpecBridge server has stopped.",
    },
    "de": {
        "tab.home": "Start", "tab.settings": "Einstellungen", "tab.extract": "Extrahieren", "tab.import": "Import", "tab.rag": "RAG", "tab.help": "Hilfe",
        "home.title": "DocSpecBridge — Web-Konsole", "home.intro": "Lokale Oberfläche für die wichtigsten CLI-Abläufe ohne rohe Prozessausgabe.", "home.what": "Möglichkeiten", "home.extract": "Lokale Dokumente, Git-Repositories, Webseiten, Confluence oder Jira in portable Pakete extrahieren.", "home.import": "Pakete nach Confluence oder Jira veröffentlichen und Bilder, Anhänge und Veröffentlichungsstatus erhalten.", "home.rag": "Portable RAG-Korpora aus demselben CanonicalDocument erzeugen.", "home.security": "Die Konsole lauscht nur auf 127.0.0.1. Tokens bleiben in Umgebungsvariablen.",
        "settings.title": "Einstellungen", "settings.quick": "Häufige Einstellungen", "settings.advanced": "Erweitertes YAML", "settings.web_language": "Sprache der Web-Konsole", "settings.browser": "Automatisch (Browsersprache)", "settings.source": "Quellverzeichnis", "settings.destination": "Output-Verzeichnis", "settings.recursive": "Lokale rekursive Suche", "settings.git_recursive": "Rekursive Markdown-Suche in Git", "settings.depth": "Zu entdeckende Confluence-Baumtiefe", "settings.rag_destination": "RAG-Verzeichnis", "settings.save": "Speichern", "settings.reload": "Neu laden", "settings.yaml_hint": "Vollständiger YAML-Editor. Geheimnisse sollten in Umgebungsvariablen bleiben.",
        "extract.title": "Extrahieren — Quellen zu Paketen", "extract.local": "Lokale Dokumente", "extract.confluence": "Confluence", "extract.jira": "Jira", "extract.web": "Web", "extract.git": "Git-Repository", "extract.force": "Force Extract", "import.title": "Import — Pakete zu Zielen", "import.confluence": "Nach Confluence", "import.jira": "Nach Jira", "rag.title": "RAG", "rag.export": "Vorhandene Pakete exportieren", "rag.doc2rag": "Extrahieren und RAG exportieren",
        "common.run": "Starten", "common.source": "Quelle", "common.destination": "Ziel", "common.url": "URL", "common.instance": "Instanz", "common.space": "Bereich", "common.page": "Seite", "common.project": "Projekt", "common.issue_type": "Vorgangstyp", "common.issue": "Vorgang", "common.mode": "Modus", "common.title": "Titel", "common.parent": "Parent", "common.summary": "Summary", "common.optional": "optional", "common.refresh": "Aktualisieren", "common.loading": "Laden…", "common.none": "Keine", "common.error": "Fehler", "common.done": "Fertig", "common.running": "Verarbeitung", "common.queued": "Wartend", "common.warning": "Warnung", "common.result": "Ergebnis", "common.next": "Weiter", "common.stop": "Konsole stoppen", "common.config": "Konfiguration", "jira.manual": "Direkter Jira-Key", "jira.browse": "Projekt / Typ / Vorgang durchsuchen", "jira.search": "Textfilter", "confluence.attachments": "Anhänge", "confluence.zip": "Auch ZIP erstellen", "confluence.keep_hierarchy": "Hierarchie behalten", "git.ref": "Branch / Tag / Commit", "git.recursive": "Rekursiv", "help.title": "Hilfe", "help.text": "Die Web-Konsole ruft die Python-Dienste von DocSpecBridge direkt auf und startet keinen CLI-Unterprozess. Ergebnisse erscheinen als Karten und Tabellen statt Terminaltext.", "doctor.title": "Diagnose", "doctor.refresh": "Diagnose aktualisieren", "status.config_saved": "Einstellungen gespeichert.", "status.server_stopped": "Der DocSpecBridge-Server wurde beendet.",
    },
    "es": {
        "tab.home": "Inicio", "tab.settings": "Configuración", "tab.extract": "Extraer", "tab.import": "Importar", "tab.rag": "RAG", "tab.help": "Ayuda",
        "home.title": "DocSpecBridge — consola Web", "home.intro": "Interfaz local para los mismos flujos principales del menú CLI, sin salida bruta de procesos.", "home.what": "Qué puede hacer", "home.extract": "Extraer documentos locales, repositorios Git, páginas Web, Confluence o Jira a paquetes portátiles.", "home.import": "Publicar paquetes en Confluence o Jira conservando imágenes, adjuntos y estado de publicación.", "home.rag": "Crear un corpus RAG portátil desde el mismo CanonicalDocument.", "home.security": "La consola escucha solo en 127.0.0.1. Los tokens permanecen en variables de entorno.",
        "settings.title": "Configuración", "settings.quick": "Parámetros comunes", "settings.advanced": "YAML avanzado", "settings.web_language": "Idioma de la consola Web", "settings.browser": "Automático (idioma del navegador)", "settings.source": "Directorio de origen", "settings.destination": "Directorio output", "settings.recursive": "Búsqueda local recursiva", "settings.git_recursive": "Búsqueda Markdown recursiva en Git", "settings.depth": "Profundidad del árbol Confluence", "settings.rag_destination": "Directorio RAG", "settings.save": "Guardar", "settings.reload": "Recargar", "settings.yaml_hint": "Editor completo del YAML. Los secretos deben permanecer en variables de entorno.",
        "extract.title": "Extraer — fuentes a paquetes", "extract.local": "Documentos locales", "extract.confluence": "Confluence", "extract.jira": "Jira", "extract.web": "Web", "extract.git": "Repositorio Git", "extract.force": "Force Extract", "import.title": "Importar — paquetes a destinos", "import.confluence": "A Confluence", "import.jira": "A Jira", "rag.title": "RAG", "rag.export": "Exportar paquetes existentes", "rag.doc2rag": "Extraer y exportar RAG",
        "common.run": "Ejecutar", "common.source": "Origen", "common.destination": "Destino", "common.url": "URL", "common.instance": "Instancia", "common.space": "Espacio", "common.page": "Página", "common.project": "Proyecto", "common.issue_type": "Tipo de incidencia", "common.issue": "Incidencia", "common.mode": "Modo", "common.title": "Título", "common.parent": "Padre", "common.summary": "Summary", "common.optional": "opcional", "common.refresh": "Actualizar", "common.loading": "Cargando…", "common.none": "Ninguno", "common.error": "Error", "common.done": "Terminado", "common.running": "Procesando", "common.queued": "En espera", "common.warning": "Aviso", "common.result": "Resultado", "common.next": "Siguiente", "common.stop": "Detener consola", "common.config": "Configuración", "jira.manual": "Clave Jira directa", "jira.browse": "Explorar proyecto / tipo / incidencia", "jira.search": "Filtro de texto", "confluence.attachments": "Adjuntos", "confluence.zip": "Crear también ZIP", "confluence.keep_hierarchy": "Conservar jerarquía", "git.ref": "Rama / tag / commit", "git.recursive": "Recursivo", "help.title": "Ayuda", "help.text": "La consola Web llama directamente a los servicios Python de DocSpecBridge. No ejecuta el CLI como subproceso; los resultados se presentan como tarjetas y tablas.", "doctor.title": "Diagnóstico", "doctor.refresh": "Actualizar diagnóstico", "status.config_saved": "Configuración guardada.", "status.server_stopped": "El servidor DocSpecBridge se ha detenido.",
    },
    "zh": {
        "tab.home": "首页", "tab.settings": "设置", "tab.extract": "提取", "tab.import": "导入", "tab.rag": "RAG", "tab.help": "帮助",
        "home.title": "DocSpecBridge — Web 控制台", "home.intro": "本地 Web 界面，提供 CLI 主菜单的主要工作流，不显示子进程的原始终端输出。", "home.what": "可以完成", "home.extract": "将本地文档、Git 仓库、网页、Confluence 或 Jira 提取为可移植包。", "home.import": "将包发布到 Confluence 或 Jira，并保留图片、附件和发布状态。", "home.rag": "从同一个 CanonicalDocument 构建可移植 RAG 语料。", "home.security": "控制台只监听 127.0.0.1。Token 保留在环境变量中，不显示在浏览器里。",
        "settings.title": "设置", "settings.quick": "常用设置", "settings.advanced": "高级 YAML", "settings.web_language": "Web 控制台语言", "settings.browser": "自动（浏览器语言）", "settings.source": "源目录", "settings.destination": "输出目录", "settings.recursive": "本地递归搜索", "settings.git_recursive": "Git 中递归搜索 Markdown", "settings.depth": "Confluence 发现树深度", "settings.rag_destination": "RAG 目录", "settings.save": "保存", "settings.reload": "重新加载", "settings.yaml_hint": "完整 YAML 编辑器。敏感信息应保存在环境变量中。",
        "extract.title": "提取 — 源到包", "extract.local": "本地文档", "extract.confluence": "Confluence", "extract.jira": "Jira", "extract.web": "Web", "extract.git": "Git 仓库", "extract.force": "Force Extract", "import.title": "导入 — 包到目标", "import.confluence": "到 Confluence", "import.jira": "到 Jira", "rag.title": "RAG", "rag.export": "导出现有包", "rag.doc2rag": "提取并导出 RAG",
        "common.run": "运行", "common.source": "源", "common.destination": "目标", "common.url": "URL", "common.instance": "实例", "common.space": "空间", "common.page": "页面", "common.project": "项目", "common.issue_type": "事项类型", "common.issue": "事项", "common.mode": "模式", "common.title": "标题", "common.parent": "父级", "common.summary": "Summary", "common.optional": "可选", "common.refresh": "刷新", "common.loading": "加载中…", "common.none": "无", "common.error": "错误", "common.done": "完成", "common.running": "处理中", "common.queued": "等待中", "common.warning": "警告", "common.result": "结果", "common.next": "下一页", "common.stop": "停止控制台", "common.config": "配置", "jira.manual": "直接输入 Jira Key", "jira.browse": "浏览项目 / 类型 / 事项", "jira.search": "文本过滤", "confluence.attachments": "附件", "confluence.zip": "同时创建 ZIP", "confluence.keep_hierarchy": "保留层级", "git.ref": "分支 / tag / commit", "git.recursive": "递归", "help.title": "帮助", "help.text": "Web 控制台直接调用 DocSpecBridge Python 服务，不启动 CLI 子进程，因此结果以卡片、表格和消息显示，而不是终端文本。", "doctor.title": "诊断", "doctor.refresh": "刷新诊断", "status.config_saved": "设置已保存。", "status.server_stopped": "DocSpecBridge 服务器已停止。",
    },
}


def web_language(config: dict[str, Any], browser_language: str | None = None) -> str:
    configured = str((config.get("web") or {}).get("language") or "auto").strip().lower()
    if configured in {"", "auto", "browser"}:
        return normalize_language(browser_language or "en")
    return normalize_language(configured)


def _serialize(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {key: _serialize(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    return value


def _outcome_rows(outcomes: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in outcomes:
        rows.append({
            "source": str(item.source),
            "package": str(item.package_dir),
            "status": "error" if item.error else ("warning" if item.warnings else "ok"),
            "images": len(item.images),
            "warnings": list(item.warnings),
            "error": item.error,
        })
    return rows


class JobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def create(self, action: str, params: dict[str, Any], runner: Callable[[str, dict[str, Any], Callable[..., None]], Any]) -> str:
        job_id = secrets.token_urlsafe(9)
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._jobs[job_id] = {
                "id": job_id, "action": action, "status": "queued", "progress": 0,
                "stage": "queued", "created_at": now, "updated_at": now, "result": None, "error": None,
            }

        def update(*, stage: str | None = None, progress: int | None = None) -> None:
            with self._lock:
                job = self._jobs[job_id]
                if stage is not None:
                    job["stage"] = stage
                if progress is not None:
                    job["progress"] = max(0, min(100, int(progress)))
                job["updated_at"] = datetime.now(timezone.utc).isoformat()

        def target() -> None:
            with self._lock:
                self._jobs[job_id]["status"] = "running"
                self._jobs[job_id]["progress"] = 5
                self._jobs[job_id]["stage"] = "starting"
                self._jobs[job_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
            try:
                result = runner(action, params, update)
                with self._lock:
                    self._jobs[job_id]["result"] = _serialize(result)
                    self._jobs[job_id]["status"] = "done"
                    self._jobs[job_id]["progress"] = 100
                    self._jobs[job_id]["stage"] = "done"
                    self._jobs[job_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
            except Exception as exc:  # keep API response structured instead of printing a traceback
                with self._lock:
                    self._jobs[job_id]["status"] = "error"
                    self._jobs[job_id]["error"] = str(exc)
                    self._jobs[job_id]["details"] = traceback.format_exc(limit=12)
                    self._jobs[job_id]["updated_at"] = datetime.now(timezone.utc).isoformat()

        threading.Thread(target=target, name=f"docspecbridge-web-{job_id}", daemon=True).start()
        return job_id

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return deepcopy(job) if job else None


class WebApplication:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.jobs = JobStore()

    def config(self) -> dict[str, Any]:
        cfg = load_config(self.config_path)
        ensure_workdirs(cfg)
        return cfg

    def bootstrap(self, browser_language: str | None) -> dict[str, Any]:
        cfg = self.config()
        lang = web_language(cfg, browser_language)
        app = cfg.get("app") or {}
        web_cfg = cfg.get("web") or {}
        cf = cfg.get("confluence") or {}
        git_cfg = cfg.get("git") or {}
        rag = cfg.get("rag_export") or {}
        return {
            "version": __version__, "language": lang, "strings": WEB_TEXT.get(lang, WEB_TEXT["en"]),
            "languages": {"auto": WEB_TEXT.get(lang, WEB_TEXT["en"])["settings.browser"], **SUPPORTED_LANGUAGES},
            "config_path": str(self.config_path), "supported_extensions": list(SUPPORTED_SOURCE_EXTENSIONS),
            "settings": {
                "web_language": str(web_cfg.get("language") or "auto"),
                "source": str(app.get("source") or "./input"), "destination": str(app.get("destination") or "./output"),
                "recursive": bool(app.get("recursive", True)), "git_recursive": bool(git_cfg.get("recursive", True)),
                "confluence_depth": int((cf.get("page_selector") or {}).get("max_depth", 0)),
                "rag_destination": str(rag.get("destination") or "./rag"),
            },
            "confluence_instances": list(confluence_instances(cfg).keys()),
            "jira_instances": list(jira_instances(cfg).keys()),
        }

    def raw_yaml(self) -> str:
        if not self.config_path.exists():
            init_config(self.config_path)
        return self.config_path.read_text(encoding="utf-8")

    def save_quick(self, data: dict[str, Any]) -> Path:
        cfg = self.config()
        cfg.setdefault("web", {})["language"] = str(data.get("web_language") or "auto")
        cfg.setdefault("app", {})["source"] = str(data.get("source") or "./input")
        cfg["app"]["destination"] = str(data.get("destination") or "./output")
        cfg["app"]["recursive"] = bool(data.get("recursive", True))
        cfg.setdefault("git", {})["recursive"] = bool(data.get("git_recursive", True))
        depth = int(data.get("confluence_depth", 0))
        if depth < 0 or depth > 10:
            raise ValueError("Confluence discovery depth must be between 0 and 10")
        cfg.setdefault("confluence", {}).setdefault("page_selector", {})["max_depth"] = depth
        cfg.setdefault("rag_export", {})["destination"] = str(data.get("rag_destination") or "./rag")
        return save_config(cfg, self.config_path)

    def save_yaml(self, text: str) -> Path:
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError("Configuration YAML must contain a mapping/object at the root")
        return save_config(data, self.config_path)

    def run_job(self, action: str, params: dict[str, Any], update: Callable[..., None]) -> Any:
        cfg = self.config()
        destination = Path(str(params.get("destination") or (cfg.get("app") or {}).get("destination") or "./output")).expanduser()
        if action == "extract.local":
            runtime = deepcopy(cfg)
            if bool(params.get("force_extract")):
                runtime.setdefault("_runtime", {})["force_extract"] = True
                runtime.setdefault("app", {})["overwrite"] = False
            source = Path(str(params.get("source") or (cfg.get("app") or {}).get("source") or "./input")).expanduser()
            update(stage="discovering", progress=15)
            outcomes = run_extract(runtime, source, destination)
            update(stage="packaging", progress=90)
            return {"kind": "extraction", "items": _outcome_rows(outcomes), "destination": str(destination)}

        if action == "extract.git":
            url = str(params.get("url") or "").strip()
            if not url:
                raise ValueError("Repository URL is required")
            update(stage="downloading", progress=15)
            outcomes, context = run_extract_git_repository(
                cfg, url, destination,
                recursive=bool(params.get("recursive")) if "recursive" in params else None,
                ref=str(params.get("ref") or "").strip() or None,
            )
            update(stage="packaging", progress=90)
            return {"kind": "git-extraction", "repository": context, "items": _outcome_rows(outcomes), "destination": str(destination)}

        if action == "extract.web":
            url = str(params.get("url") or "").strip()
            if not url:
                raise ValueError("URL is required")
            parsed = urlparse(url)
            hint = safe_stem(Path(parsed.path).stem or parsed.hostname or "web-page")
            package = destination / f"{hint}__html"
            if package.exists() and any(package.iterdir()):
                raise FileExistsError(f"Package already exists: {package}")
            package.mkdir(parents=True, exist_ok=True)
            update(stage="fetching", progress=20)
            doc, warnings, _ = canonical_from_html_source(url, package_dir=package, fetch_config=(cfg.get("html") or {}).get("fetch") or {})
            update(stage="packaging", progress=75)
            write_canonical_package(
                doc, package, stem=hint,
                rag_profile=((cfg.get("profiles") or {}).get("rag") or {}),
                publication_profile=((cfg.get("profiles") or {}).get("publication") or {}), warnings=warnings,
            )
            return {"kind": "web-extraction", "package": str(package), "warnings": warnings}

        if action == "extract.confluence":
            update(stage="fetching", progress=20)
            package = export_page_to_package(
                cfg, str(params.get("page_id") or ""), destination,
                instance_name=str(params.get("instance") or "") or None,
                attachment_mode=str(params.get("attachment_mode") or "all"),
                zip_package=bool(params.get("zip_package", False)),
            )
            return {"kind": "confluence-extraction", "package": str(package)}

        if action == "extract.jira":
            issue = str(params.get("issue") or "").strip()
            if not issue:
                raise ValueError("Jira issue key is required")
            update(stage="fetching", progress=20)
            package = export_issue(cfg, issue, destination, instance_name=str(params.get("instance") or "") or None)
            return {"kind": "jira-extraction", "package": str(package), "issue": issue}

        if action == "import.confluence":
            source = Path(str(params.get("source") or "")).expanduser()
            update(stage="publishing", progress=20)
            results = publish(
                cfg, source,
                space_key=str(params.get("space_key") or "") or None,
                root_page=str(params.get("parent_id") or "") or None,
                instance_name=str(params.get("instance") or "") or None,
                keep_hierarchy=bool(params.get("keep_hierarchy", False)),
                mode=str(params.get("mode") or "replace"),
                title=str(params.get("title") or "").strip() or None,
            )
            return {"kind": "confluence-publication", "items": [_serialize(item) for item in results]}

        if action == "import.jira":
            source = Path(str(params.get("source") or "")).expanduser()
            update(stage="creating-issue", progress=20)
            result = create_issue_from_markdown(
                cfg, source,
                project=str(params.get("project") or ""),
                issue_type=str(params.get("issue_type_name") or params.get("issue_type") or "Story"),
                summary=str(params.get("summary") or "").strip() or None,
                parent=str(params.get("parent") or "").strip() or None,
                instance_name=str(params.get("instance") or "") or None,
            )
            return {"kind": "jira-publication", **_serialize(result)}

        if action == "rag.export":
            source = Path(str(params.get("source") or (cfg.get("app") or {}).get("destination") or "./output")).expanduser()
            rag_destination = Path(str(params.get("rag_destination") or (cfg.get("rag_export") or {}).get("destination") or "./rag")).expanduser()
            update(stage="aggregating", progress=20)
            result = export_rag_corpus(
                source, rag_destination,
                copy_assets=bool((cfg.get("rag_export") or {}).get("copy_assets", True)),
                copy_document_json=bool((cfg.get("rag_export") or {}).get("copy_document_json", True)),
                overwrite=bool((cfg.get("rag_export") or {}).get("overwrite", True)),
            )
            return {"kind": "rag-export", **_serialize(result)}

        if action == "rag.doc2rag":
            source = Path(str(params.get("source") or (cfg.get("app") or {}).get("source") or "./input")).expanduser()
            out = Path(str(params.get("destination") or (cfg.get("app") or {}).get("destination") or "./output")).expanduser()
            rag_destination = Path(str(params.get("rag_destination") or (cfg.get("rag_export") or {}).get("destination") or "./rag")).expanduser()
            update(stage="extracting", progress=15)
            outcomes = run_extract(cfg, source, out)
            good = [item.package_dir for item in outcomes if not item.error]
            update(stage="aggregating", progress=75)
            result = export_rag_corpus(
                good, rag_destination,
                copy_assets=bool((cfg.get("rag_export") or {}).get("copy_assets", True)),
                copy_document_json=bool((cfg.get("rag_export") or {}).get("copy_document_json", True)),
                overwrite=bool((cfg.get("rag_export") or {}).get("overwrite", True)),
            )
            return {"kind": "doc2rag", "items": _outcome_rows(outcomes), "rag": _serialize(result)}

        raise ValueError(f"Unknown web action: {action}")


HTML_TEMPLATE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DocSpecBridge</title>
<style>
:root{font-family:Inter,Segoe UI,Arial,sans-serif;color:#172033;background:#f5f7fb;line-height:1.45}*{box-sizing:border-box}body{margin:0}button,input,select,textarea{font:inherit}.top{position:sticky;top:0;z-index:20;background:#172033;color:#fff;padding:12px 22px;display:flex;align-items:center;gap:16px;box-shadow:0 2px 10px #0002}.brand{font-weight:750;font-size:18px}.version{font-size:12px;opacity:.72}.spacer{flex:1}.stop{background:#fff1f1;color:#9d1d1d;border:1px solid #e8b7b7;border-radius:8px;padding:7px 11px;cursor:pointer}.tabs{background:#fff;border-bottom:1px solid #dfe4ee;display:flex;gap:4px;padding:0 18px;overflow:auto}.tab{border:0;background:transparent;padding:13px 14px;cursor:pointer;border-bottom:3px solid transparent;white-space:nowrap}.tab.active{font-weight:700;border-bottom-color:#2b5bd7;color:#2049ad}.wrap{max-width:1220px;margin:0 auto;padding:22px}.page{display:none}.page.active{display:block}.hero{background:linear-gradient(135deg,#fff,#edf3ff);border:1px solid #dce5f6;border-radius:16px;padding:28px;margin-bottom:18px}.hero h1{margin:0 0 10px;font-size:30px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:16px}.card{background:#fff;border:1px solid #dfe4ee;border-radius:13px;padding:18px;box-shadow:0 2px 8px #1c2a4b0a}.card h2,.card h3{margin-top:0}.card.soft{background:#fafcff}.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px}.field{display:flex;flex-direction:column;gap:5px}.field.wide{grid-column:1/-1}label{font-weight:650;font-size:13px;color:#3c465c}input,select,textarea{width:100%;border:1px solid #cdd5e2;border-radius:8px;padding:9px;background:white;color:#172033}textarea{min-height:340px;font-family:Consolas,monospace;font-size:13px}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:13px}.primary,.secondary{border-radius:8px;padding:9px 14px;cursor:pointer}.primary{border:1px solid #2454c6;background:#2b5bd7;color:#fff}.secondary{border:1px solid #cbd4e3;background:#fff;color:#25324a}.subtabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}.subtab{padding:7px 10px;border:1px solid #d3dae7;border-radius:999px;background:white;cursor:pointer}.subtab.active{background:#eaf0ff;border-color:#89a5eb;color:#1743a8;font-weight:650}.subpanel{display:none}.subpanel.active{display:block}.result{margin-top:16px}.job{border:1px solid #dbe2ee;border-radius:12px;padding:14px;background:#fff}.job-head{display:flex;justify-content:space-between;gap:12px}.badge{font-size:12px;border-radius:999px;padding:3px 8px;font-weight:700}.ok{background:#e8f7ef;color:#17643b}.warn{background:#fff6dc;color:#755200}.err{background:#fdeaea;color:#9b2323}.run{background:#eaf0ff;color:#264aa0}.progress{height:7px;background:#e8edf5;border-radius:999px;overflow:hidden;margin:10px 0}.progress>div{height:100%;background:#2b5bd7;width:0;transition:width .3s}.muted{color:#68758a;font-size:13px}.message{padding:10px 12px;border-radius:8px;background:#eef4ff;margin:10px 0}.message.error{background:#fdecec;color:#8f2020}.message.warning{background:#fff7df;color:#735617}table{width:100%;border-collapse:collapse;margin-top:10px;font-size:13px}th,td{text-align:left;border-bottom:1px solid #e4e8f0;padding:8px;vertical-align:top}th{background:#f8faff}code{background:#eef1f6;padding:2px 5px;border-radius:5px}.row{display:flex;gap:10px;align-items:center}.row input[type=checkbox]{width:auto}.hidden{display:none!important}.statusline{font-size:12px;color:#69768b}.doctor-table td:first-child{font-weight:650;width:34%}@media(max-width:700px){.wrap{padding:12px}.hero{padding:18px}.top{padding:10px 12px}}
</style></head><body>
<div class="top"><div class="brand">DocSpecBridge</div><div class="version">v__VERSION__</div><div class="spacer"></div><div id="configPath" class="version"></div><button id="stopBtn" class="stop" data-i18n="common.stop">Stop</button></div>
<nav class="tabs">
<button class="tab active" data-tab="home" data-i18n="tab.home">Home</button><button class="tab" data-tab="settings" data-i18n="tab.settings">Settings</button><button class="tab" data-tab="extract" data-i18n="tab.extract">Extract</button><button class="tab" data-tab="import" data-i18n="tab.import">Import</button><button class="tab" data-tab="rag" data-i18n="tab.rag">RAG</button><button class="tab" data-tab="help" data-i18n="tab.help">Help</button>
</nav>
<main class="wrap">
<section id="page-home" class="page active"><div class="hero"><h1 data-i18n="home.title">DocSpecBridge — Web console</h1><p data-i18n="home.intro"></p><div class="statusline" id="supportedFormats"></div></div><h2 data-i18n="home.what"></h2><div class="grid"><div class="card"><h3 data-i18n="tab.extract"></h3><p data-i18n="home.extract"></p></div><div class="card"><h3 data-i18n="tab.import"></h3><p data-i18n="home.import"></p></div><div class="card"><h3>RAG</h3><p data-i18n="home.rag"></p></div><div class="card soft"><h3>Local</h3><p data-i18n="home.security"></p></div></div></section>
<section id="page-settings" class="page"><h1 data-i18n="settings.title"></h1><div class="card"><h2 data-i18n="settings.quick"></h2><div class="form-grid"><div class="field"><label data-i18n="settings.web_language"></label><select id="webLanguage"></select></div><div class="field"><label data-i18n="settings.source"></label><input id="setSource"></div><div class="field"><label data-i18n="settings.destination"></label><input id="setDestination"></div><div class="field"><label data-i18n="settings.rag_destination"></label><input id="setRagDestination"></div><div class="field"><label data-i18n="settings.depth"></label><input id="setDepth" type="number" min="0" max="10"></div><div class="field row"><input id="setRecursive" type="checkbox"><label for="setRecursive" data-i18n="settings.recursive"></label></div><div class="field row"><input id="setGitRecursive" type="checkbox"><label for="setGitRecursive" data-i18n="settings.git_recursive"></label></div></div><div class="actions"><button id="saveQuick" class="primary" data-i18n="settings.save"></button></div><div id="settingsMessage"></div></div><div class="card" style="margin-top:16px"><h2 data-i18n="settings.advanced"></h2><p class="muted" data-i18n="settings.yaml_hint"></p><textarea id="yamlEditor" spellcheck="false"></textarea><div class="actions"><button id="saveYaml" class="primary" data-i18n="settings.save"></button><button id="reloadYaml" class="secondary" data-i18n="settings.reload"></button></div></div></section>
<section id="page-extract" class="page"><h1 data-i18n="extract.title"></h1><div class="subtabs" data-group="extract"><button class="subtab active" data-panel="local" data-i18n="extract.local"></button><button class="subtab" data-panel="confluence" data-i18n="extract.confluence"></button><button class="subtab" data-panel="jira" data-i18n="extract.jira"></button><button class="subtab" data-panel="web" data-i18n="extract.web"></button><button class="subtab" data-panel="git" data-i18n="extract.git"></button></div>
<div id="extract-local" class="subpanel active card"><div class="form-grid"><div class="field"><label data-i18n="common.source"></label><input id="localSource"></div><div class="field"><label data-i18n="common.destination"></label><input id="localDest"></div><div class="field row"><input id="localForce" type="checkbox"><label for="localForce" data-i18n="extract.force"></label></div></div><div class="actions"><button class="primary" id="runLocal" data-i18n="common.run"></button></div></div>
<div id="extract-confluence" class="subpanel card"><div class="form-grid"><div class="field"><label data-i18n="common.instance"></label><select id="ceInstance"></select></div><div class="field"><label data-i18n="common.space"></label><select id="ceSpace"></select></div><div class="field wide"><label data-i18n="common.page"></label><select id="cePage"></select></div><div class="field"><label data-i18n="confluence.attachments"></label><select id="ceAttach"><option value="all">all</option><option value="images">images</option><option value="none">none</option></select></div><div class="field row"><input id="ceZip" type="checkbox"><label for="ceZip" data-i18n="confluence.zip"></label></div></div><div class="actions"><button class="primary" id="runCe" data-i18n="common.run"></button></div></div>
<div id="extract-jira" class="subpanel card"><div class="subtabs"><button class="subtab active" data-jira-mode="manual" data-i18n="jira.manual"></button><button class="subtab" data-jira-mode="browse" data-i18n="jira.browse"></button></div><div class="form-grid"><div class="field"><label data-i18n="common.instance"></label><select id="jeInstance"></select></div><div id="jiraManual" class="field"><label data-i18n="common.issue"></label><input id="jeIssue" placeholder="ABC-123"></div><div id="jiraBrowse" class="field wide hidden"><div class="form-grid"><div class="field"><label data-i18n="common.project"></label><select id="jeProject"></select></div><div class="field"><label data-i18n="common.issue_type"></label><select id="jeType"></select></div><div class="field"><label data-i18n="jira.search"></label><input id="jeQuery"></div><div class="field wide"><label data-i18n="common.issue"></label><select id="jeIssueBrowse"></select></div></div><div class="actions"><button class="secondary hidden" id="jeNext" data-i18n="common.next">Next</button></div></div></div><div class="actions"><button class="primary" id="runJe" data-i18n="common.run"></button></div></div>
<div id="extract-web" class="subpanel card"><div class="form-grid"><div class="field wide"><label data-i18n="common.url"></label><input id="webUrl" placeholder="https://..."></div><div class="field"><label data-i18n="common.destination"></label><input id="webDest"></div></div><div class="actions"><button class="primary" id="runWeb" data-i18n="common.run"></button></div></div>
<div id="extract-git" class="subpanel card"><div class="form-grid"><div class="field wide"><label data-i18n="common.url"></label><input id="gitUrl" placeholder="https://github.com/org/repo"></div><div class="field"><label data-i18n="common.destination"></label><input id="gitDest"></div><div class="field"><label data-i18n="git.ref"></label><input id="gitRef"></div><div class="field row"><input id="gitRecursive" type="checkbox"><label for="gitRecursive" data-i18n="git.recursive"></label></div></div><div class="actions"><button class="primary" id="runGit" data-i18n="common.run"></button></div></div><div id="extractResult" class="result"></div></section>
<section id="page-import" class="page"><h1 data-i18n="import.title"></h1><div class="subtabs" data-group="import"><button class="subtab active" data-panel="confluence" data-i18n="import.confluence"></button><button class="subtab" data-panel="jira" data-i18n="import.jira"></button></div>
<div id="import-confluence" class="subpanel active card"><div class="form-grid"><div class="field wide"><label data-i18n="common.source"></label><input id="ciSource"></div><div class="field"><label data-i18n="common.instance"></label><select id="ciInstance"></select></div><div class="field"><label data-i18n="common.space"></label><select id="ciSpace"></select></div><div class="field wide"><label data-i18n="common.page"></label><select id="ciPage"></select></div><div class="field"><label data-i18n="common.mode"></label><select id="ciMode"><option value="replace">replace</option><option value="add">add</option></select></div><div class="field"><label><span data-i18n="common.title"></span> (<span data-i18n="common.optional"></span>)</label><input id="ciTitle"></div><div class="field row"><input id="ciHierarchy" type="checkbox"><label for="ciHierarchy" data-i18n="confluence.keep_hierarchy"></label></div></div><div class="actions"><button class="primary" id="runCi" data-i18n="common.run"></button></div></div>
<div id="import-jira" class="subpanel card"><div class="form-grid"><div class="field wide"><label data-i18n="common.source"></label><input id="jiSource"></div><div class="field"><label data-i18n="common.instance"></label><select id="jiInstance"></select></div><div class="field"><label data-i18n="common.project"></label><select id="jiProject"></select></div><div class="field"><label data-i18n="common.issue_type"></label><select id="jiType"></select></div><div class="field"><label><span data-i18n="common.summary"></span> (<span data-i18n="common.optional"></span>)</label><input id="jiSummary"></div><div class="field"><label><span data-i18n="common.parent"></span> (<span data-i18n="common.optional"></span>)</label><input id="jiParent"></div></div><div class="actions"><button class="primary" id="runJi" data-i18n="common.run"></button></div></div><div id="importResult" class="result"></div></section>
<section id="page-rag" class="page"><h1 data-i18n="rag.title"></h1><div class="grid"><div class="card"><h2 data-i18n="rag.export"></h2><div class="field"><label data-i18n="common.source"></label><input id="ragSource"></div><div class="field"><label data-i18n="common.destination"></label><input id="ragDest"></div><div class="actions"><button id="runRag" class="primary" data-i18n="common.run"></button></div></div><div class="card"><h2 data-i18n="rag.doc2rag"></h2><div class="field"><label data-i18n="common.source"></label><input id="d2rSource"></div><div class="field"><label>Output</label><input id="d2rOut"></div><div class="field"><label>RAG</label><input id="d2rDest"></div><div class="actions"><button id="runD2r" class="primary" data-i18n="common.run"></button></div></div></div><div id="ragResult" class="result"></div></section>
<section id="page-help" class="page"><h1 data-i18n="help.title"></h1><div class="card"><p data-i18n="help.text"></p><p><code>docspecbridge web</code></p><p class="muted">127.0.0.1 — Python ThreadingHTTPServer</p></div><div class="card" style="margin-top:16px"><h2 data-i18n="doctor.title"></h2><div class="actions"><button id="refreshDoctor" class="secondary" data-i18n="doctor.refresh"></button></div><div id="doctorResult"></div></div></section>
</main>
<script>
const TOKEN='__TOKEN__'; let B=null, T={}; let jiraMode='manual'; let jiraNextToken=null;
const $=id=>document.getElementById(id); const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function tr(k){return T[k]||k} function apiHeaders(){return {'Content-Type':'application/json','X-DocSpecBridge-Token':TOKEN}}
async function api(url,opt={}){opt.headers={...(opt.headers||{}),'X-DocSpecBridge-Token':TOKEN}; const r=await fetch(url,opt); const data=await r.json().catch(()=>({error:r.statusText})); if(!r.ok) throw new Error(data.error||r.statusText); return data}
function applyI18n(){document.querySelectorAll('[data-i18n]').forEach(el=>{const k=el.dataset.i18n;if(T[k])el.textContent=T[k]});document.documentElement.lang=B.language}
function fillSelect(el,items,valueKey='value',labelKey='label'){el.innerHTML='';for(const it of items){const o=document.createElement('option');if(typeof it==='string'){o.value=it;o.textContent=it}else{o.value=it[valueKey]??'';o.textContent=it[labelKey]??o.value;if(it.name)o.dataset.name=it.name}el.appendChild(o)}}
function populateBootstrap(){const s=B.settings;$('configPath').textContent=B.config_path;$('supportedFormats').textContent=B.supported_extensions.join(' · ');fillSelect($('webLanguage'),Object.entries(B.languages).map(([value,label])=>({value,label})));$('webLanguage').value=s.web_language;$('setSource').value=s.source;$('setDestination').value=s.destination;$('setRecursive').checked=s.recursive;$('setGitRecursive').checked=s.git_recursive;$('setDepth').value=s.confluence_depth;$('setRagDestination').value=s.rag_destination;for(const id of ['localSource','d2rSource'])$(id).value=s.source;for(const id of ['localDest','webDest','gitDest','ciSource','jiSource','ragSource','d2rOut'])$(id).value=s.destination;for(const id of ['ragDest','d2rDest'])$(id).value=s.rag_destination;$('gitRecursive').checked=s.git_recursive;const cis=B.confluence_instances.map(x=>({value:x,label:x}));const jis=B.jira_instances.map(x=>({value:x,label:x}));for(const id of ['ceInstance','ciInstance'])fillSelect($(id),cis);for(const id of ['jeInstance','jiInstance'])fillSelect($(id),jis)}
async function bootstrap(){B=await api('/api/bootstrap?browser_lang='+encodeURIComponent(navigator.language||'en'));T=B.strings;applyI18n();populateBootstrap();await reloadYaml();if(B.confluence_instances.length){await loadSpaces('ce');await loadSpaces('ci')}if(B.jira_instances.length){await loadProjects('je');await loadProjects('ji')}}
function switchPage(name){document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x.dataset.tab===name));document.querySelectorAll('.page').forEach(x=>x.classList.toggle('active',x.id==='page-'+name))}
document.querySelectorAll('.tab').forEach(x=>x.addEventListener('click',()=>switchPage(x.dataset.tab)));
document.querySelectorAll('.subtabs[data-group]').forEach(group=>group.querySelectorAll('.subtab').forEach(btn=>btn.addEventListener('click',()=>{group.querySelectorAll('.subtab').forEach(b=>b.classList.toggle('active',b===btn));const scope=group.dataset.group;document.querySelectorAll('#page-'+scope+' .subpanel').forEach(p=>p.classList.toggle('active',p.id===scope+'-'+btn.dataset.panel))})));
document.querySelectorAll('[data-jira-mode]').forEach(btn=>btn.addEventListener('click',async()=>{document.querySelectorAll('[data-jira-mode]').forEach(b=>b.classList.toggle('active',b===btn));jiraMode=btn.dataset.jiraMode;$('jiraManual').classList.toggle('hidden',jiraMode!=='manual');$('jiraBrowse').classList.toggle('hidden',jiraMode!=='browse');if(jiraMode==='browse')await loadProjects('je')}));
async function reloadYaml(){$('yamlEditor').value=(await api('/api/config/yaml')).yaml}
function msg(target,text,error=false){target.innerHTML='<div class="message '+(error?'error':'')+'">'+esc(text)+'</div>'}
$('saveQuick').onclick=async()=>{try{await api('/api/config/quick',{method:'POST',headers:apiHeaders(),body:JSON.stringify({web_language:$('webLanguage').value,source:$('setSource').value,destination:$('setDestination').value,recursive:$('setRecursive').checked,git_recursive:$('setGitRecursive').checked,confluence_depth:Number($('setDepth').value),rag_destination:$('setRagDestination').value})});msg($('settingsMessage'),tr('status.config_saved'));setTimeout(()=>location.reload(),350)}catch(e){msg($('settingsMessage'),e.message,true)}};
$('saveYaml').onclick=async()=>{try{await api('/api/config/yaml',{method:'POST',headers:apiHeaders(),body:JSON.stringify({yaml:$('yamlEditor').value})});msg($('settingsMessage'),tr('status.config_saved'));setTimeout(()=>location.reload(),350)}catch(e){msg($('settingsMessage'),e.message,true)}};$('reloadYaml').onclick=reloadYaml;
async function loadSpaces(prefix){const inst=$(prefix+'Instance').value;if(!inst)return;const data=await api('/api/confluence/spaces?instance='+encodeURIComponent(inst));fillSelect($(prefix+'Space'),data.items.map(x=>({value:x.id,label:(x.key||'')+' — '+(x.name||''),name:x.key})));await loadPages(prefix)}
async function loadPages(prefix){const inst=$(prefix+'Instance').value,space=$(prefix+'Space').value;if(!inst||!space)return;const data=await api('/api/confluence/pages?instance='+encodeURIComponent(inst)+'&space_id='+encodeURIComponent(space));fillSelect($(prefix+'Page'),data.items.map(x=>({value:x.id,label:(x.tree_label||x.title||'')+' ('+x.id+')'})))}
for(const p of ['ce','ci']){$(p+'Instance').onchange=()=>loadSpaces(p);$(p+'Space').onchange=()=>loadPages(p)}
async function loadProjects(prefix){const inst=$(prefix+'Instance').value;if(!inst)return;const data=await api('/api/jira/projects?instance='+encodeURIComponent(inst));fillSelect($(prefix+'Project'),data.items.map(x=>({value:x.key||x.id,label:(x.key||'')+' — '+(x.name||'')})));await loadTypes(prefix)}
async function loadTypes(prefix){const inst=$(prefix+'Instance').value,project=$(prefix+'Project').value;if(!inst||!project)return;const data=await api('/api/jira/types?instance='+encodeURIComponent(inst)+'&project='+encodeURIComponent(project));fillSelect($(prefix+'Type'),data.items.map(x=>({value:x.id||x.name,label:x.name||x.id,name:x.name||x.id})));if(prefix==='je')await loadIssues()}
async function loadIssues(next=false){const inst=$('jeInstance').value,project=$('jeProject').value,type=$('jeType').value,query=$('jeQuery').value;if(!inst||!project||!type)return;if(!next)jiraNextToken=null;let u='/api/jira/issues?instance='+encodeURIComponent(inst)+'&project='+encodeURIComponent(project)+'&issue_type='+encodeURIComponent(type)+'&query='+encodeURIComponent(query);if(next&&jiraNextToken)u+='&next_page_token='+encodeURIComponent(jiraNextToken);const data=await api(u);fillSelect($('jeIssueBrowse'),data.items.map(x=>({value:x.key||x.id,label:(x.key||'')+' — '+((x.fields||{}).summary||'')})));jiraNextToken=data.next_page_token||null;$('jeNext').classList.toggle('hidden',!jiraNextToken)}
for(const p of ['je','ji']){$(p+'Instance').onchange=()=>loadProjects(p);$(p+'Project').onchange=()=>loadTypes(p);$(p+'Type').onchange=()=>p==='je'?loadIssues():null}$('jeQuery').onchange=()=>loadIssues(false);$('jeNext').onclick=()=>loadIssues(true);
function renderResult(target,job){let h='<div class="job"><div class="job-head"><strong>'+esc(tr('common.result'))+'</strong><span class="badge '+(job.status==='done'?'ok':job.status==='error'?'err':'run')+'">'+esc(job.status)+'</span></div><div class="progress"><div style="width:'+Number(job.progress||0)+'%"></div></div><div class="muted">'+esc(job.stage||'')+'</div>';if(job.error){h+='<div class="message error">'+esc(job.error)+'</div>';if(job.details)h+='<details><summary>details</summary><pre>'+esc(job.details)+'</pre></details>'}if(job.result){const r=job.result;if(Array.isArray(r.items)){h+='<table><thead><tr><th>Status</th><th>Source / Title</th><th>Package / ID</th><th>Warnings</th></tr></thead><tbody>';for(const x of r.items){h+='<tr><td>'+esc(x.status||x.action||'OK')+'</td><td>'+esc(x.source||x.title||'')+'</td><td>'+esc(x.package||x.page_id||x.key||'')+'</td><td>'+esc((x.warnings||[]).join(' · ')||x.error||'')+'</td></tr>'}h+='</tbody></table>'}else{h+='<table><tbody>';for(const [k,v] of Object.entries(r)){if(typeof v!=='object'||v===null)h+='<tr><th>'+esc(k)+'</th><td>'+esc(v)+'</td></tr>'}h+='</tbody></table>'}}h+='</div>';target.innerHTML=h}
async function startJob(action,params,target){target.innerHTML='<div class="job"><span class="badge run">'+esc(tr('common.queued'))+'</span><div class="progress"><div style="width:5%"></div></div></div>';try{const x=await api('/api/jobs',{method:'POST',headers:apiHeaders(),body:JSON.stringify({action,params})});while(true){await new Promise(r=>setTimeout(r,450));const j=await api('/api/jobs/'+encodeURIComponent(x.id));renderResult(target,j);if(j.status==='done'||j.status==='error')break}}catch(e){msg(target,e.message,true)}}
$('runLocal').onclick=()=>startJob('extract.local',{source:$('localSource').value,destination:$('localDest').value,force_extract:$('localForce').checked},$('extractResult'));
$('runGit').onclick=()=>startJob('extract.git',{url:$('gitUrl').value,destination:$('gitDest').value,ref:$('gitRef').value,recursive:$('gitRecursive').checked},$('extractResult'));
$('runWeb').onclick=()=>startJob('extract.web',{url:$('webUrl').value,destination:$('webDest').value},$('extractResult'));
$('runCe').onclick=()=>startJob('extract.confluence',{instance:$('ceInstance').value,page_id:$('cePage').value,attachment_mode:$('ceAttach').value,zip_package:$('ceZip').checked},$('extractResult'));
$('runJe').onclick=()=>startJob('extract.jira',{instance:$('jeInstance').value,issue:jiraMode==='manual'?$('jeIssue').value:$('jeIssueBrowse').value},$('extractResult'));
$('runCi').onclick=()=>{const so=$('ciSpace').selectedOptions[0];startJob('import.confluence',{source:$('ciSource').value,instance:$('ciInstance').value,space_key:so?so.dataset.name:'',parent_id:$('ciPage').value,mode:$('ciMode').value,title:$('ciTitle').value,keep_hierarchy:$('ciHierarchy').checked},$('importResult'))};
$('runJi').onclick=()=>{const to=$('jiType').selectedOptions[0];startJob('import.jira',{source:$('jiSource').value,instance:$('jiInstance').value,project:$('jiProject').value,issue_type:$('jiType').value,issue_type_name:to?to.dataset.name:$('jiType').value,summary:$('jiSummary').value,parent:$('jiParent').value},$('importResult'))};
$('runRag').onclick=()=>startJob('rag.export',{source:$('ragSource').value,rag_destination:$('ragDest').value},$('ragResult'));
$('runD2r').onclick=()=>startJob('rag.doc2rag',{source:$('d2rSource').value,destination:$('d2rOut').value,rag_destination:$('d2rDest').value},$('ragResult'));
async function doctor(){try{const d=await api('/api/doctor');let h='<table class="doctor-table"><tbody>';for(const [k,v] of Object.entries(d.items))h+='<tr><td>'+esc(k)+'</td><td>'+esc(v)+'</td></tr>';h+='</tbody></table>';$('doctorResult').innerHTML=h}catch(e){msg($('doctorResult'),e.message,true)}}$('refreshDoctor').onclick=doctor;
$('stopBtn').onclick=async()=>{try{await api('/api/shutdown',{method:'POST',headers:apiHeaders(),body:'{}'});document.body.innerHTML='<main class="wrap"><div class="hero"><h1>DocSpecBridge</h1><p>'+esc(tr('status.server_stopped'))+'</p></div></main>'}catch(e){alert(e.message)}};
bootstrap().catch(e=>{document.body.innerHTML='<main class="wrap"><div class="message error">'+esc(e.message)+'</div></main>'});
</script></body></html>'''


def _make_handler(app: WebApplication, token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"DocSpecBridge/{__version__}"

        def log_message(self, format: str, *args: Any) -> None:
            # The browser is the primary UI. Keep the console quiet except server lifecycle.
            return

        def _authorized(self) -> bool:
            if self.path == "/" or self.path.startswith("/?"):
                return True
            return self.headers.get("X-DocSpecBridge-Token", "") == token

        def _json(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(_serialize(payload), ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _error(self, exc: Exception, status: int = 400) -> None:
            self._json({"error": str(exc)}, status)

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 2_000_000:
                raise ValueError("Request body is too large")
            raw = self.rfile.read(length) if length else b"{}"
            value = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(value, dict):
                raise ValueError("JSON request must contain an object")
            return value

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                html = HTML_TEMPLATE.replace("__TOKEN__", token).replace("__VERSION__", __version__).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers(); self.wfile.write(html); return
            if not self._authorized():
                self._json({"error": "Unauthorized"}, 403); return
            query = parse_qs(parsed.query)
            try:
                if parsed.path == "/api/bootstrap":
                    self._json(app.bootstrap((query.get("browser_lang") or [None])[0])); return
                if parsed.path == "/api/config/yaml":
                    self._json({"yaml": app.raw_yaml()}); return
                if parsed.path == "/api/doctor":
                    self._json({"items": doctor_info(app.config(), config_path=app.config_path)}); return
                if parsed.path == "/api/confluence/spaces":
                    cfg = app.config(); instance = (query.get("instance") or [""])[0]
                    self._json({"items": list_spaces(cfg, instance or None)}); return
                if parsed.path == "/api/confluence/pages":
                    cfg = app.config(); instance = (query.get("instance") or [""])[0]; space_id = (query.get("space_id") or [""])[0]
                    depth = int(((cfg.get("confluence") or {}).get("page_selector") or {}).get("max_depth", 0))
                    self._json({"items": list_root_pages(cfg, instance or None, space_id, max_depth=depth)}); return
                if parsed.path == "/api/jira/projects":
                    cfg = app.config(); instance = (query.get("instance") or [""])[0]; q = (query.get("query") or [""])[0] or None
                    self._json({"items": list_projects(cfg, instance or None, query=q)}); return
                if parsed.path == "/api/jira/types":
                    cfg = app.config(); instance = (query.get("instance") or [""])[0]; project = (query.get("project") or [""])[0]
                    self._json({"items": list_issue_types(cfg, project, instance or None)}); return
                if parsed.path == "/api/jira/issues":
                    cfg = app.config(); instance = (query.get("instance") or [""])[0]; project = (query.get("project") or [""])[0]
                    issue_type = (query.get("issue_type") or [""])[0] or None; q = (query.get("query") or [""])[0] or None
                    next_page_token = (query.get("next_page_token") or [""])[0] or None
                    page = list_issues(cfg, project, instance or None, issue_type=issue_type, query=q, next_page_token=next_page_token, max_results=min(int(((cfg.get("jira") or {}).get("discovery") or {}).get("page_size", 50)), 100))
                    self._json({"items": page.get("issues") or [], "jql": page.get("jql"), "next_page_token": page.get("next_page_token")}); return
                if parsed.path.startswith("/api/jobs/"):
                    job = app.jobs.get(parsed.path.rsplit("/", 1)[-1])
                    if not job: self._json({"error": "Unknown job"}, 404)
                    else: self._json(job)
                    return
                self._json({"error": "Not found"}, 404)
            except Exception as exc:
                self._error(exc)

        def do_POST(self) -> None:
            if not self._authorized():
                self._json({"error": "Unauthorized"}, 403); return
            parsed = urlparse(self.path)
            try:
                body = self._body()
                if parsed.path == "/api/config/quick":
                    self._json({"path": str(app.save_quick(body))}); return
                if parsed.path == "/api/config/yaml":
                    self._json({"path": str(app.save_yaml(str(body.get("yaml") or "")))}); return
                if parsed.path == "/api/jobs":
                    action = str(body.get("action") or "")
                    params = body.get("params") or {}
                    if not isinstance(params, dict): raise ValueError("params must be an object")
                    job_id = app.jobs.create(action, params, app.run_job)
                    self._json({"id": job_id}, 202); return
                if parsed.path == "/api/shutdown":
                    self._json({"ok": True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start(); return
                self._json({"error": "Not found"}, 404)
            except Exception as exc:
                self._error(exc)

    return Handler


def run_web_console(
    config_path: Path | None = None,
    *,
    port: int | None = None,
    open_browser: bool | None = None,
) -> str:
    """Run the local DocSpecBridge Web console using Python's built-in HTTP server."""
    selected = selected_config_path(config_path)
    if not selected.exists():
        init_config(selected)
    cfg = load_config(selected)
    web_cfg = cfg.get("web") or {}
    requested_port = int(port if port is not None else web_cfg.get("port", 8765))
    should_open = bool(web_cfg.get("open_browser", True) if open_browser is None else open_browser)
    token = secrets.token_urlsafe(24)
    application = WebApplication(selected)
    handler = _make_handler(application, token)

    server: ThreadingHTTPServer | None = None
    last_error: OSError | None = None
    actual_port = requested_port
    for candidate in range(requested_port, requested_port + 10):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
            actual_port = candidate
            break
        except OSError as exc:
            last_error = exc
    if server is None:
        raise RuntimeError(f"Unable to start the Web console on ports {requested_port}-{requested_port + 9}: {last_error}")

    url = f"http://127.0.0.1:{actual_port}/"
    print(f"DocSpecBridge Web: {url}")
    if should_open:
        threading.Timer(0.25, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
    return url

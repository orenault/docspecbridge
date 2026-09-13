from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

from .adf import canonical_from_adf, canonical_to_adf
from .html_io import canonical_from_markdown
from .package_io import write_canonical_package
from .utils import safe_stem, write_json


def _instances(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    jira = config.get("jira") or {}
    explicit = jira.get("instances") or {}
    if explicit:
        return explicit
    # Convenience: Jira and Confluence often share the same Atlassian Cloud site/token.
    return ((config.get("confluence") or {}).get("instances") or {})


def get_jira_instance(config: dict[str, Any], name: str | None = None) -> tuple[str, dict[str, Any]]:
    instances = _instances(config)
    if not instances:
        raise RuntimeError("No Jira/Atlassian Cloud instance configured.")
    default = str((config.get("jira") or {}).get("default_instance") or (config.get("confluence") or {}).get("default_instance") or "")
    selected = name or default or next(iter(instances))
    if selected not in instances:
        raise KeyError(f"Unknown Jira instance: {selected}")
    return selected, dict(instances[selected] or {})


def _auth(instance: dict[str, Any]) -> httpx.BasicAuth:
    user = str(instance.get("user_name") or "").strip()
    env_name = str(instance.get("token_env") or "ATLASSIAN_API_TOKEN")
    token = os.getenv(env_name)
    if not user or not token:
        raise RuntimeError(f"Missing Jira credentials: user_name / {env_name}")
    return httpx.BasicAuth(user, token)


def _base(instance: dict[str, Any]) -> str:
    domain = str(instance.get("domain") or "").strip().rstrip("/")
    domain = domain.removeprefix("https://").removeprefix("http://")
    if not domain:
        raise RuntimeError("Missing Jira domain")
    return f"https://{domain}/rest/api/3"


def get_issue(config: dict[str, Any], issue_key: str, instance_name: str | None = None) -> tuple[str, dict[str, Any], dict[str, Any]]:
    name, instance = get_jira_instance(config, instance_name)
    with httpx.Client(auth=_auth(instance), timeout=30.0, follow_redirects=True, headers={"Accept": "application/json"}) as client:
        response = client.get(f"{_base(instance)}/issue/{issue_key}", params={"fields": "*all"})
        response.raise_for_status()
        return name, instance, dict(response.json())


def issue_to_canonical(issue: dict[str, Any], *, instance_name: str) -> dict[str, Any]:
    fields = issue.get("fields") or {}
    key = str(issue.get("key") or issue.get("id") or "issue")
    summary = str(fields.get("summary") or key)
    doc = canonical_from_adf(
        fields.get("description") if isinstance(fields.get("description"), dict) else None,
        title=f"{key} - {summary}",
        source={
            "type": "jira",
            "instance": instance_name,
            "issue_key": key,
            "issue_id": issue.get("id"),
            "summary": summary,
            "issue_type": ((fields.get("issuetype") or {}).get("name") if isinstance(fields.get("issuetype"), dict) else None),
            "status": ((fields.get("status") or {}).get("name") if isinstance(fields.get("status"), dict) else None),
        },
    )
    meta_lines = []
    for label, value in (
        ("Issue", key),
        ("Type", ((fields.get("issuetype") or {}).get("name") if isinstance(fields.get("issuetype"), dict) else "")),
        ("Status", ((fields.get("status") or {}).get("name") if isinstance(fields.get("status"), dict) else "")),
    ):
        if value:
            meta_lines.append({"type": "paragraph", "inlines": [
                {"type": "text", "text": f"{label}: ", "marks": [{"type": "strong"}]},
                {"type": "text", "text": str(value), "marks": []},
            ]})
    doc["blocks"] = [{"type": "heading", "level": 1, "inlines": [{"type": "text", "text": summary, "marks": []}]}] + meta_lines + doc["blocks"]
    doc["source"]["raw_fields"] = {
        "labels": fields.get("labels"), "components": fields.get("components"), "fixVersions": fields.get("fixVersions"),
        "parent": fields.get("parent"),
    }
    return doc


def export_issue(
    config: dict[str, Any], issue_key: str, destination: Path, *, instance_name: str | None = None,
) -> Path:
    name, _, issue = get_issue(config, issue_key, instance_name)
    doc = issue_to_canonical(issue, instance_name=name)
    package = destination / f"{safe_stem(issue_key)}__jira"
    package.mkdir(parents=True, exist_ok=True)
    write_json(package / f"{safe_stem(issue_key)}.jira.json", issue)
    write_canonical_package(
        doc,
        package,
        stem=safe_stem(issue_key),
        rag_profile=((config.get("profiles") or {}).get("rag") or {}),
        publication_profile=((config.get("profiles") or {}).get("publication") or {}),
    )
    return package


def _canonical_from_md_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    return canonical_from_markdown(text, title=path.stem, source={"type": "markdown", "original_path": str(path.resolve())})


def create_issue_from_markdown(
    config: dict[str, Any], markdown: Path, *, project: str, issue_type: str = "Story", summary: str | None = None,
    instance_name: str | None = None, parent: str | None = None,
) -> dict[str, Any]:
    _, instance = get_jira_instance(config, instance_name)
    doc = _canonical_from_md_file(markdown)
    fields: dict[str, Any] = {
        "project": {"key": project},
        "issuetype": {"name": issue_type},
        "summary": summary or str(doc.get("title") or markdown.stem),
        "description": canonical_to_adf(doc),
    }
    if parent:
        fields["parent"] = {"key": parent}
    payload = {"fields": fields}
    with httpx.Client(auth=_auth(instance), timeout=30.0, follow_redirects=True, headers={"Accept": "application/json", "Content-Type": "application/json"}) as client:
        response = client.post(f"{_base(instance)}/issue", json=payload)
        response.raise_for_status()
        return dict(response.json())

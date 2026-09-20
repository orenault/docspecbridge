from __future__ import annotations

import hashlib
import json
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx

from .adf import canonical_from_adf, canonical_to_adf
from .html_io import canonical_from_markdown
from .i18n import config_language
from .package_io import read_manifest, resolve_package_output, write_canonical_package
from .utils import safe_stem, write_json


def _instances(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return effective Jira instances.

    Jira-specific instances override matching Confluence/Atlassian instances, while
    non-overridden Confluence instances remain available.  This keeps the historical
    credential reuse behaviour without making it all-or-nothing as soon as one Jira
    override is created.
    """
    inherited = ((config.get("confluence") or {}).get("instances") or {})
    explicit = ((config.get("jira") or {}).get("instances") or {})
    merged = {str(name): dict(item or {}) for name, item in inherited.items()}
    for name, item in explicit.items():
        merged[str(name)] = dict(item or {})
    return merged


def jira_instances(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(k): dict(v or {}) for k, v in _instances(config).items()}


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


def _domain(instance: dict[str, Any]) -> str:
    domain = str(instance.get("domain") or "").strip().rstrip("/")
    domain = domain.removeprefix("https://").removeprefix("http://")
    if not domain:
        raise RuntimeError("Missing Jira domain")
    return domain


def _base(instance: dict[str, Any]) -> str:
    return f"https://{_domain(instance)}/rest/api/3"


def _client(instance: dict[str, Any], *, timeout: float = 60.0) -> httpx.Client:
    return httpx.Client(auth=_auth(instance), timeout=timeout, follow_redirects=True, headers={"Accept": "application/json"})


def _paged(client: httpx.Client, url: str, *, params: dict[str, Any] | None = None, values_key: str = "values") -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    start_at = 0
    page_size = 100
    while True:
        call_params = dict(params or {})
        call_params.setdefault("startAt", start_at)
        call_params.setdefault("maxResults", page_size)
        response = client.get(url, params=call_params)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list):
            return [dict(x) for x in data if isinstance(x, dict)]
        rows = data.get(values_key)
        if rows is None:
            # Jira uses several collection keys depending on the endpoint.
            for key in ("values", "comments", "issues", "projects"):
                if isinstance(data.get(key), list):
                    rows = data[key]
                    break
        rows = rows or []
        results.extend(dict(x) for x in rows if isinstance(x, dict))
        if data.get("isLast") is True:
            break
        total = data.get("total")
        returned = len(rows)
        if returned == 0:
            break
        start_at += returned
        if total is not None and start_at >= int(total):
            break
    return results


def list_projects(config: dict[str, Any], instance_name: str | None = None, *, query: str | None = None) -> list[dict[str, Any]]:
    _, instance = get_jira_instance(config, instance_name)
    params: dict[str, Any] = {"orderBy": "name"}
    if query:
        params["query"] = query
    with _client(instance) as client:
        return _paged(client, f"{_base(instance)}/project/search", params=params, values_key="values")


def list_issue_types(config: dict[str, Any], project: str, instance_name: str | None = None) -> list[dict[str, Any]]:
    _, instance = get_jira_instance(config, instance_name)
    with _client(instance) as client:
        # Resolve key/name/id via project search so /issuetype/project always receives an ID.
        rows = _paged(client, f"{_base(instance)}/project/search", params={"query": project}, values_key="values")
        wanted = project.casefold()
        match = next((p for p in rows if str(p.get("key") or "").casefold() == wanted), None)
        match = match or next((p for p in rows if str(p.get("name") or "").casefold() == wanted), None)
        match = match or next((p for p in rows if str(p.get("id") or "") == project), None)
        if not match:
            raise RuntimeError(f"Jira project not found: {project}")
        response = client.get(f"{_base(instance)}/issuetype/project", params={"projectId": str(match.get('id'))})
        response.raise_for_status()
        data = response.json()
        return [dict(x) for x in (data if isinstance(data, list) else data.get("values") or []) if isinstance(x, dict)]


def list_issues(
    config: dict[str, Any], project: str, instance_name: str | None = None, *,
    issue_type: str | None = None, query: str | None = None,
    next_page_token: str | None = None, max_results: int = 50,
) -> dict[str, Any]:
    """Return one Jira Cloud issue-discovery page for a project.

    Uses the enhanced JQL search endpoint so large projects are never loaded in full.
    Results are ordered by most recently updated issue. ``issue_type`` limits the
    discovery to one project issue type before any issue page is loaded. ``query``
    narrows that already-filtered page by key/summary/text, while ``next_page_token``
    fetches the following page.
    """
    _, instance = get_jira_instance(config, instance_name)
    project_key = str(project).replace('\\', '\\\\').replace('"', '\\"')
    clauses = [f'project = "{project_key}"']
    selected_type = str(issue_type or "").strip()
    if selected_type:
        escaped_type = selected_type.replace('\\', '\\\\').replace('"', '\\"')
        clauses.append(f'issuetype = "{escaped_type}"')
    search = str(query or "").strip()
    if search:
        escaped = search.replace('\\', '\\\\').replace('"', '\\"')
        if '-' in search and ' ' not in search:
            clauses.append(f'(key = "{escaped}" OR summary ~ "\\"{escaped}*\\"" OR text ~ "\\"{escaped}*\\"")')
        else:
            clauses.append(f'(summary ~ "\\"{escaped}*\\"" OR text ~ "\\"{escaped}*\\"")')
    jql = ' AND '.join(clauses) + ' ORDER BY updated DESC'
    params: list[tuple[str, Any]] = [
        ("jql", jql),
        ("maxResults", max(1, min(int(max_results), 100))),
    ]
    for field in ("summary", "status", "issuetype", "updated", "assignee"):
        params.append(("fields", field))
    if next_page_token:
        params.append(("nextPageToken", next_page_token))
    with _client(instance) as client:
        response = client.get(f"{_base(instance)}/search/jql", params=params)
        response.raise_for_status()
        data = dict(response.json())
    return {
        "issues": [dict(x) for x in (data.get("issues") or []) if isinstance(x, dict)],
        "next_page_token": data.get("nextPageToken"),
        "is_last": bool(data.get("isLast", not data.get("nextPageToken"))),
        "jql": jql,
    }


def get_issue(
    config: dict[str, Any], issue_key: str, instance_name: str | None = None, *, fields: str = "*all",
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    name, instance = get_jira_instance(config, instance_name)
    with _client(instance) as client:
        response = client.get(f"{_base(instance)}/issue/{issue_key}", params={"fields": fields, "expand": "names,schema"})
        response.raise_for_status()
        return name, instance, dict(response.json())


def get_comments(config: dict[str, Any], issue_key: str, instance_name: str | None = None) -> list[dict[str, Any]]:
    """Return comments exposed by the standard Jira Platform API."""
    _, instance = get_jira_instance(config, instance_name)
    with _client(instance) as client:
        return _paged(
            client,
            f"{_base(instance)}/issue/{issue_key}/comment",
            params={"orderBy": "created", "expand": "renderedBody"},
            values_key="comments",
        )


def _embedded_comments(issue: dict[str, Any]) -> tuple[list[dict[str, Any]], int | None]:
    embedded = ((issue.get("fields") or {}).get("comment") or {})
    if not isinstance(embedded, dict):
        return [], None
    rows = [dict(x) for x in (embedded.get("comments") or []) if isinstance(x, dict)]
    total = embedded.get("total")
    try:
        total_value = int(total) if total is not None else None
    except (TypeError, ValueError):
        total_value = None
    return rows, total_value


def _jsm_comments(instance: dict[str, Any], issue_key: str) -> list[dict[str, Any]]:
    """Best-effort Jira Service Management comment fallback.

    Jira Service Management exposes request comments through a separate REST surface.
    Non-JSM issues normally return 404; inaccessible requests can also return an empty
    page, so this function deliberately treats those cases as "no fallback comments".
    """
    results: list[dict[str, Any]] = []
    start = 0
    limit = 100
    url = f"https://{_domain(instance)}/rest/servicedeskapi/request/{issue_key}/comment"
    with _client(instance) as client:
        while True:
            response = client.get(url, params={"start": start, "limit": limit})
            if response.status_code in {400, 403, 404}:
                return results
            response.raise_for_status()
            data = response.json()
            rows = data.get("values") or [] if isinstance(data, dict) else []
            for raw in rows:
                if not isinstance(raw, dict):
                    continue
                item = dict(raw)
                created = item.get("created")
                if not created and isinstance(item.get("createdDate"), dict):
                    date = item.get("createdDate") or {}
                    created = date.get("iso8601") or date.get("jira") or date.get("friendly")
                if created:
                    item["created"] = created
                item.setdefault("_docspecbridge_source", "jsm")
                results.append(item)
            if not isinstance(data, dict) or data.get("isLastPage") is True:
                break
            size = int(data.get("size") or len(rows) or 0)
            if size <= 0:
                break
            start = int(data.get("start") or start) + size
    return results


def _comment_identity(comment: dict[str, Any]) -> tuple[str, str, str]:
    comment_id = str(comment.get("id") or "").strip()
    author = (comment.get("author") or {}) if isinstance(comment.get("author"), dict) else {}
    author_id = str(author.get("accountId") or author.get("displayName") or "").strip()
    created = str(comment.get("created") or "").strip()
    if comment_id:
        return ("id", comment_id, "")
    body = comment.get("body")
    if isinstance(body, dict):
        body_key = json.dumps(body, ensure_ascii=False, sort_keys=True)
    else:
        body_key = str(body or "")
    return (author_id, created, body_key)


def collect_comments(
    config: dict[str, Any], issue_key: str, issue: dict[str, Any], instance_name: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Collect visible comments from Jira Platform, embedded issue data and JSM.

    The sources are merged and de-duplicated.  JSM is queried when the issue looks like
    a service-desk issue or when the standard Jira API returned nothing.
    """
    warnings: list[str] = []
    comments: list[dict[str, Any]] = []
    platform_error: str | None = None
    try:
        comments.extend(get_comments(config, issue_key, instance_name))
    except Exception as exc:
        platform_error = str(exc)
        warnings.append(f"Jira comments API failed: {exc}")

    embedded, embedded_total = _embedded_comments(issue)
    comments.extend(embedded)

    _, instance = get_jira_instance(config, instance_name)
    project = ((issue.get("fields") or {}).get("project") or {})
    project_type = str(project.get("projectTypeKey") or "") if isinstance(project, dict) else ""
    if project_type == "service_desk" or not comments:
        try:
            comments.extend(_jsm_comments(instance, issue_key))
        except Exception as exc:
            warnings.append(f"Jira Service Management comments fallback failed: {exc}")

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in comments:
        if not isinstance(item, dict):
            continue
        identity = _comment_identity(item)
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(dict(item))

    if not deduped and embedded_total and embedded_total > 0:
        warnings.append(
            f"Jira reports {embedded_total} comment(s) on {issue_key}, but none were returned to the configured API account."
        )
    elif not deduped and platform_error:
        warnings.append(f"No Jira comment could be extracted for {issue_key}.")
    return deduped, warnings


def get_changelog(config: dict[str, Any], issue_key: str, instance_name: str | None = None) -> list[dict[str, Any]]:
    _, instance = get_jira_instance(config, instance_name)
    with _client(instance) as client:
        return _paged(client, f"{_base(instance)}/issue/{issue_key}/changelog", values_key="values")


def _walk_blocks(blocks: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for block in blocks or []:
        yield block
        for key in ("blocks",):
            yield from _walk_blocks(block.get(key) or [])
        for item in block.get("items") or []:
            yield from _walk_blocks(item.get("blocks") or [])
        for row in block.get("rows") or []:
            for cell in row.get("cells") or []:
                yield from _walk_blocks(cell.get("blocks") or [])


def _rewrite_downloaded_media(doc: dict[str, Any], attachments: list[dict[str, Any]]) -> None:
    """Rewrite Jira remote attachment/media references to package-local files.

    Jira ADF media IDs are not always the numeric attachment IDs.  Matching therefore
    also uses filename/alt text and link URLs, which preserves inline images far more
    reliably while still keeping every downloaded attachment in the package.
    """
    mapping: dict[str, str] = {}
    for item in attachments:
        local = str(item.get("file") or "")
        if not local or not item.get("saved"):
            continue
        for key in (
            item.get("id"), item.get("content"), item.get("thumbnail"), item.get("self"),
            item.get("filename"), Path(local).name,
        ):
            if key:
                value = str(key)
                mapping[value] = local
                mapping[value.rsplit("/", 1)[-1]] = local
    for block in _walk_blocks(doc.get("blocks") or []):
        if block.get("type") == "image":
            src = str(block.get("src") or "")
            media = block.get("media") or {}
            candidates = [
                src, src.rsplit("/", 1)[-1], str(block.get("alt") or ""),
                str(media.get("id") or ""), str(media.get("url") or ""), str(media.get("alt") or ""),
            ]
            local = next((mapping[c] for c in candidates if c and c in mapping), None)
            if local:
                block["src"] = local
        for inline in block.get("inlines") or []:
            for mark in inline.get("marks") or []:
                if mark.get("type") != "link":
                    continue
                href = str(mark.get("href") or "")
                candidates = [href, href.rsplit("/", 1)[-1], str(inline.get("text") or "")]
                local = next((mapping[c] for c in candidates if c and c in mapping), None)
                if local:
                    mark["href"] = local


def _append_attachments_section(doc: dict[str, Any], attachments: list[dict[str, Any]], *, title: str = "Attachments") -> None:
    saved = [item for item in attachments if item.get("saved") and item.get("file")]
    if not saved:
        return
    existing_images = {
        str(block.get("src") or "")
        for block in _walk_blocks(doc.get("blocks") or [])
        if block.get("type") == "image"
    }
    doc["blocks"].append({"type": "heading", "level": 2, "inlines": [{"type": "text", "text": title, "marks": []}]})
    for item in saved:
        local = str(item.get("file") or "")
        filename = str(item.get("filename") or Path(local).name)
        mime = str(item.get("mime_type") or "")
        if mime.startswith("image/") and local not in existing_images:
            doc["blocks"].append({"type": "image", "src": local, "alt": filename, "role": "attachment"})
            existing_images.add(local)
        else:
            doc["blocks"].append({
                "type": "paragraph",
                "inlines": [{"type": "text", "text": filename, "marks": [{"type": "link", "href": local}]}],
            })


def _useful_fields(fields: dict[str, Any], names: dict[str, Any] | None = None) -> dict[str, Any]:
    names = names or {}
    out: dict[str, Any] = {}
    for key, value in fields.items():
        if key in {"description", "attachment", "comment"}:
            continue
        if value in (None, "", [], {}):
            continue
        out[key] = {"name": names.get(key, key), "value": value}
    return out


def issue_to_canonical(
    issue: dict[str, Any], *, instance_name: str, comments: list[dict[str, Any]] | None = None,
    downloaded_attachments: list[dict[str, Any]] | None = None, comments_title: str = "Comments",
    attachments_title: str = "Attachments",
) -> dict[str, Any]:
    fields = issue.get("fields") or {}
    key = str(issue.get("key") or issue.get("id") or "issue")
    summary = str(fields.get("summary") or key)
    doc = canonical_from_adf(
        fields.get("description") if isinstance(fields.get("description"), dict) else None,
        title=f"{key} - {summary}",
        source={
            "type": "jira", "instance": instance_name, "issue_key": key, "issue_id": issue.get("id"),
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
        ("Project", ((fields.get("project") or {}).get("key") if isinstance(fields.get("project"), dict) else "")),
        ("Assignee", ((fields.get("assignee") or {}).get("displayName") if isinstance(fields.get("assignee"), dict) else "")),
    ):
        if value:
            meta_lines.append({"type": "paragraph", "inlines": [
                {"type": "text", "text": f"{label}: ", "marks": [{"type": "strong"}]},
                {"type": "text", "text": str(value), "marks": []},
            ]})
    doc["blocks"] = [{"type": "heading", "level": 1, "inlines": [{"type": "text", "text": summary, "marks": []}]}] + meta_lines + doc["blocks"]
    doc["source"]["fields"] = _useful_fields(fields, issue.get("names") or {})

    if downloaded_attachments:
        doc["assets"].extend(downloaded_attachments)
        _rewrite_downloaded_media(doc, downloaded_attachments)

    if comments:
        doc["blocks"].append({"type": "heading", "level": 2, "inlines": [{"type": "text", "text": comments_title, "marks": []}]})
        for comment in comments:
            author = (comment.get("author") or {}).get("displayName") or ""
            created = comment.get("created") or ""
            label = " — ".join(x for x in (str(author), str(created)) if x)
            if label:
                doc["blocks"].append({"type": "heading", "level": 3, "inlines": [{"type": "text", "text": label, "marks": []}]})
            body = comment.get("body")
            if isinstance(body, dict):
                cdoc = canonical_from_adf(body, title="comment")
                doc["blocks"].extend(cdoc.get("blocks") or [])
            elif body not in (None, ""):
                doc["blocks"].append({"type": "paragraph", "inlines": [{"type": "text", "text": str(body), "marks": []}]})
    if downloaded_attachments:
        _rewrite_downloaded_media(doc, downloaded_attachments)
        _append_attachments_section(doc, downloaded_attachments, title=attachments_title)
    return doc


def _download_attachments(instance: dict[str, Any], issue: dict[str, Any], package: Path) -> list[dict[str, Any]]:
    rows = ((issue.get("fields") or {}).get("attachment") or [])
    if not rows:
        return []
    target_dir = package / "attachments"
    target_dir.mkdir(parents=True, exist_ok=True)
    assets: list[dict[str, Any]] = []
    with _client(instance, timeout=120.0) as client:
        for item in rows:
            filename = Path(str(item.get("filename") or item.get("id") or "attachment.bin")).name
            target = target_dir / filename
            url = str(item.get("content") or "")
            saved = False
            error = None
            attachment_id = str(item.get("id") or "")
            if attachment_id or url:
                try:
                    if attachment_id:
                        response = client.get(
                            f"{_base(instance)}/attachment/content/{attachment_id}",
                            params={"redirect": "false"},
                        )
                    else:
                        response = client.get(url)
                    response.raise_for_status()
                    target.write_bytes(response.content)
                    saved = True
                except Exception as exc:
                    error = str(exc)
            assets.append({
                "role": "attachment", "file": str(Path("attachments") / filename).replace("\\", "/"),
                "saved": saved, "id": item.get("id"), "filename": filename, "mime_type": item.get("mimeType"),
                "size": item.get("size"), "content": item.get("content"), "thumbnail": item.get("thumbnail"),
                "self": item.get("self"),
                **({"error": error} if error else {}),
            })
    return assets


def export_issue(config: dict[str, Any], issue_key: str, destination: Path, *, instance_name: str | None = None) -> Path:
    name, instance, issue = get_issue(config, issue_key, instance_name)
    export_cfg = ((config.get("jira") or {}).get("export") or {})
    package = destination / f"{safe_stem(issue_key)}__jira"
    package.mkdir(parents=True, exist_ok=True)
    comment_warnings: list[str] = []
    if export_cfg.get("include_comments", True):
        comments, comment_warnings = collect_comments(config, issue_key, issue, name)
    else:
        comments = []
    attachments = _download_attachments(instance, issue, package) if export_cfg.get("include_attachments", True) else []
    changelog = get_changelog(config, issue_key, name) if export_cfg.get("include_changelog", False) else []
    lang = config_language(config)
    labels = {
        "fr": ("Commentaires", "Pièces jointes"),
        "de": ("Kommentare", "Anhänge"),
        "es": ("Comentarios", "Adjuntos"),
        "zh": ("评论", "附件"),
    }.get(lang, ("Comments", "Attachments"))
    doc = issue_to_canonical(
        issue, instance_name=name, comments=comments, downloaded_attachments=attachments,
        comments_title=labels[0], attachments_title=labels[1],
    )
    if comment_warnings:
        doc.setdefault("diagnostics", {}).setdefault("warnings", []).extend(comment_warnings)
    failed_attachments = [a for a in attachments if not a.get("saved")]
    if failed_attachments:
        doc.setdefault("diagnostics", {}).setdefault("warnings", []).extend(
            f"Jira attachment not downloaded: {a.get('filename')} ({a.get('error', 'unknown error')})"
            for a in failed_attachments
        )
    write_json(package / f"{safe_stem(issue_key)}.jira.json", issue)
    if export_cfg.get("include_comments", True):
        # Always materialise the comments payload, even when empty, so missing comments
        # can be diagnosed without guessing whether comment extraction was enabled.
        write_json(package / f"{safe_stem(issue_key)}.comments.json", comments)
    if changelog:
        write_json(package / f"{safe_stem(issue_key)}.changelog.json", changelog)
    write_canonical_package(
        doc, package, stem=safe_stem(issue_key),
        rag_profile=((config.get("profiles") or {}).get("rag") or {}),
        publication_profile=((config.get("profiles") or {}).get("publication") or {}),
        extra_manifest={"jira": {"issue_key": issue_key, "comments": len(comments), "attachments": len(attachments), "changelog": len(changelog)}},
    )
    if export_cfg.get("zip_package", False):
        import shutil
        shutil.make_archive(str(package), "zip", root_dir=package.parent, base_dir=package.name)
    return package


def _canonical_from_source(path: Path) -> tuple[dict[str, Any], Path, Path]:
    """Return canonical document, package root, and source markdown/document path."""
    source = path.resolve()
    if source.is_dir():
        package = source
        document = resolve_package_output(package, "document_json", "document.json")
        if not document:
            raise FileNotFoundError(f"No CanonicalDocument found in package: {package}")
        return json.loads(document.read_text(encoding="utf-8")), package, document
    if source.suffix.lower() == ".md":
        package = source.parent
        manifest = read_manifest(package)
        document = resolve_package_output(package, "document_json", "document.json") if manifest else None
        if document:
            return json.loads(document.read_text(encoding="utf-8")), package, source
        text = source.read_text(encoding="utf-8")
        return canonical_from_markdown(text, title=source.stem, source={"type": "markdown", "original_path": str(source)}), package, source
    if source.suffix.lower() == ".json":
        data = json.loads(source.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("schema_version") and "blocks" in data:
            return data, source.parent, source
    raise ValueError("Jira import accepts a DocSpecBridge package, canonical JSON, or Markdown file.")


def _local_asset_paths(doc: dict[str, Any], package: Path) -> list[tuple[str, Path]]:
    values: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for asset in doc.get("assets") or []:
        rel = str(asset.get("file") or "")
        if rel:
            candidate = package / rel
            if candidate.is_file() and candidate not in seen:
                seen.add(candidate); values.append((rel, candidate))
    for block in _walk_blocks(doc.get("blocks") or []):
        if block.get("type") == "image":
            rel = str(block.get("src") or "")
            if rel and not rel.startswith(("http://", "https://", "data:", "#")):
                candidate = package / rel
                if candidate.is_file() and candidate not in seen:
                    seen.add(candidate); values.append((rel, candidate))
    return values


def _fingerprint(path: Path) -> str:
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = path / "manifest.json"
    return hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.is_file() else hashlib.sha256(str(path).encode()).hexdigest()


def _state_path(package: Path) -> Path:
    return package / "jira_publication_state.json"


def _load_state(package: Path) -> dict[str, Any]:
    path = _state_path(package)
    if not path.is_file():
        return {"schema_version": "1.0", "publications": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("publications", [])
            return data
    except Exception:
        pass
    return {"schema_version": "1.0", "publications": []}


def _save_state(package: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json(_state_path(package), state)


def _upload_attachment(client: httpx.Client, instance: dict[str, Any], issue_key: str, path: Path) -> dict[str, Any]:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    with path.open("rb") as handle:
        response = client.post(
            f"{_base(instance)}/issue/{issue_key}/attachments",
            headers={"X-Atlassian-Token": "no-check", "Accept": "application/json"},
            files={"file": (path.name, handle, mime)},
        )
    response.raise_for_status()
    rows = response.json()
    if not rows:
        raise RuntimeError(f"Jira returned no attachment metadata for {path.name}")
    return dict(rows[0])


def create_issue_from_markdown(
    config: dict[str, Any], markdown: Path, *, project: str, issue_type: str = "Story", summary: str | None = None,
    instance_name: str | None = None, parent: str | None = None,
) -> dict[str, Any]:
    """Create or resume a Jira issue publication from Markdown or a DocSpecBridge package.

    The state file is written after every phase, so a retry reuses the created issue and
    already-uploaded attachments instead of creating duplicates.
    """
    name, instance = get_jira_instance(config, instance_name)
    doc, package, source_path = _canonical_from_source(markdown)
    final_summary = summary or str(doc.get("title") or markdown.stem)
    import_cfg = ((config.get("jira") or {}).get("import") or {})
    resume = bool(import_cfg.get("resume", True))
    fingerprint = _fingerprint(package if (package / "manifest.json").is_file() else source_path)
    state = _load_state(package)
    row = next((x for x in state["publications"] if x.get("instance") == name and x.get("project") == project and x.get("issue_type") == issue_type and x.get("source_fingerprint") == fingerprint), None)
    if row is None:
        row = {"instance": name, "project": project, "issue_type": issue_type, "source_fingerprint": fingerprint, "attachments": {}, "status": "new"}
        state["publications"].append(row)

    issue_key = str(row.get("issue_key") or "") if resume else ""
    was_resumed = bool(issue_key)
    try:
        with _client(instance, timeout=120.0) as client:
            if issue_key:
                check = client.get(f"{_base(instance)}/issue/{issue_key}", params={"fields": "summary,attachment"})
                if check.status_code == 404:
                    issue_key = ""
                else:
                    check.raise_for_status()
            if not issue_key:
                fields: dict[str, Any] = {
                    "project": {"key": project}, "issuetype": {"name": issue_type},
                    "summary": final_summary, "description": canonical_to_adf(doc),
                }
                if parent:
                    fields["parent"] = {"key": parent}
                response = client.post(f"{_base(instance)}/issue", headers={"Content-Type": "application/json"}, json={"fields": fields})
                response.raise_for_status()
                created = dict(response.json())
                issue_key = str(created.get("key") or created.get("id") or "")
                if not issue_key:
                    raise RuntimeError("Jira created the issue but returned no issue key/id")
                row.update({"issue_key": issue_key, "issue_id": created.get("id"), "status": "issue-created", "summary": final_summary})
                _save_state(package, state)

            issue = client.get(f"{_base(instance)}/issue/{issue_key}", params={"fields": "attachment"})
            issue.raise_for_status()
            existing = {str(a.get("filename")): dict(a) for a in ((issue.json().get("fields") or {}).get("attachment") or [])}
            media_urls: dict[str, str] = {}
            for rel, asset_path in _local_asset_paths(doc, package):
                if not bool(import_cfg.get("upload_images", True)) and rel.startswith("images/"):
                    continue
                if not bool(import_cfg.get("upload_attachments", True)) and rel.startswith("attachments/"):
                    continue
                uploaded = existing.get(asset_path.name) or (row.get("attachments") or {}).get(asset_path.name)
                if not uploaded:
                    uploaded = _upload_attachment(client, instance, issue_key, asset_path)
                    row.setdefault("attachments", {})[asset_path.name] = uploaded
                    _save_state(package, state)
                content_url = str(uploaded.get("content") or f"https://{_domain(instance)}/rest/api/3/attachment/content/{uploaded.get('id')}")
                media_urls[rel] = content_url
                media_urls[asset_path.name] = content_url

            # Second pass: replace local image references by the Jira attachment URLs at
            # the original canonical block positions.
            final_description = canonical_to_adf(doc, media_urls=media_urls)
            update = client.put(
                f"{_base(instance)}/issue/{issue_key}",
                headers={"Content-Type": "application/json"},
                json={"fields": {"summary": final_summary, "description": final_description}},
            )
            update.raise_for_status()
            row.update({"status": "complete", "summary": final_summary, "completed_at": datetime.now(timezone.utc).isoformat()})
            _save_state(package, state)
            return {"key": issue_key, "id": row.get("issue_id"), "resumed": was_resumed, "attachments": list((row.get("attachments") or {}).values()), "state": str(_state_path(package))}
    except Exception as exc:
        row.update({"status": "partial", "error": str(exc), "failed_at": datetime.now(timezone.utc).isoformat()})
        _save_state(package, state)
        raise RuntimeError(f"Jira publication stopped after a partial state was saved in {_state_path(package)}: {exc}") from exc

from __future__ import annotations

import os
import tempfile
import zipfile
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from .extractor import ExtractionOutcome, XbergExtractor


def _clean_repo_path(path: str) -> str:
    value = path.strip("/")
    if value.endswith(".git"):
        value = value[:-4]
    return value


def _provider(url: str) -> tuple[str, str, str]:
    parsed = urlparse(url if "://" in url else "https://" + url)
    host = (parsed.hostname or "").lower()
    path = _clean_repo_path(parsed.path)
    if not host or not path:
        raise ValueError("Invalid Git repository URL")
    if host == "github.com" or host.endswith(".github.com"):
        if len(path.split("/")) != 2:
            raise ValueError("GitHub URL must identify a repository, for example https://github.com/org/repo")
        return "github", host, path
    # gitlab.com and self-managed GitLab use the same /api/v4 contract.
    return "gitlab", host, path


def _headers(config: dict[str, Any], provider: str) -> dict[str, str]:
    git_cfg = config.get("git") or {}
    if provider == "github":
        env_name = str(git_cfg.get("github_token_env") or "GITHUB_TOKEN")
        token = os.getenv(env_name, "").strip()
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers
    env_name = str(git_cfg.get("gitlab_token_env") or "GITLAB_TOKEN")
    token = os.getenv(env_name, "").strip()
    return {"PRIVATE-TOKEN": token} if token else {}


def _repository_archive(config: dict[str, Any], url: str, ref: str | None = None) -> tuple[bytes, dict[str, str]]:
    provider, host, project = _provider(url)
    headers = _headers(config, provider)
    timeout = float((config.get("git") or {}).get("timeout_seconds", 60))
    verify = bool((config.get("git") or {}).get("verify_ssl", True))
    with httpx.Client(timeout=timeout, follow_redirects=True, verify=verify) as client:
        if provider == "github":
            api = f"https://api.github.com/repos/{project}"
            meta_resp = client.get(api, headers=headers)
            if meta_resp.status_code in {401, 403, 404} and not headers.get("Authorization"):
                raise RuntimeError("GitHub repository is not publicly accessible. Configure GITHUB_TOKEN for a private repository.")
            meta_resp.raise_for_status()
            metadata = meta_resp.json()
            branch = ref or str(metadata.get("default_branch") or "main")
            archive_resp = client.get(f"{api}/zipball/{quote(branch, safe='')}", headers=headers)
        else:
            encoded = quote(project, safe="")
            api = f"https://{host}/api/v4/projects/{encoded}"
            meta_resp = client.get(api, headers=headers)
            if meta_resp.status_code in {401, 403, 404} and not headers.get("PRIVATE-TOKEN"):
                raise RuntimeError("GitLab repository is not publicly accessible. Configure GITLAB_TOKEN for a private repository.")
            meta_resp.raise_for_status()
            metadata = meta_resp.json()
            branch = ref or str(metadata.get("default_branch") or "main")
            archive_resp = client.get(f"{api}/repository/archive.zip", params={"sha": branch}, headers=headers)
        archive_resp.raise_for_status()
        return archive_resp.content, {"provider": provider, "host": host, "project": project, "ref": branch, "url": url}


def _archive_root(extract_dir: Path) -> Path:
    children = [p for p in extract_dir.iterdir() if p.name != "__MACOSX"]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return extract_dir


async def extract_git_repository(
    config: dict[str, Any],
    url: str,
    destination: Path,
    *,
    recursive: bool | None = None,
    ref: str | None = None,
) -> tuple[list[ExtractionOutcome], dict[str, str]]:
    """Download a GitHub/GitLab archive over HTTP and extract its Markdown files.

    No local git executable, SSH key or token is required for public repositories.
    Tokens are only read from optional environment variables for private repositories.
    """
    payload, context = _repository_archive(config, url, ref=ref)
    runtime = deepcopy(config)
    git_cfg = runtime.get("git") or {}
    runtime.setdefault("app", {})["recursive"] = bool(git_cfg.get("recursive", True) if recursive is None else recursive)
    runtime.setdefault("_runtime", {})["extensions"] = [".md", ".markdown"]
    runtime["_runtime"]["git_context"] = context

    with tempfile.TemporaryDirectory(prefix="docspecbridge-git-") as temp_name:
        temp = Path(temp_name)
        archive = temp / "repository.zip"
        archive.write_bytes(payload)
        expanded = temp / "repository"
        expanded.mkdir()
        try:
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(expanded)
        except zipfile.BadZipFile as exc:
            raise RuntimeError("The Git repository archive is not a valid ZIP file") from exc
        root = _archive_root(expanded)
        runtime["_runtime"]["git_root"] = str(root)
        outcomes = await XbergExtractor(runtime).extract_source(root, destination)
    return outcomes, context


def run_extract_git_repository(
    config: dict[str, Any],
    url: str,
    destination: Path,
    *,
    recursive: bool | None = None,
    ref: str | None = None,
) -> tuple[list[ExtractionOutcome], dict[str, str]]:
    import asyncio
    return asyncio.run(extract_git_repository(config, url, destination, recursive=recursive, ref=ref))

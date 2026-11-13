#!/usr/bin/env python3
"""Post or update the sticky slopo duplication review comment."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

MARKER: Final[str] = "<!-- slopo-review -->"
MAX_CLUSTERS: Final[int] = 10
MAX_SNIPPET_LINES: Final[int] = 30
MAX_LOCATIONS_PER_CELL: Final[int] = 3
PAGE_LIMIT: Final[int] = 10
DEFAULT_API_URL: Final[str] = "https://api.github.com"
HTTP_OK: Final[int] = 200
HTTP_SUCCESS_LIMIT: Final[int] = 300
CLUSTER_HEADER: Final = re.compile(
    r"^## \((\d+)\) score ([0-9.]+(?:-[0-9.]+)?)[ \t]*$", re.MULTILINE
)
CLUSTER_SEPARATOR: Final = re.compile(r"^### ______ \d+ ______[ \t]*$", re.MULTILINE)
CLUSTER_BULLET: Final = re.compile(
    r"^- (\*\*CHANGED\*\* )?`([^`]+)` lines (\d+)-(\d+)[ \t]*$",
    re.MULTILINE,
)
CLUSTER_FENCE: Final = re.compile(r"```([A-Za-z0-9+#-]*)\n(.*?)```", re.DOTALL)
CLUSTER_FILE: Final = re.compile(r"cluster-\d+\.md")
NEXT_LINK: Final = re.compile(r'<([^>]+)>;\s*rel="next"')


@dataclass(frozen=True)
class Location:
    file: str
    start: int
    end: int


@dataclass(frozen=True)
class Snippet:
    language: str
    code: str


@dataclass(frozen=True)
class Cluster:
    number: int
    score: str
    changed: list[Location]
    similar: list[Location]
    snippet: Snippet | None


def source_dir_from_config(config_text: str) -> str | None:
    match = re.search(r"^source_dir:[ \t]*(.+?)[ \t]*$", config_text, re.MULTILINE)
    if match is None:
        return None
    value = match[1].strip("'\"").strip()
    if value in {"", "."}:
        return None
    return value.rstrip("/")


def repository_path(source_dir: str | None, file_path: str) -> str:
    normalized = file_path.replace("\\", "/")
    if source_dir is None or normalized.startswith(f"{source_dir}/"):
        return normalized
    return f"{source_dir}/{normalized}"


def parse_cluster(text: str) -> Cluster:
    header = CLUSTER_HEADER.search(text)
    if header is None:
        raise ValueError("Unrecognized slopo cluster report header")
    changed: list[Location] = []
    similar: list[Location] = []
    snippets: list[Snippet] = []
    for block in CLUSTER_SEPARATOR.split(text)[1:]:
        for match in CLUSTER_BULLET.finditer(block):
            location = Location(file=match[2], start=int(match[3]), end=int(match[4]))
            if match[1] is None:
                similar.append(location)
            else:
                changed.append(location)
        fences = CLUSTER_FENCE.findall(block)
        if fences:
            language, code = fences[-1]
            if code.strip():
                snippets.append(Snippet(language=language or "text", code=code.rstrip()))
    return Cluster(
        number=int(header[1]),
        score=header[2],
        changed=changed,
        similar=similar,
        snippet=snippets[0] if snippets else None,
    )


def parse_review_report(report_dir: Path) -> list[Cluster] | None:
    try:
        names = list(report_dir.iterdir())
    except FileNotFoundError:
        return None
    clusters = [
        parse_cluster((report_dir / name.name).read_text(encoding="utf-8"))
        for name in names
        if CLUSTER_FILE.fullmatch(name.name) is not None
    ]
    clusters.sort(key=lambda cluster: cluster.number)
    return clusters


def format_locations(records: list[Location], source_dir: str | None) -> str:
    if not records:
        return "—"
    shown = records[:MAX_LOCATIONS_PER_CELL]
    lines = [
        f"`{repository_path(source_dir, record.file)}:{record.start}-{record.end}`"
        for record in shown
    ]
    if len(records) > len(shown):
        lines.append(f"+{len(records) - len(shown)} more")
    return "<br>".join(lines)


def trim_snippet(code: str) -> str:
    lines = code.split("\n")
    if len(lines) <= MAX_SNIPPET_LINES:
        return code
    remaining = len(lines) - MAX_SNIPPET_LINES
    return "\n".join(
        [*lines[:MAX_SNIPPET_LINES], f"... {remaining} more lines in the report artifact"],
    )


def build_comment_body(
    clusters: list[Cluster],
    *,
    source_dir: str | None = None,
    run_url: str = "",
    cluster_limit: int = MAX_CLUSTERS,
) -> str:
    header = f"{MARKER}\n## Slopo duplication review\n"
    if not clusters:
        return f"{header}\nNo similar code involving this change was found.\n"
    shown = clusters[:cluster_limit]
    rows = [
        f"| {cluster.number} | {cluster.score} | {format_locations(cluster.changed, source_dir)} | "
        f"{format_locations(cluster.similar, source_dir)} |"
        for cluster in shown
    ]
    details = [
        f"<details><summary>Cluster {cluster.number} snippet (score {cluster.score})</summary>\n\n"
        f"```{cluster.snippet.language}\n{trim_snippet(cluster.snippet.code)}\n```\n\n</details>"
        for cluster in shown
        if cluster.snippet is not None
    ]
    hidden = len(clusters) - len(shown)
    summary = (
        f"\nFound **{len(clusters)}** cluster(s) of similar code involving this change. "
        "These are embedding-based hints, not required fixes; slopo can report intentional "
        "similarity as noise.\n"
    )
    rows_text = "\n".join(rows)
    table = f"\n| # | Score | Changed | Similar code |\n| --- | --- | --- | --- |\n{rows_text}\n"
    remainder = (
        f"\n{hidden} further cluster(s) are only in the report artifact.\n" if hidden > 0 else ""
    )
    footer = f"\nFull report and run log: [workflow run]({run_url}).\n"
    return "".join(
        [header, summary, table, remainder, *[f"\n{entry}\n" for entry in details], footer],
    )


def next_link(link_header: str | None) -> str | None:
    if link_header is None:
        return None
    match = NEXT_LINK.search(link_header)
    return None if match is None else match[1]


def request(
    url: str,
    *,
    headers: Mapping[str, str],
    method: str = "GET",
    body: bytes | None = None,
) -> tuple[int, Any, str]:
    outgoing = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        with urllib.request.urlopen(outgoing) as response:
            return response.status, response.headers, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read().decode()


def find_sticky_comment(
    *,
    api_url: str,
    repository: str,
    pr_number: str,
    headers: Mapping[str, str],
) -> int | None:
    url: str | None = f"{api_url}/repos/{repository}/issues/{pr_number}/comments?per_page=100"
    for _ in range(PAGE_LIMIT):
        if url is None:
            break
        status, response_headers, text = request(url, headers=headers)
        if not HTTP_OK <= status < HTTP_SUCCESS_LIMIT:
            raise RuntimeError(f"GitHub comment listing failed: {status} {text}")
        comments = json.loads(text)
        if isinstance(comments, list):
            match = next(
                (
                    comment
                    for comment in comments
                    if isinstance(comment, dict)
                    and isinstance(comment.get("body"), str)
                    and comment["body"].startswith(MARKER)
                ),
                None,
            )
            if match is not None:
                comment_id = match.get("id")
                if isinstance(comment_id, int):
                    return comment_id
        url = next_link(response_headers.get("link"))
    return None


def upsert_sticky_comment(
    *,
    api_url: str,
    repository: str,
    pr_number: str,
    body: str,
    headers: Mapping[str, str],
) -> str:
    existing = find_sticky_comment(
        api_url=api_url,
        repository=repository,
        pr_number=pr_number,
        headers=headers,
    )
    creating = existing is None
    url = (
        f"{api_url}/repos/{repository}/issues/{pr_number}/comments"
        if creating
        else f"{api_url}/repos/{repository}/issues/comments/{existing}"
    )
    payload = json.dumps({"body": body}).encode()
    status, _, text = request(
        url,
        headers=headers,
        method="POST" if creating else "PATCH",
        body=payload,
    )
    if not HTTP_OK <= status < HTTP_SUCCESS_LIMIT:
        action = "create" if creating else "update"
        raise RuntimeError(f"GitHub comment {action} failed: {status} {text}")
    return "created" if creating else "updated"


def read_source_dir(config_path: Path) -> str | None:
    try:
        return source_dir_from_config(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def require_env(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if value is None or value == "":
        raise RuntimeError(f"Missing required environment variable {name}")
    return value


def main(environment: Mapping[str, str] | None = None) -> None:
    env = os.environ if environment is None else environment
    repository = require_env(env, "GITHUB_REPOSITORY")
    token = require_env(env, "GITHUB_TOKEN")
    pr_number = require_env(env, "PR_NUMBER")
    api_url = env.get("GITHUB_API_URL", DEFAULT_API_URL)
    report_dir = Path(env.get("SLOPO_REPORT_DIR", ".dealer-local/slopo/report"))
    source_dir = read_source_dir(Path(env.get("SLOPO_CONFIG", "slopo.conf.yaml")))
    clusters = parse_review_report(report_dir) or []
    body = build_comment_body(
        clusters,
        source_dir=source_dir,
        run_url=env.get("RUN_URL", ""),
    )
    headers = {
        "accept": "application/vnd.github+json",
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
        "user-agent": "dealer-slopo-review",
        "x-github-api-version": "2022-11-28",
    }
    action = upsert_sticky_comment(
        api_url=api_url,
        repository=repository,
        pr_number=pr_number,
        body=body,
        headers=headers,
    )
    print(f"Slopo review comment {action} for {repository}#{pr_number}")
    summary_path = env.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as stream:
            stream.write(f"{body}\n")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError) as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from error

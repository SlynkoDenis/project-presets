#!/usr/bin/env python3
"""Dead-code nightly baseline, verification, and publication controls."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

BRANCH_PREFIX = "agent/dead-code-removal-"
PUBLISH_JOB = "Publish draft pull request"

WORKFLOW_FILE = "dead-code-nightly.yml"
LOOKBACK_HOURS = 24
MAX_SCOPE_COMMITS = 100
MAX_SCOPE_FILES = 200
MAX_SCOPE_PULL_REQUESTS = 50
MAX_BODY_PULL_REQUESTS = 30
MAX_BODY_LENGTH = 60_000
PAGE_LIMIT = 100
NUMSTAT_FIELDS = 2
SHA = re.compile(r"[a-f0-9]{40}")


@dataclass(frozen=True)
class Scope:
    base: str
    head: str
    branch: str
    boundary: datetime
    baseline_run: dict[str, Any] | None
    merged_pull_requests: list[dict[str, Any]]


def require_value(value: str | None, name: str) -> str:
    if value is None or value.strip() == "":
        raise ValueError(f"{name} is required")
    return value


def parse_date(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def isoformat(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def date_string(value: datetime | None = None) -> str:
    if value is None:
        value = datetime.now(tz=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%d")


def git(args: Sequence[str]) -> str:
    result = subprocess.run(["git", *args], capture_output=True, check=False, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"git {' '.join(args)} failed{suffix}")
    return result.stdout.rstrip()


def git_status(args: Sequence[str]) -> int:
    result = subprocess.run(["git", *args], capture_output=True, check=False, text=True)
    return result.returncode


def gh(args: Sequence[str], token: str) -> str:
    environment = {**os.environ, "GH_TOKEN": token}
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"gh {' '.join(args[:2])} failed{suffix}")
    return result.stdout.rstrip()


def parse_json(text: str, label: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{label} returned invalid JSON") from error


def load_object(path: Path, label: str) -> dict[str, Any]:
    value = parse_json(path.read_text(encoding="utf-8"), label)
    if not isinstance(value, dict):
        raise TypeError(f"{label} is not an object")
    return cast("dict[str, Any]", value)


def write_github_output(path: str | None, values: Mapping[str, str | None]) -> None:
    if path is None:
        return
    lines = [f"{key}={value if value is not None else ''}" for key, value in values.items()]
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")


def append_summary(path: str | None, lines: Sequence[str]) -> None:
    if path is None:
        return
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")


def newest_commit_before(value: datetime) -> str:
    commit = git(["rev-list", "-n", "1", f"--before={isoformat(value)}", "HEAD"]).strip()
    if commit != "":
        return commit
    roots = [line for line in git(["rev-list", "--max-parents=0", "HEAD"]).splitlines() if line]
    if not roots:
        raise RuntimeError("The repository has no commits")
    return roots[0]


def previous_publish_run(repository: str, token: str) -> dict[str, Any] | None:
    run_args = [
        "run",
        "list",
        "--repo",
        repository,
        "--workflow",
        WORKFLOW_FILE,
        "--branch",
        "main",
        "--status",
        "success",
        "--limit",
        str(PAGE_LIMIT),
        "--json",
        "databaseId,headSha,createdAt",
    ]
    runs = parse_json(gh(run_args, token), "gh run list")
    if not isinstance(runs, list):
        raise TypeError("gh run list returned an unexpected value")
    for run in runs:
        if not isinstance(run, dict):
            continue
        head_sha = run.get("headSha")
        if not isinstance(head_sha, str) or SHA.fullmatch(head_sha) is None:
            continue
        try:
            view = parse_json(
                gh(
                    [
                        "run",
                        "view",
                        str(run.get("databaseId")),
                        "--repo",
                        repository,
                        "--json",
                        "jobs",
                    ],
                    token,
                ),
                "gh run view",
            )
        except (OSError, RuntimeError):
            continue
        jobs = view.get("jobs") if isinstance(view, dict) else None
        if not isinstance(jobs, list):
            continue
        publish_job = next(
            (job for job in jobs if isinstance(job, dict) and job.get("name") == PUBLISH_JOB),
            None,
        )
        if publish_job is None or publish_job.get("conclusion") != "success":
            continue
        database_id = run.get("databaseId")
        created_at = run.get("createdAt")
        if isinstance(database_id, int) and isinstance(created_at, str):
            return {"databaseId": database_id, "headSha": head_sha, "createdAt": created_at}
    if len(runs) >= PAGE_LIMIT:
        raise RuntimeError("No completed audit found in the newest 100 successful runs")
    return None


def build_scope(scope: Scope) -> str:
    commit_log = [
        line
        for line in git(
            ["log", "--first-parent", "--pretty=format:%h %s", f"{scope.base}..{scope.head}"],
        ).splitlines()
        if line
    ][:MAX_SCOPE_COMMITS]
    stat_output = git(["diff", "--stat", scope.base, scope.head])
    stat_lines = [line for line in stat_output.splitlines() if line]
    stat = stat_lines[:MAX_SCOPE_FILES]
    baseline = (
        "24-hour lookback, no completed audit found"
        if scope.baseline_run is None
        else f"run {scope.baseline_run['databaseId']}"
    )
    pull_requests = [
        f"- [#{pull['number']}]({pull['url']}) {pull['title']} (merged {pull['mergedAt']})"
        for pull in scope.merged_pull_requests[:MAX_SCOPE_PULL_REQUESTS]
    ]
    if len(scope.merged_pull_requests) > MAX_SCOPE_PULL_REQUESTS:
        pull_requests.append(
            f"- {len(scope.merged_pull_requests) - MAX_SCOPE_PULL_REQUESTS} more pull requests",
        )
    return "\n".join(
        [
            "# Nightly dead-code audit scope",
            "",
            f"- Base commit: `{scope.base}`",
            f"- Head commit: `{scope.head}`",
            f"- Baseline: {baseline}, boundary `{isoformat(scope.boundary)}`",
            f"- Branch: `{scope.branch}`",
            "",
            "## Merged pull requests in the audit range",
            "",
            *(pull_requests or ["None."]),
            "",
            "## Commits",
            "",
            "```text",
            *(commit_log or ["No commits."]),
            "```",
            "",
            "## Changed files",
            "",
            "```text",
            *(stat or ["No changed files."]),
            "```",
            "",
        ],
    )


def baseline(args: argparse.Namespace) -> None:
    output = Path(require_value(args.output, "--output"))
    now = datetime.now(tz=UTC) if args.now is None else parse_date(require_value(args.now, "--now"))
    lookback = LOOKBACK_HOURS if args.lookback_hours is None else int(args.lookback_hours)
    if lookback <= 0:
        raise ValueError("--lookback-hours must be positive")
    output.mkdir(parents=True, exist_ok=True)

    def write_result(*, run: bool, reason: str, **extra: Any) -> None:
        preflight: dict[str, Any] = {"run": run, "reason": reason, "now": isoformat(now), **extra}
        (output / "preflight.json").write_text(
            json.dumps(preflight, indent=2) + "\n",
            encoding="utf-8",
        )
        scope = (
            build_scope(
                Scope(
                    base=extra["base"],
                    head=extra["head"],
                    branch=extra["branch"],
                    boundary=parse_date(extra["boundary"]),
                    baseline_run=extra["baselineRun"],
                    merged_pull_requests=extra["mergedPullRequests"],
                ),
            )
            if run
            else f"# Nightly dead-code audit scope\n\nThe audit did not run.\n\n{reason}\n"
        )
        (output / "scope.md").write_text(scope, encoding="utf-8")
        write_github_output(
            getattr(args, "github_output", None),
            {
                "run": str(run),
                "base": extra.get("base", ""),
                "head": extra.get("head", ""),
                "branch": extra.get("branch", ""),
            },
        )
        heading = "### Nightly dead-code audit" if run else "### Nightly dead-code audit skipped"
        append_summary(getattr(args, "summary", None), [heading, "", reason, ""])

    repository = require_value(os.environ.get("GITHUB_REPOSITORY"), "GITHUB_REPOSITORY")
    token = require_value(os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"), "GH_TOKEN")
    head = git(["rev-parse", "HEAD"])
    if SHA.fullmatch(head) is None:
        raise RuntimeError("HEAD is not a full commit SHA")

    baseline_run = previous_publish_run(repository, token)
    if baseline_run is None:
        boundary = now - timedelta(hours=lookback)
        base = newest_commit_before(boundary)
    else:
        boundary = parse_date(str(baseline_run["createdAt"]))
        base = str(baseline_run["headSha"])

    if git_status(["merge-base", "--is-ancestor", base, head]) != 0:
        merge_base = git(["merge-base", base, head]).strip()
        if merge_base == "":
            boundary = now - timedelta(hours=lookback)
            base = newest_commit_before(boundary)
        else:
            base = merge_base

    open_pulls = parse_json(
        gh(
            [
                "pr",
                "list",
                "--repo",
                repository,
                "--state",
                "open",
                "--limit",
                "100",
                "--json",
                "number,url,isDraft,headRefName,isCrossRepository",
            ],
            token,
        ),
        "gh pr list",
    )
    if not isinstance(open_pulls, list):
        raise TypeError("gh pr list returned an unexpected value")
    open_nightly = next(
        (
            pull
            for pull in open_pulls
            if isinstance(pull, dict)
            and pull.get("isCrossRepository") is not True
            and isinstance(pull.get("headRefName"), str)
            and pull["headRefName"].startswith(BRANCH_PREFIX)
        ),
        None,
    )
    if open_nightly is not None:
        write_result(
            run=False,
            reason=f"Open nightly pull request #{open_nightly['number']} is still open.",
            base=base,
            head=head,
        )
        return

    merged_pulls = parse_json(
        gh(
            [
                "pr",
                "list",
                "--repo",
                repository,
                "--state",
                "merged",
                "--limit",
                "200",
                "--search",
                f"merged:>={isoformat(boundary)}",
                "--json",
                "number,title,url,mergedAt,mergeCommit",
            ],
            token,
        ),
        "gh pr list",
    )
    if not isinstance(merged_pulls, list):
        raise TypeError("gh pr list returned an unexpected value")
    commits = set(git(["rev-list", f"{base}..{head}"]).splitlines())
    merged_pull_requests: list[dict[str, Any]] = []
    for pull in merged_pulls:
        if not isinstance(pull, dict) or not isinstance(pull.get("mergedAt"), str):
            continue
        try:
            merged_at = parse_date(pull["mergedAt"])
        except ValueError:
            continue
        if merged_at.timestamp() < boundary.timestamp():
            continue
        merge_commit = pull.get("mergeCommit")
        oid = merge_commit.get("oid") if isinstance(merge_commit, dict) else None
        if isinstance(oid, str) and oid not in commits:
            continue
        merged_pull_requests.append(pull)
    if not merged_pull_requests:
        write_result(
            run=False,
            reason=f"No pull requests merged since {isoformat(boundary)}.",
            base=base,
            head=head,
        )
        return

    if git_status(["diff", "--quiet", base, head]) == 0:
        write_result(
            run=False,
            reason=f"No net change between {base} and {head}.",
            base=base,
            head=head,
        )
        return

    branch = f"{BRANCH_PREFIX}{date_string(now)}"
    write_result(
        run=True,
        reason=(
            f"{len(merged_pull_requests)} merged pull request(s) changed the tree "
            f"between {base} and {head}."
        ),
        base=base,
        head=head,
        branch=branch,
        date=date_string(now),
        boundary=isoformat(boundary),
        baselineRun=baseline_run,
        mergedPullRequests=merged_pull_requests,
    )


def verify(args: argparse.Namespace) -> None:
    expected_head = require_value(args.head, "--head")
    output = Path(require_value(args.output, "--output"))
    report_path = getattr(args, "report", None)

    head = git(["rev-parse", "HEAD"])
    if head != expected_head:
        raise RuntimeError(f"HEAD is {head}, expected {expected_head}")

    git(["add", "-A", "-N"])
    status = git(["status", "--porcelain"])
    changed = status.strip() != ""
    paths = [
        entry
        for entry in git(["diff", "--name-only", "-z", expected_head, "--"]).split("\0")
        if entry
    ]

    additions = 0
    deletions = 0
    for line in git(["diff", "--numstat", expected_head, "--"]).splitlines():
        if line.strip() == "":
            continue
        fields = line.split("\t")
        if len(fields) < NUMSTAT_FIELDS or fields[0] == "-" or fields[1] == "-":
            continue
        additions += int(fields[0])
        deletions += int(fields[1])

    report = ""
    if report_path is not None:
        try:
            report = Path(report_path).read_text(encoding="utf-8")
        except FileNotFoundError:
            report = ""
    if changed and report.strip() == "":
        raise RuntimeError("The audit changed files without a non-empty report")

    validation = {
        "changed": changed,
        "files": len(paths),
        "additions": additions,
        "deletions": deletions,
        "paths": paths,
        "report": report_path,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(validation, indent=2) + "\n", encoding="utf-8")
    write_github_output(
        getattr(args, "github_output", None),
        {
            "changed": str(changed),
            "files": str(len(paths)),
            "additions": str(additions),
            "deletions": str(deletions),
        },
    )
    append_summary(
        getattr(args, "summary", None),
        [
            (
                f"### Dead-code patch: {len(paths)} file(s), +{additions}/-{deletions}"
                if changed
                else "### No dead-code changes"
            ),
            "",
            "\n".join(paths) if changed else "The model removed nothing.",
            "",
        ],
    )


def build_pull_request_body(
    preflight: dict[str, Any],
    validation: dict[str, Any],
    report: str,
) -> str:
    server = os.environ.get("GITHUB_SERVER_URL")
    run_id = os.environ.get("GITHUB_RUN_ID")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    run_url = (
        None if server is None or run_id is None else f"{server}/{repository}/actions/runs/{run_id}"
    )
    merged = preflight.get("mergedPullRequests")
    pulls: list[str] = []
    if isinstance(merged, list):
        pulls = [
            f"- [#{pull['number']}]({pull['url']}) {pull['title']}"
            for pull in merged[:MAX_BODY_PULL_REQUESTS]
            if isinstance(pull, dict)
        ]
        if len(merged) > MAX_BODY_PULL_REQUESTS:
            pulls.append(f"- {len(merged) - MAX_BODY_PULL_REQUESTS} more pull requests")
    lines = [
        "<!-- nightly-dead-code -->",
        (
            "Draft cleanup generated by the nightly dead-code audit."
            if run_url is None
            else f"Draft cleanup generated by the [nightly dead-code audit]({run_url})."
        ),
        "",
        f"Audit range: `{preflight['base']}..{preflight['head']}`",
        f"Merged pull requests: {len(merged) if isinstance(merged, list) else 0}",
        (
            f"Patch: {validation['files']} file(s), "
            f"+{validation['additions']}/-{validation['deletions']}."
        ),
        "",
    ]
    if pulls:
        lines.extend(["### Merged pull requests", "", *pulls, ""])
    footer = "_This draft is a suggestion. Review every removal. The workflow never merges it._"
    lines.extend(["### Findings", "", report.strip(), "", footer])
    body = "\n".join(lines)
    if len(body) > MAX_BODY_LENGTH:
        body = f"{body[:MAX_BODY_LENGTH]}\n\n_Report truncated._"
    return body


def publish(args: argparse.Namespace) -> None:
    preflight = load_object(Path(require_value(args.preflight, "--preflight")), "preflight")
    validation = load_object(Path(require_value(args.validation, "--validation")), "validation")
    output = Path(require_value(args.output, "--output"))
    output.parent.mkdir(parents=True, exist_ok=True)

    if validation.get("changed") is not True:
        output.write_text(
            json.dumps({"published": False, "reason": "No dead code found."}, indent=2) + "\n",
            encoding="utf-8",
        )
        append_summary(
            getattr(args, "summary", None),
            ["### No draft pull request", "", "The audit found no removable artifacts.", ""],
        )
        return
    if preflight.get("run") is not True:
        raise RuntimeError("The preflight did not authorize an audit")
    branch_value = preflight.get("branch")
    branch = require_value(
        branch_value if isinstance(branch_value, str) else None,
        "preflight.branch",
    )
    if not branch.startswith(BRANCH_PREFIX):
        raise RuntimeError(f"Invalid nightly branch {branch}")
    report = Path(require_value(args.report, "--report")).read_text(encoding="utf-8")
    if report.strip() == "":
        raise RuntimeError("The removal report is empty")
    repository = require_value(os.environ.get("GITHUB_REPOSITORY"), "GITHUB_REPOSITORY")
    token = require_value(os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"), "GH_TOKEN")
    body_path = Path(require_value(args.body, "--body"))
    body = build_pull_request_body(preflight, validation, report)
    body_path.write_text(body + "\n", encoding="utf-8")

    git(["config", "user.name", "github-actions[bot]"])
    git(["config", "user.email", "github-actions[bot]@users.noreply.github.com"])
    git(["switch", "-C", branch])
    git(["add", "-A"])
    git(
        [
            "commit",
            "-m",
            (
                "Remove dead code and obsolete artifacts "
                f"({preflight['base'][:12]}..{preflight['head'][:12]})"
            ),
        ],
    )
    gh(["auth", "setup-git"], token)
    git(["push", "--force", "origin", f"HEAD:refs/heads/{branch}"])
    date = preflight.get("date")
    title = (
        f"Nightly dead-code removal: {date}"
        if isinstance(date, str)
        else f"Nightly dead-code removal: {date_string(datetime.now(tz=UTC))}"
    )
    url = gh(
        [
            "pr",
            "create",
            "--draft",
            "--repo",
            repository,
            "--base",
            "main",
            "--head",
            branch,
            "--title",
            title,
            "--body-file",
            str(body_path),
        ],
        token,
    )
    output.write_text(
        json.dumps(
            {"published": True, "branch": branch, "url": url, "title": title},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    append_summary(getattr(args, "summary", None), ["### Draft pull request", "", url, ""])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dead-code-nightly.py")
    subparsers = parser.add_subparsers(dest="command", required=True)

    baseline_parser = subparsers.add_parser("baseline")
    baseline_parser.add_argument("--output", required=True)
    baseline_parser.add_argument("--github-output")
    baseline_parser.add_argument("--summary")
    baseline_parser.add_argument("--lookback-hours")
    baseline_parser.add_argument("--now")

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--head", required=True)
    verify_parser.add_argument("--report")
    verify_parser.add_argument("--output", required=True)
    verify_parser.add_argument("--github-output")
    verify_parser.add_argument("--summary")

    publish_parser = subparsers.add_parser("publish")
    publish_parser.add_argument("--preflight", required=True)
    publish_parser.add_argument("--validation", required=True)
    publish_parser.add_argument("--report", required=True)
    publish_parser.add_argument("--body", required=True)
    publish_parser.add_argument("--output", required=True)
    publish_parser.add_argument("--summary")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "baseline":
        baseline(args)
    elif args.command == "verify":
        verify(args)
    elif args.command == "publish":
        publish(args)
    else:
        parser.error("Expected baseline, verify or publish")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, TypeError, ValueError, OSError) as error:
        print(f"dead-code-nightly: {error}", file=sys.stderr)
        raise SystemExit(1) from error

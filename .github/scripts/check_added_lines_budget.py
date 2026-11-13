#!/usr/bin/env python3
"""Enforce a reviewable added-lines budget for pull requests."""

from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

DEFAULT_LIMIT: Final[int] = 1000
DEFAULT_EXCLUDES: Final[tuple[str, ...]] = (
    "tests/**",
    "**/tests/**",
    "**/__tests__/**",
    "**/*.test.*",
    "**/*.spec.*",
    "*.lock",
    "*package-lock.json",
    "*pnpm-lock.yaml",
    "*yarn.lock",
    "*go.sum",
)
SHA_PATTERN = re.compile(r"[a-f0-9]{40}")
NUMSTAT_FIELDS = 3


@dataclass(frozen=True)
class FileAddition:
    path: str
    added: int


@dataclass(frozen=True)
class Exemption:
    sha: str
    reason: str


def run_git(args: Sequence[str]) -> str:
    result = subprocess.run(["git", *args], capture_output=True, check=False, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"git {' '.join(args)} failed{suffix}")
    return result.stdout


def rev_parse(ref: str) -> str:
    return run_git(["rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"]).strip()


def resolve_range(base: str, head: str) -> tuple[str, str, str]:
    base_sha = rev_parse(base)
    head_sha = rev_parse(head)
    merge_base = run_git(["merge-base", base_sha, head_sha]).strip()
    if not merge_base:
        raise RuntimeError(f"no merge base between {base} and {head}")
    return base_sha, head_sha, merge_base


def range_commits(merge_base: str, head: str) -> set[str]:
    return {line for line in run_git(["rev-list", f"{merge_base}..{head}"]).splitlines() if line}


def normalized_destination(path: str) -> str:
    if " => " not in path:
        return path
    prefix, separator, suffix = path.partition("{")
    if not separator:
        return path.split(" => ", 1)[1]
    renamed, separator, remainder = suffix.partition("}")
    if not separator:
        return path
    return f"{prefix}{renamed.split(' => ', 1)[1]}{remainder}"


def is_excluded(path: str, patterns: Sequence[str]) -> bool:
    normalized = PurePosixPath(normalized_destination(path)).as_posix()
    return any(fnmatch.fnmatchcase(normalized, pattern) for pattern in patterns)


def parse_exemptions(path: Path) -> tuple[Exemption, ...]:
    if not path.exists():
        raise RuntimeError(f"exemption file not found: {path}")
    entries: list[Exemption] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        sha = parts[0]
        reason = " ".join(parts[1:]).strip()
        if SHA_PATTERN.fullmatch(sha) is None:
            raise RuntimeError(f"invalid exemption entry (expected a full commit SHA): {line}")
        if not reason:
            raise RuntimeError(f"invalid exemption entry (missing reason): {line}")
        if sha in seen:
            raise RuntimeError(f"duplicate exemption entry: {sha}")
        seen.add(sha)
        entries.append(Exemption(sha=sha, reason=reason))
    return tuple(entries)


def find_range_exemption(entries: Sequence[Exemption], commits: set[str]) -> Exemption | None:
    for entry in entries:
        if entry.sha in commits:
            return entry
    return None


def changed_additions(
    merge_base: str,
    head: str,
    excludes: Sequence[str],
    *,
    tolerate_binary: bool,
) -> list[FileAddition]:
    output = run_git(["diff", "--find-renames", "--numstat", f"{merge_base}..{head}"])
    additions: list[FileAddition] = []
    for line in output.splitlines():
        if not line:
            continue
        parts = line.split("\t", 2)
        if len(parts) != NUMSTAT_FIELDS:
            raise RuntimeError(f"invalid git numstat record: {line}")
        added, removed, path = parts
        if added == "-" or removed == "-":
            if tolerate_binary:
                continue
            raise RuntimeError(f"binary change has no measurable line budget: {path}")
        if is_excluded(path, excludes):
            continue
        try:
            added_lines = int(added)
        except ValueError as error:
            raise RuntimeError(f"invalid git addition count: {added}") from error
        additions.append(FileAddition(path=path, added=added_lines))
    return additions


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Git ref to compare against")
    parser.add_argument("--head", default="HEAD", help="Git ref under review")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument(
        "--exclude",
        action="append",
        default=None,
        help="Additional fnmatch-style path pattern to exclude",
    )
    parser.add_argument("--exemptions", type=Path, help="Reviewed exemption file")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.limit <= 0:
        print("added-lines-budget: --limit must be positive", file=sys.stderr)
        return 2

    try:
        _, head_sha, merge_base = resolve_range(args.base, args.head)
        commits = range_commits(merge_base, head_sha)
        entries = parse_exemptions(args.exemptions) if args.exemptions is not None else ()
        exempt = find_range_exemption(entries, commits)
        excludes = (*DEFAULT_EXCLUDES, *(args.exclude or []))
        additions = changed_additions(
            merge_base,
            head_sha,
            excludes,
            tolerate_binary=exempt is not None,
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"added-lines-budget: {error}", file=sys.stderr)
        return 2

    total = sum(item.added for item in additions)
    if exempt is not None:
        status = "EXEMPT"
    elif total <= args.limit:
        status = "PASS"
    else:
        status = "FAIL"
    detail = f"added-lines-budget: {status} {total}/{args.limit} non-test non-lock added lines"
    if exempt is not None:
        detail += f" ({exempt.sha}: {exempt.reason})"
    print(detail)

    if status == "FAIL":
        print("Top added files:", file=sys.stderr)
        for item in sorted(additions, key=lambda value: value.added, reverse=True)[:20]:
            print(f"  {item.added:>5} {item.path}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

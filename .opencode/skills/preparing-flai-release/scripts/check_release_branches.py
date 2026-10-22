#!/usr/bin/env python3
"""Report whether every FLAI release branch is merged into the release branch.

Read-only. Run from the repository root.

Exit codes:
    0  every matching branch is merged
    1  at least one branch is unmerged
    2  usage or git error
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class BranchStatus:
    branch: str
    location: str
    merged: bool
    commits: int
    head: str
    subject: str


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def is_ancestor(ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def local_branches() -> set[str]:
    output = git("for-each-ref", "--format=%(refname:short)", "refs/heads")
    return {line.strip() for line in output.splitlines() if line.strip()}


def remote_branches(remote: str) -> dict[str, str]:
    output = git("for-each-ref", "--format=%(refname:short)", f"refs/remotes/{remote}")
    branches: dict[str, str] = {}
    prefix = f"{remote}/"
    for line in output.splitlines():
        ref = line.strip()
        if not ref or ref.endswith("/HEAD") or not ref.startswith(prefix):
            continue
        branches[ref[len(prefix) :]] = ref
    return branches


def resolve_release(release: str, remote: str) -> str:
    if release in local_branches():
        return release
    remotes = remote_branches(remote)
    if release in remotes:
        return remotes[release]
    raise RuntimeError(f"release branch not found locally or on {remote}: {release}")


def collect(release: str, remote: str, prefixes: tuple[str, ...]) -> tuple[str, list[BranchStatus]]:
    release_ref = resolve_release(release, remote)
    prefix_pattern = "|".join(re.escape(prefix) for prefix in prefixes)
    pattern = re.compile(rf"^(?:{prefix_pattern})/{re.escape(release)}(?:-|$)")
    locals_ = local_branches()
    remotes = remote_branches(remote)
    names = sorted({name for name in [*locals_, *remotes] if pattern.match(name)})

    statuses: list[BranchStatus] = []
    for name in names:
        if name in locals_:
            ref, location = name, "local"
        else:
            ref, location = remotes[name], "remote"
        merged = is_ancestor(ref, release_ref)
        commits = 0 if merged else int(git("rev-list", "--count", f"{release_ref}..{ref}") or 0)
        statuses.append(
            BranchStatus(
                branch=name,
                location=location,
                merged=merged,
                commits=commits,
                head=git("rev-parse", "--short", ref),
                subject=git("log", "-1", "--format=%s", ref),
            )
        )

    statuses.sort(key=lambda status: (status.merged, status.branch))
    return release_ref, statuses


def print_table(release: str, previous: str | None, release_ref: str, statuses: list[BranchStatus]) -> None:
    print(f"Release: {release} ({release_ref})")
    if previous:
        try:
            count = int(git("rev-list", "--count", f"{previous}..{release_ref}") or 0)
            print(f"Previous: {previous} ({count} commits in the release range)")
        except RuntimeError as exc:
            print(f"Previous: {previous} (not found: {exc})")
    if not statuses:
        print(f"No feature/fix branches found for {release}.")
        return

    width = max(len(status.branch) for status in statuses)
    print(f"{'Branch':<{width}}  {'State':<8}  {'Commits':>7}  {'Location':<7}  Head     Subject")
    for status in statuses:
        state = "merged" if status.merged else "UNMERGED"
        print(
            f"{status.branch:<{width}}  {state:<8}  {status.commits:>7}  "
            f"{status.location:<7}  {status.head:<8} {status.subject}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True, help="release branch, for example v12.6")
    parser.add_argument("--previous", help="previous release branch, for example v12.5")
    parser.add_argument("--remote", default="origin", help="remote used for remote-only branches (default: origin)")
    parser.add_argument(
        "--prefixes",
        default="features,fix",
        help="comma-separated branch prefixes (default: features,fix)",
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args()

    prefixes = tuple(part.strip() for part in args.prefixes.split(",") if part.strip())
    if not prefixes:
        print("error: --prefixes must contain at least one prefix", file=sys.stderr)
        return 2

    try:
        git("rev-parse", "--is-inside-work-tree")
        release_ref, statuses = collect(args.release, args.remote, prefixes)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                {
                    "release": args.release,
                    "release_ref": release_ref,
                    "previous": args.previous,
                    "branches": [asdict(status) for status in statuses],
                },
                indent=2,
            )
        )
    else:
        print_table(args.release, args.previous, release_ref, statuses)

    return 0 if all(status.merged for status in statuses) else 1


if __name__ == "__main__":
    raise SystemExit(main())

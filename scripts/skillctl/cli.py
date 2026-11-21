"""Command-line interface for skillctl."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path
from typing import Sequence

from .config import VALID_UPSTREAM_MODES, ManifestStore
from .frontmatter import SkillDiscovery
from .github import GitHubCliTransport, UpstreamTransportError
from .syncer import SkillSyncer, StatusReporter
from .upstreams import AddResult, CheckItem, UpdateItem, UpstreamManager
from .validator import RepoValidator
from .verifiers import (
    AntigravityVerifier,
    CodexVerifier,
    HarnessVerifier,
    OpenCodeVerifier,
    StaticLeakVerifier,
    VerificationRunner,
    VerifyContext,
)


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _parse_enable_targets(raw_values: list[str] | None) -> list[str]:
    targets: list[str] = []
    seen: set[str] = set()
    for item in raw_values or []:
        for part in item.split(","):
            cleaned = part.strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                targets.append(cleaned)
    return targets


def _format_check_human(items: list[CheckItem]) -> str:
    if not items:
        return "No upstream sources matched."
    lines: list[str] = []
    for item in items:
        pinned = item.pinned_commit[:12] if item.pinned_commit else "unpinned"
        head = item.head_commit[:12] if item.head_commit else "unknown"
        lines.append(
            f"[{item.status}] {item.source_id} ({item.repo}@{item.ref}, mode={item.mode}): "
            f"{pinned} -> {head}"
        )
        if item.changed_skills:
            lines.append(f"  changed skills: {', '.join(item.changed_skills)}")
        if item.new_upstream_skills:
            lines.append(f"  new upstream skills: {', '.join(item.new_upstream_skills)}")
        for commit in item.commits[:10]:
            lines.append(f"  commit {commit.sha}: {commit.message}")
        for note in item.notes:
            lines.append(f"  note: {note}")
        if item.error:
            lines.append(f"  error: {item.error}")
    return "\n".join(lines)


def _format_update_human(items: list[UpdateItem]) -> str:
    if not items:
        return "No upstream sources matched."
    lines: list[str] = []
    for item in items:
        old_sha = item.old_commit[:12] if item.old_commit else "unpinned"
        new_sha = item.new_commit[:12] if item.new_commit else "unknown"
        lines.append(
            f"[{item.action}] {item.source_id} ({item.repo}, mode={item.mode}): "
            f"{old_sha} -> {new_sha}"
        )
        if item.updated_skills:
            lines.append(f"  skills: {', '.join(item.updated_skills)}")
        if item.merged_files:
            lines.append(f"  merged files: {', '.join(item.merged_files)}")
        if item.conflict_files:
            lines.append(f"  conflict files: {', '.join(item.conflict_files)}")
        if item.new_upstream_skills:
            lines.append(f"  new upstream skills: {', '.join(item.new_upstream_skills)}")
        for note in item.notes:
            lines.append(f"  note: {note}")
        if item.error:
            lines.append(f"  error: {item.error}")
        if item.diff.strip():
            lines.append(item.diff.rstrip())
    return "\n".join(lines)


def _format_add_human(result: AddResult, *, discovery_only: bool) -> str:
    lines: list[str] = []
    if discovery_only:
        lines.append(
            f"Discovered {len(result.discovered_paths)} skill(s) in "
            f"{result.repo}@{result.ref} ({result.commit[:12]}):"
        )
        for path in result.discovered_paths:
            lines.append(f"  - {path or '.'}")
        return "\n".join(lines)

    prefix = "[DRY-RUN] " if result.dry_run else ""
    lines.append(
        f"{prefix}Added {len(result.added_skills)} skill(s) from "
        f"{result.repo}@{result.ref} ({result.commit[:12]}) "
        f"into {result.group} (source={result.source_id}, mode={result.mode}):"
    )
    for skill in result.added_skills:
        lines.append(f"  - {skill}")
    if result.license_copied:
        lines.append(f"  license: {result.license_copied}")
    if not result.dry_run and not result.validation_errors:
        lines.append("[PASS] Validation OK")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Manage skill symlinks, verify harness discovery, and track upstreams."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="Sync skill symlinks to target directories")
    p_sync.add_argument(
        "--dry-run", action="store_true", help="Print actions without modifying filesystem"
    )
    p_sync.add_argument(
        "--allow-worktree",
        action="store_true",
        help="Allow live sync from linked worktree or non-canonical root",
    )
    p_sync.add_argument(
        "--no-reload", action="store_true", help="Skip `opencode reload` after changes"
    )
    p_sync.add_argument("--root", type=Path, default=None, help="Override repository root")

    p_status = sub.add_parser("status", help="Show skill matrix and target symlink status")
    p_status.add_argument("--root", type=Path, default=None, help="Override repository root")

    p_verify = sub.add_parser(
        "verify", help="Verify static symlinks, leaks, and live harness discovery"
    )
    p_verify.add_argument(
        "--live-agy", action="store_true", help="Also run live Antigravity CLI check"
    )
    p_verify.add_argument("--root", type=Path, default=None, help="Override repository root")

    p_validate = sub.add_parser(
        "validate", help="Validate skills.toml, SKILL.md frontmatter, and upstreams.toml"
    )
    p_validate.add_argument("--root", type=Path, default=None, help="Override repository root")

    p_upstream = sub.add_parser("upstream", help="Check, update, and import upstream skills")
    up_sub = p_upstream.add_subparsers(dest="upstream_command", required=True)

    p_up_check = up_sub.add_parser("check", help="Check upstream sources for updates")
    p_up_check.add_argument("source", nargs="?", default=None, help="Optional source ID")
    p_up_check.add_argument("--skill", default=None, help="Filter to a single skill name")
    p_up_check.add_argument("--json", action="store_true", help="Emit JSON output")
    p_up_check.add_argument("--root", type=Path, default=None, help="Override repository root")

    p_up_update = up_sub.add_parser("update", help="Apply or preview upstream updates")
    p_up_update.add_argument("source", nargs="?", default=None, help="Optional source ID")
    p_up_update.add_argument("--skill", default=None, help="Filter to a single skill name")
    p_up_update.add_argument(
        "--dry-run", action="store_true", help="Preview updates without writing repo files"
    )
    p_up_update.add_argument(
        "--merge",
        action="store_true",
        help="Perform 3-way merge via git merge-file for adapted sources",
    )
    p_up_update.add_argument("--json", action="store_true", help="Emit JSON output")
    p_up_update.add_argument("--root", type=Path, default=None, help="Override repository root")

    p_up_add = up_sub.add_parser("add", help="Discover or import skills from a GitHub repo")
    p_up_add.add_argument("repo", help="GitHub repository slug (owner/repo)")
    p_up_add.add_argument("--ref", default=None, help="Git ref/branch/tag (defaults to repo default branch)")
    p_up_add.add_argument(
        "--path",
        action="extend",
        nargs="+",
        default=[],
        help="Upstream skill path(s) to import",
    )
    p_up_add.add_argument(
        "--all",
        dest="all_skills",
        action="store_true",
        help="Import all discovered skills",
    )
    p_up_add.add_argument(
        "--source-id",
        "--source",
        dest="source_id",
        default=None,
        help="Override upstream source ID",
    )
    p_up_add.add_argument("--group", default=None, help="Override destination group under skills/")
    p_up_add.add_argument(
        "--mode",
        choices=VALID_UPSTREAM_MODES,
        default="mirror",
        help="Upstream tracking mode (default: mirror)",
    )
    p_up_add.add_argument(
        "--enable",
        action="append",
        default=[],
        help="Target column(s) to enable (comma-separated or repeated)",
    )
    p_up_add.add_argument(
        "--watch-root",
        action="extend",
        nargs="+",
        default=[],
        help="Upstream directory root(s) to watch for new skills",
    )
    p_up_add.add_argument(
        "--license-dest",
        default=None,
        help="Repository-relative destination path for copied upstream license",
    )
    p_up_add.add_argument(
        "--dry-run", action="store_true", help="Preview import without writing files"
    )
    p_up_add.add_argument("--root", type=Path, default=None, help="Override repository root")

    return parser


def _cmd_sync(repo_root: Path, args: argparse.Namespace, root_overridden: bool) -> int:
    try:
        manifest = ManifestStore.load_skills_manifest(repo_root)
    except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
        print(f"ERROR: Failed to load skills.toml: {exc}", file=sys.stderr)
        return 1

    return SkillSyncer(repo_root, manifest).sync(
        dry_run=args.dry_run,
        allow_worktree=args.allow_worktree,
        no_reload=args.no_reload,
        root_overridden=root_overridden,
    )


def _cmd_status(repo_root: Path) -> int:
    rc, text = StatusReporter.render(repo_root)
    stream = sys.stdout if rc == 0 else sys.stderr
    print(text, file=stream)
    return rc


def _cmd_verify(repo_root: Path, args: argparse.Namespace) -> int:
    try:
        manifest = ManifestStore.load_skills_manifest(repo_root)
    except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
        print(f"[FAIL] Cannot load skills.toml: {exc}")
        return 1

    disk_skills = {s.name: s for s in SkillDiscovery.discover(repo_root)}
    ctx = VerifyContext(
        repo_root=repo_root,
        manifest=manifest,
        disk_skills=disk_skills,
        home=Path.home(),
    )
    verifiers: list[HarnessVerifier] = [
        StaticLeakVerifier(),
        CodexVerifier(),
        OpenCodeVerifier(),
    ]
    if args.live_agy:
        verifiers.append(AntigravityVerifier())
    return VerificationRunner(verifiers).run(ctx)


def _cmd_validate(repo_root: Path) -> int:
    errors = RepoValidator.validate_all(repo_root)
    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1
    print("[PASS] Validation OK")
    return 0


def _cmd_upstream(repo_root: Path, args: argparse.Namespace) -> int:
    manager = UpstreamManager(repo_root, GitHubCliTransport())
    try:
        if args.upstream_command == "check":
            items = manager.check(args.source, args.skill)
            if args.json:
                print(json.dumps({"items": [item.to_dict() for item in items]}, indent=2))
            else:
                print(_format_check_human(items))
            return 1 if any(item.error or item.status == "error" for item in items) else 0

        if args.upstream_command == "update":
            items = manager.update(
                args.source,
                args.skill,
                dry_run=args.dry_run,
                merge=args.merge,
            )
            if args.json:
                print(json.dumps({"items": [item.to_dict() for item in items]}, indent=2))
            else:
                print(_format_update_human(items))
            has_failure = any(
                item.error
                or item.action in ("error", "merge_conflicts")
                or bool(item.conflict_files)
                for item in items
            )
            return 1 if has_failure else 0

        if args.upstream_command == "add":
            enable_targets = _parse_enable_targets(args.enable)
            discovery_only = not args.path and not args.all_skills
            result = manager.add(
                args.repo,
                ref=args.ref,
                paths=args.path or None,
                all_skills=args.all_skills,
                source_id=args.source_id,
                group=args.group,
                mode=args.mode,
                enable=enable_targets or None,
                watch_roots=args.watch_root or None,
                license_dest=args.license_dest,
                dry_run=args.dry_run,
            )
            print(_format_add_human(result, discovery_only=discovery_only))
            if result.validation_errors:
                for err in result.validation_errors:
                    print(f"ERROR: {err}", file=sys.stderr)
                return 1
            return 0
    except (FileNotFoundError, UpstreamTransportError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 1


def main(
    argv: Sequence[str] | None = None,
    *,
    default_root: Path | None = None,
) -> int:
    """CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    root_overridden = getattr(args, "root", None) is not None
    fallback_root = default_root if default_root is not None else _default_repo_root()
    repo_root = args.root.expanduser().resolve() if root_overridden else fallback_root

    if args.command == "sync":
        return _cmd_sync(repo_root, args, root_overridden)
    if args.command == "status":
        return _cmd_status(repo_root)
    if args.command == "verify":
        return _cmd_verify(repo_root, args)
    if args.command == "validate":
        return _cmd_validate(repo_root)
    if args.command == "upstream":
        return _cmd_upstream(repo_root, args)
    return 1

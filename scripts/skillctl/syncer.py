"""Symlink ownership checks, target syncing, and status reporting."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

from .config import Manifest, ManifestStore, PolicyConfig
from .validator import RepoValidator


def _path_is_inside(candidate: Path, parent: Path) -> bool:
    try:
        return candidate != parent and candidate.is_relative_to(parent)
    except ValueError:
        return False


def _path_matches_or_inside(candidate: Path, parent: Path) -> bool:
    try:
        return candidate == parent or candidate.is_relative_to(parent)
    except ValueError:
        return False


class PathOwnership:
    """Path ownership, worktree guard, and unmanaged entry classification helpers."""

    @staticmethod
    def is_linked_worktree(repo_root: Path) -> bool:
        """Return True when `<repo_root>/.git` is a file (linked git worktree)."""
        return (repo_root / ".git").is_file()

    @staticmethod
    def is_repo_owned(entry: Path, roots: list[Path]) -> bool:
        """Return True only if entry is a symlink whose target sits inside `<root>/skills`."""
        if not entry.is_symlink():
            return False
        try:
            raw_target = os.readlink(entry)
        except OSError:
            return False

        joined = Path(os.path.join(os.fspath(entry.parent), raw_target))
        candidates = [Path(os.path.abspath(os.fspath(joined)))]
        try:
            candidates.append(joined.resolve(strict=False))
        except OSError:
            pass

        skill_roots: list[Path] = []
        for root in roots:
            sr = root / "skills"
            skill_roots.append(Path(os.path.abspath(os.fspath(sr))))
            try:
                skill_roots.append(sr.resolve(strict=False))
            except OSError:
                pass

        return any(_path_is_inside(cand, sr) for cand in candidates for sr in skill_roots)

    @staticmethod
    def exact_link_matches(dest: Path, source: Path) -> bool:
        """Return True when `dest` is a symlink pointing directly to `source`."""
        if not dest.is_symlink():
            return False
        try:
            raw_target = os.readlink(dest)
        except OSError:
            return False
        abs_target = os.path.abspath(os.path.join(os.fspath(dest.parent), raw_target))
        abs_source = os.path.abspath(os.fspath(source))
        if abs_target != abs_source:
            return False
        try:
            return dest.exists() and dest.is_dir() and dest.resolve() == source.resolve()
        except OSError:
            return False

    @staticmethod
    def classify_unmanaged(entry: Path, policy: PolicyConfig) -> str:
        """Classify a non-repo entry as system, orca, omarchy, or unmanaged."""
        if entry.name in policy.system_entries:
            return "system"
        if entry.name in policy.orca_skills:
            return "orca"
        if entry.is_symlink():
            try:
                raw = os.readlink(entry)
                joined = Path(os.path.abspath(os.path.join(os.fspath(entry.parent), raw)))
                resolved = joined.resolve(strict=False)
                for prefix in policy.omarchy_prefixes:
                    if _path_matches_or_inside(joined, prefix) or _path_matches_or_inside(
                        resolved, prefix
                    ):
                        return "omarchy"
            except OSError:
                pass
        return "unmanaged"

    @staticmethod
    def roots_for_manifest(repo_root: Path, manifest: Manifest) -> list[Path]:
        """Return repository roots whose `skills/` entries count as repo-owned."""
        roots = [repo_root]
        if manifest.canonical_root is not None:
            roots.append(manifest.canonical_root)
        return roots

    @staticmethod
    def check_sync_root_guard(
        repo_root: Path,
        canonical_root: Path | None,
        *,
        dry_run: bool,
        allow_worktree: bool,
        root_overridden: bool,
    ) -> str | None:
        """Return error message if sync is blocked by worktree/canonical_root guard."""
        if dry_run or allow_worktree or root_overridden:
            return None
        if PathOwnership.is_linked_worktree(repo_root):
            return (
                f"Refusing live sync from linked git worktree: {repo_root} "
                "(pass --allow-worktree or --dry-run to override)"
            )
        if canonical_root is not None and repo_root.resolve() != canonical_root.resolve():
            return (
                f"Refusing live sync from non-canonical root {repo_root.resolve()} "
                f"(expected {canonical_root.resolve()}; pass --allow-worktree or --root to override)"
            )
        return None


class SkillSyncer:
    """Synchronize declared skill symlinks into target harness directories."""

    def __init__(
        self,
        repo_root: Path,
        manifest: Manifest,
        *,
        reloader: Callable[[], str | None] | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.manifest = manifest
        self.reloader = reloader

    def _reload_opencode(self) -> None:
        if self.reloader is not None:
            err = self.reloader()
            if err:
                print(f"[WARN] opencode reload failed: {err}", file=sys.stderr)
            return

        if shutil.which("opencode"):
            try:
                proc = subprocess.run(
                    ["opencode", "reload"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if proc.returncode != 0:
                    msg = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
                    print(f"[WARN] opencode reload failed: {msg}", file=sys.stderr)
            except (OSError, subprocess.SubprocessError) as exc:
                print(f"[WARN] opencode reload failed: {exc}", file=sys.stderr)

    def sync(
        self,
        *,
        dry_run: bool = False,
        allow_worktree: bool = False,
        no_reload: bool = False,
        root_overridden: bool = False,
    ) -> int:
        """Sync enabled skills as symlinks into target directories."""
        guard_err = PathOwnership.check_sync_root_guard(
            self.repo_root,
            self.manifest.canonical_root,
            dry_run=dry_run,
            allow_worktree=allow_worktree,
            root_overridden=root_overridden,
        )
        if guard_err:
            print(f"ERROR: {guard_err}", file=sys.stderr)
            return 1

        errors = RepoValidator.validate_skills(self.repo_root)
        if errors:
            for err in errors:
                print(f"ERROR: {err}", file=sys.stderr)
            return 1

        roots = PathOwnership.roots_for_manifest(self.repo_root, self.manifest)
        had_errors = False
        opencode_changed = False

        for col_idx, target_name in enumerate(self.manifest.columns):
            target_dir = self.manifest.targets[target_name]
            if target_dir.is_symlink():
                print(
                    f"ERROR [{target_name}] target path is a symlink (refusing to traverse): {target_dir}",
                    file=sys.stderr,
                )
                had_errors = True
                continue
            if target_dir.exists() and not target_dir.is_dir():
                print(
                    f"ERROR [{target_name}] target path exists and is not a directory: {target_dir}",
                    file=sys.stderr,
                )
                had_errors = True
                continue

            if not dry_run:
                target_dir.mkdir(parents=True, exist_ok=True)

            enabled: dict[str, Path] = {}
            for skill_name, (group, row) in self.manifest.declared.items():
                if row[col_idx] == 1:
                    src = (
                        self.repo_root / "skills" / group / skill_name
                        if group
                        else self.repo_root / "skills" / skill_name
                    )
                    enabled[skill_name] = src

            target_changed = False

            for skill_name, source in sorted(enabled.items()):
                dest = target_dir / skill_name
                if not dest.exists() and not dest.is_symlink():
                    print(f"[CREATE] {target_name}/{skill_name} -> {source}")
                    if not dry_run:
                        dest.symlink_to(source)
                    target_changed = True
                elif PathOwnership.is_repo_owned(dest, roots):
                    if PathOwnership.exact_link_matches(dest, source):
                        continue
                    print(f"[UPDATE] {target_name}/{skill_name} -> {source}")
                    if not dry_run:
                        dest.unlink()
                        dest.symlink_to(source)
                    target_changed = True
                else:
                    print(
                        f"CONFLICT [{target_name}/{skill_name}] non-repo entry exists at {dest}; refusing to overwrite",
                        file=sys.stderr,
                    )
                    had_errors = True

            if target_dir.exists() and target_dir.is_dir():
                for entry in sorted(target_dir.iterdir(), key=lambda p: p.name):
                    if entry.name in enabled:
                        continue
                    if PathOwnership.is_repo_owned(entry, roots):
                        print(f"[REMOVE] {target_name}/{entry.name}")
                        if not dry_run:
                            entry.unlink()
                        target_changed = True

            if target_name == "opencode" and target_changed:
                opencode_changed = True

        if opencode_changed and not dry_run and not no_reload:
            self._reload_opencode()

        return 1 if had_errors else 0


class StatusReporter:
    """Render aligned skill matrix status and unmanaged target entries."""

    @staticmethod
    def render(repo_root: Path, home: Path | None = None) -> tuple[int, str]:
        """Render skill matrix status report, returning `(exit_code, text)`."""
        try:
            manifest = ManifestStore.load_skills_manifest(repo_root, home=home)
        except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
            return 1, f"ERROR: Failed to load skills.toml: {exc}"

        roots = PathOwnership.roots_for_manifest(repo_root, manifest)
        rows_out: list[tuple[str, str, list[str]]] = []

        for group, skills_map in manifest.groups.items():
            for skill_name, row in sorted(skills_map.items()):
                cells: list[str] = []
                source = (
                    repo_root / "skills" / group / skill_name
                    if group
                    else repo_root / "skills" / skill_name
                )
                for col_idx, target_name in enumerate(manifest.columns):
                    target_dir = manifest.targets[target_name]
                    if target_dir.is_symlink():
                        cells.append("TARGET_SYMLINK")
                        continue
                    enabled = (
                        isinstance(row, list)
                        and col_idx < len(row)
                        and row[col_idx] == 1
                    )
                    dest = target_dir / skill_name
                    if enabled:
                        if not dest.exists() and not dest.is_symlink():
                            cells.append("MISSING")
                        elif PathOwnership.is_repo_owned(dest, roots):
                            if not dest.exists():
                                cells.append("BROKEN")
                            elif PathOwnership.exact_link_matches(dest, source):
                                cells.append("1")
                            else:
                                cells.append("DRIFT")
                        else:
                            cells.append("CONFLICT")
                    else:
                        if not dest.exists() and not dest.is_symlink():
                            cells.append(".")
                        elif PathOwnership.is_repo_owned(dest, roots):
                            cells.append("EXTRA" if dest.exists() else "BROKEN")
                        else:
                            cells.append("CONFLICT")
                rows_out.append((skill_name, group, cells))

        buf = io.StringIO()
        skill_w = max([len("skill"), *(len(r[0]) for r in rows_out)])
        group_w = max([len("group"), *(len(r[1]) for r in rows_out)])
        col_ws = [
            max([len(c), *(len(r[2][i]) for r in rows_out)])
            for i, c in enumerate(manifest.columns)
        ]

        header = (
            f"{'skill':<{skill_w}}  {'group':<{group_w}}  "
            + "  ".join(f"{c:<{col_ws[i]}}" for i, c in enumerate(manifest.columns))
        )
        print(header, file=buf)
        print("-" * len(header), file=buf)
        for skill_name, group, cells in rows_out:
            cols_str = "  ".join(
                f"{cells[i]:<{col_ws[i]}}" for i in range(len(manifest.columns))
            )
            print(f"{skill_name:<{skill_w}}  {group:<{group_w}}  {cols_str}", file=buf)

        print("\nUnmanaged / external entries per target:", file=buf)
        for target_name in manifest.columns:
            target_dir = manifest.targets[target_name]
            if target_dir.is_symlink():
                print(
                    f"  {target_name} ({target_dir}): TARGET_SYMLINK -> {os.readlink(target_dir)}",
                    file=buf,
                )
                continue
            if not target_dir.exists():
                print(f"  {target_name} ({target_dir}): (absent)", file=buf)
                continue
            unmanaged = []
            for entry in sorted(target_dir.iterdir(), key=lambda p: p.name):
                if not PathOwnership.is_repo_owned(entry, roots):
                    kind = PathOwnership.classify_unmanaged(entry, manifest.policy)
                    unmanaged.append(f"{entry.name} [{kind}]")
            summary = ", ".join(unmanaged) if unmanaged else "(none)"
            print(f"  {target_name} ({target_dir}): {summary}", file=buf)

        print("\nShared agent directories:", file=buf)
        for shared_dir in manifest.policy.shared_dirs:
            if shared_dir.is_symlink():
                print(
                    f"  {shared_dir}: SYMLINK -> {os.readlink(shared_dir)}",
                    file=buf,
                )
            elif not shared_dir.exists():
                print(f"  {shared_dir}: (absent)", file=buf)
            else:
                items = [
                    f"{e.name} [{PathOwnership.classify_unmanaged(e, manifest.policy)}]"
                    for e in sorted(shared_dir.iterdir(), key=lambda p: p.name)
                ]
                print(
                    f"  {shared_dir}: {', '.join(items) if items else '(empty)'}",
                    file=buf,
                )

        return 0, buf.getvalue().rstrip("\n")

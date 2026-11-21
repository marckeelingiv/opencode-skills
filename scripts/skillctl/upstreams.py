"""Upstream check, update, merge, and import engine."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from skillctl.config import ManifestStore, UpstreamSource, UpstreamsManifest
from skillctl.frontmatter import FrontmatterParser
from skillctl.github import (
    CommitSummary,
    UpstreamTransport,
    UpstreamTransportError,
    normalize_repo_path,
    path_matches_prefix,
    validate_repo_slug,
)
from skillctl.validator import RepoValidator

VALID_MODES = ("mirror", "adapted", "notify")
_LICENSE_CANDIDATES = ("LICENSE", "LICENSE.txt", "LICENSE.md")


@dataclass
class CheckItem:
    """Result of checking a single upstream source."""

    source_id: str
    repo: str
    ref: str
    mode: str
    pinned_commit: str
    head_commit: str
    status: str
    changed_skills: list[str] = field(default_factory=list)
    new_upstream_skills: list[str] = field(default_factory=list)
    commits: list[CommitSummary] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "repo": self.repo,
            "ref": self.ref,
            "mode": self.mode,
            "pinned_commit": self.pinned_commit,
            "head_commit": self.head_commit,
            "status": self.status,
            "changed_skills": list(self.changed_skills),
            "new_upstream_skills": list(self.new_upstream_skills),
            "commits": [c.to_dict() for c in self.commits],
            "notes": list(self.notes),
            "error": self.error,
        }


@dataclass
class UpdateItem:
    """Result of applying or previewing an upstream update."""

    source_id: str
    repo: str
    mode: str
    old_commit: str
    new_commit: str
    action: str
    updated_skills: list[str] = field(default_factory=list)
    merged_files: list[str] = field(default_factory=list)
    conflict_files: list[str] = field(default_factory=list)
    diff: str = ""
    new_upstream_skills: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str | None = None
    commits: list[CommitSummary] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "repo": self.repo,
            "mode": self.mode,
            "old_commit": self.old_commit,
            "new_commit": self.new_commit,
            "action": self.action,
            "updated_skills": list(self.updated_skills),
            "merged_files": list(self.merged_files),
            "conflict_files": list(self.conflict_files),
            "diff": self.diff,
            "new_upstream_skills": list(self.new_upstream_skills),
            "notes": list(self.notes),
            "error": self.error,
            "commits": [c.to_dict() for c in self.commits],
        }


@dataclass
class AddResult:
    """Result of discovering or importing skills from a GitHub repository."""

    repo: str
    source_id: str
    ref: str
    commit: str
    group: str
    mode: str
    discovered_paths: list[str] = field(default_factory=list)
    added_skills: list[str] = field(default_factory=list)
    license_copied: str | None = None
    validation_errors: list[str] = field(default_factory=list)
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "source_id": self.source_id,
            "ref": self.ref,
            "commit": self.commit,
            "group": self.group,
            "mode": self.mode,
            "discovered_paths": list(self.discovered_paths),
            "added_skills": list(self.added_skills),
            "license_copied": self.license_copied,
            "validation_errors": list(self.validation_errors),
            "dry_run": self.dry_run,
        }


def _skill_paths_map(
    source: UpstreamSource, skill_filter: str | None = None
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for skill_name, raw_paths in source.skills.items():
        if skill_filter is not None and skill_name != skill_filter:
            continue
        if isinstance(raw_paths, str):
            result[skill_name] = [normalize_repo_path(raw_paths)]
        else:
            result[skill_name] = [normalize_repo_path(p) for p in raw_paths]
    return result


def _unique_paths(paths_map: dict[str, list[str]]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for path_list in paths_map.values():
        for path in path_list:
            if path not in seen:
                seen.add(path)
                ordered.append(path)
    return ordered


def _matching_changed_skills(
    paths_map: dict[str, list[str]], changed_files: list[str]
) -> list[str]:
    changed: list[str] = []
    for skill_name, tracked_list in paths_map.items():
        if any(
            path_matches_prefix(fn, tracked)
            for tracked in tracked_list
            for fn in changed_files
        ):
            changed.append(skill_name)
    return changed


def _validate_fetched_skill_md(
    skill_dir: Path,
    expected_name: str | None,
    label: str,
) -> dict[str, Any]:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise ValueError(f"Missing SKILL.md for '{label}'")
    try:
        fm = FrontmatterParser.parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"Invalid frontmatter in '{label}/SKILL.md': {exc}") from exc

    fm_name = fm.get("name")
    if expected_name is not None and fm_name != expected_name:
        raise ValueError(
            f"Frontmatter name mismatch in '{label}/SKILL.md': "
            f"expected '{expected_name}', got {fm_name!r}"
        )
    desc = fm.get("description")
    if not isinstance(desc, str) or not desc.strip():
        raise ValueError(f"Missing or empty description in '{label}/SKILL.md'")
    return fm


def _replace_dir_atomically(src_dir: Path, dest_dir: Path) -> None:
    """Replace dest_dir with a fresh copy of src_dir preserving file metadata."""
    dest_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".tmp-{dest_dir.name}-", dir=dest_dir.parent
    ) as stage_parent:
        staged = Path(stage_parent) / dest_dir.name
        shutil.copytree(
            src_dir,
            staged,
            copy_function=shutil.copy2,
            ignore=shutil.ignore_patterns(".git"),
        )
        backup: Path | None = None
        if dest_dir.exists():
            backup = Path(stage_parent) / f"{dest_dir.name}.bak"
            dest_dir.rename(backup)
        try:
            staged.rename(dest_dir)
        except Exception:
            if backup is not None and backup.exists() and not dest_dir.exists():
                backup.rename(dest_dir)
            raise
        if backup is not None and backup.exists():
            shutil.rmtree(backup, ignore_errors=True)


def _is_binary_file(path: Path) -> bool:
    if not path.is_file():
        return False
    return b"\x00" in path.read_bytes()


def _collect_rel_files(*dirs: Path) -> list[Path]:
    rels: set[Path] = set()
    for base in dirs:
        if not base.is_dir():
            continue
        for item in base.rglob("*"):
            if item.is_file() and ".git" not in item.relative_to(base).parts:
                rels.add(item.relative_to(base))
    return sorted(rels)


class UpstreamManager:
    """Coordinates checking, updating, merging, and importing upstream skills."""

    def __init__(self, repo_root: Path, transport: UpstreamTransport) -> None:
        self.repo_root = repo_root
        self.transport = transport

    def _select_sources(
        self,
        upstreams: UpstreamsManifest,
        source_filter: str | None,
        skill_filter: str | None,
    ) -> list[UpstreamSource]:
        if source_filter is not None and source_filter not in upstreams.sources:
            raise ValueError(f"Unknown upstream source: '{source_filter}'")

        selected: list[UpstreamSource] = []
        for source in upstreams.sources.values():
            if source_filter is not None and source.id != source_filter:
                continue
            if skill_filter is not None and skill_filter not in source.skills:
                continue
            selected.append(source)

        if skill_filter is not None and not selected:
            scope = f" in source '{source_filter}'" if source_filter else ""
            raise ValueError(f"Unknown upstream skill '{skill_filter}'{scope}")
        return selected

    def check(
        self,
        source_filter: str | None = None,
        skill_filter: str | None = None,
    ) -> list[CheckItem]:
        """Check upstream sources for new commits or new skills under watch_roots."""
        upstreams = ManifestStore.load_upstreams_manifest(self.repo_root)
        sources = self._select_sources(upstreams, source_filter, skill_filter)
        results: list[CheckItem] = []

        for source in sources:
            notes: list[str] = [source.notes] if source.notes else []
            try:
                head_commit = self.transport.get_head_commit(source.repo, source.ref)
                filtered_map = _skill_paths_map(source, skill_filter)
                tracked_paths = _unique_paths(filtered_map)

                new_upstream_skills: list[str] = []
                if source.watch_roots:
                    discovered = self.transport.list_skill_dirs(
                        source.repo, head_commit, source.watch_roots
                    )
                    all_tracked = set(_unique_paths(_skill_paths_map(source, None)))
                    new_upstream_skills = [
                        d for d in discovered if d not in all_tracked
                    ]

                if source.commit == "":
                    notes.append("Source is unpinned (commit is empty).")
                    results.append(
                        CheckItem(
                            source_id=source.id,
                            repo=source.repo,
                            ref=source.ref,
                            mode=source.mode,
                            pinned_commit="",
                            head_commit=head_commit,
                            status="unpinned",
                            changed_skills=list(filtered_map.keys()),
                            new_upstream_skills=new_upstream_skills,
                            notes=notes,
                        )
                    )
                    continue

                if source.commit == head_commit:
                    results.append(
                        CheckItem(
                            source_id=source.id,
                            repo=source.repo,
                            ref=source.ref,
                            mode=source.mode,
                            pinned_commit=source.commit,
                            head_commit=head_commit,
                            status="up_to_date",
                            new_upstream_skills=new_upstream_skills,
                            notes=notes,
                        )
                    )
                    continue

                cmp = self.transport.compare_commits(
                    source.repo, source.commit, head_commit, tracked_paths
                )
                changed_skills = _matching_changed_skills(filtered_map, cmp.changed_files)
                if cmp.truncated:
                    notes.append(
                        "GitHub compare response truncated at 300 files; "
                        "checked tracked paths individually."
                    )

                if changed_skills:
                    results.append(
                        CheckItem(
                            source_id=source.id,
                            repo=source.repo,
                            ref=source.ref,
                            mode=source.mode,
                            pinned_commit=source.commit,
                            head_commit=head_commit,
                            status="updates_available",
                            changed_skills=changed_skills,
                            new_upstream_skills=new_upstream_skills,
                            commits=cmp.commits,
                            notes=notes,
                        )
                    )
                else:
                    notes.append(
                        f"Upstream HEAD {head_commit[:12]} has no changes in tracked paths."
                    )
                    results.append(
                        CheckItem(
                            source_id=source.id,
                            repo=source.repo,
                            ref=source.ref,
                            mode=source.mode,
                            pinned_commit=source.commit,
                            head_commit=head_commit,
                            status="up_to_date",
                            new_upstream_skills=new_upstream_skills,
                            notes=notes,
                        )
                    )
            except UpstreamTransportError as exc:
                results.append(
                    CheckItem(
                        source_id=source.id,
                        repo=source.repo,
                        ref=source.ref,
                        mode=source.mode,
                        pinned_commit=source.commit,
                        head_commit="",
                        status="error",
                        notes=notes,
                        error=str(exc),
                    )
                )
        return results

    def _can_advance_source_commit(
        self,
        source: UpstreamSource,
        head_commit: str,
        skill_filter: str | None,
    ) -> bool:
        if skill_filter is None:
            return True
        if len(source.skills) <= 1:
            return True
        if source.commit == "":
            return False
        full_map = _skill_paths_map(source, None)
        cmp = self.transport.compare_commits(
            source.repo, source.commit, head_commit, _unique_paths(full_map)
        )
        all_changed = _matching_changed_skills(full_map, cmp.changed_files)
        return set(all_changed) <= {skill_filter}

    def _apply_mirror_update(
        self,
        source: UpstreamSource,
        chk: CheckItem,
        *,
        source_filter: str | None,
        skill_filter: str | None,
        dry_run: bool,
    ) -> tuple[UpdateItem, bool]:
        notes = list(chk.notes)
        if chk.status == "unpinned" and source_filter is None and skill_filter is None:
            notes.append(
                "Unpinned mirror source skipped during bulk update; "
                "pass an explicit source or skill filter to adopt HEAD."
            )
            return (
                UpdateItem(
                    source_id=source.id,
                    repo=source.repo,
                    mode=source.mode,
                    old_commit=chk.pinned_commit,
                    new_commit=chk.head_commit,
                    action="unpinned_manual",
                    new_upstream_skills=chk.new_upstream_skills,
                    notes=notes,
                ),
                False,
            )

        target_skills = list(chk.changed_skills)
        skill_paths: dict[str, str] = {}
        for skill_name in target_skills:
            raw_val = source.skills[skill_name]
            if isinstance(raw_val, list):
                if len(raw_val) != 1:
                    return (
                        UpdateItem(
                            source_id=source.id,
                            repo=source.repo,
                            mode=source.mode,
                            old_commit=chk.pinned_commit,
                            new_commit=chk.head_commit,
                            action="error",
                            new_upstream_skills=chk.new_upstream_skills,
                            notes=notes,
                            error=f"Mirror skill '{skill_name}' must map to a single path.",
                        ),
                        False,
                    )
                skill_paths[skill_name] = normalize_repo_path(raw_val[0])
            else:
                skill_paths[skill_name] = normalize_repo_path(raw_val)

        fetch_list = list(dict.fromkeys(skill_paths.values()))
        license_rel = (
            normalize_repo_path(source.license_path) if source.license_path else None
        )
        if license_rel and license_rel not in fetch_list:
            fetch_list.append(license_rel)

        with tempfile.TemporaryDirectory(prefix="skillctl-mirror-") as tmp_root:
            fetch_dir = Path(tmp_root) / "upstream"
            fetch_dir.mkdir(parents=True, exist_ok=True)
            try:
                self.transport.fetch_paths(
                    source.repo, chk.head_commit, fetch_list, fetch_dir
                )
                for skill_name, rel_path in skill_paths.items():
                    src_dir = fetch_dir / rel_path if rel_path else fetch_dir
                    expected_name = skill_name if rel_path else None
                    _validate_fetched_skill_md(src_dir, expected_name, skill_name)
            except (UpstreamTransportError, ValueError) as exc:
                return (
                    UpdateItem(
                        source_id=source.id,
                        repo=source.repo,
                        mode=source.mode,
                        old_commit=chk.pinned_commit,
                        new_commit=chk.head_commit,
                        action="error",
                        new_upstream_skills=chk.new_upstream_skills,
                        notes=notes,
                        error=str(exc),
                    ),
                    False,
                )

            if dry_run:
                return (
                    UpdateItem(
                        source_id=source.id,
                        repo=source.repo,
                        mode=source.mode,
                        old_commit=chk.pinned_commit,
                        new_commit=chk.head_commit,
                        action="dry_run",
                        updated_skills=target_skills,
                        new_upstream_skills=chk.new_upstream_skills,
                        notes=notes,
                    ),
                    False,
                )

            for skill_name, rel_path in skill_paths.items():
                src_dir = fetch_dir / rel_path if rel_path else fetch_dir
                dest_dir = self.repo_root / "skills" / source.group / skill_name
                _replace_dir_atomically(src_dir, dest_dir)

            if license_rel and source.license_dest:
                fetched_license = fetch_dir / license_rel
                if fetched_license.is_file():
                    dest_lic = self.repo_root / source.license_dest
                    dest_lic.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(fetched_license, dest_lic)

            if self._can_advance_source_commit(source, chk.head_commit, skill_filter):
                ManifestStore.update_upstream_commit(
                    self.repo_root / "upstreams.toml", source.id, chk.head_commit
                )
            else:
                notes.append(
                    "Partial skill update; shared source commit left unchanged."
                )

            return (
                UpdateItem(
                    source_id=source.id,
                    repo=source.repo,
                    mode=source.mode,
                    old_commit=chk.pinned_commit,
                    new_commit=chk.head_commit,
                    action="updated",
                    updated_skills=target_skills,
                    new_upstream_skills=chk.new_upstream_skills,
                    notes=notes,
                ),
                True,
            )

    def _apply_adapted_update(
        self,
        source: UpstreamSource,
        chk: CheckItem,
        *,
        skill_filter: str | None,
        dry_run: bool,
        merge: bool,
    ) -> tuple[UpdateItem, bool]:
        notes = list(chk.notes)
        if chk.status == "unpinned":
            notes.append(
                "Adapted source is unpinned; cannot compute diff or 3-way merge."
            )
            return (
                UpdateItem(
                    source_id=source.id,
                    repo=source.repo,
                    mode=source.mode,
                    old_commit=chk.pinned_commit,
                    new_commit=chk.head_commit,
                    action="unpinned_manual",
                    new_upstream_skills=chk.new_upstream_skills,
                    notes=notes,
                ),
                False,
            )

        changed_map = {
            s: _skill_paths_map(source, None)[s] for s in chk.changed_skills
        }
        changed_paths = _unique_paths(changed_map)

        if not merge:
            try:
                diff_text = self.transport.diff_paths(
                    source.repo, source.commit, chk.head_commit, changed_paths
                )
            except UpstreamTransportError as exc:
                return (
                    UpdateItem(
                        source_id=source.id,
                        repo=source.repo,
                        mode=source.mode,
                        old_commit=chk.pinned_commit,
                        new_commit=chk.head_commit,
                        action="error",
                        new_upstream_skills=chk.new_upstream_skills,
                        notes=notes,
                        error=str(exc),
                    ),
                    False,
                )
            return (
                UpdateItem(
                    source_id=source.id,
                    repo=source.repo,
                    mode=source.mode,
                    old_commit=chk.pinned_commit,
                    new_commit=chk.head_commit,
                    action="diff_only",
                    updated_skills=list(chk.changed_skills),
                    diff=diff_text,
                    new_upstream_skills=chk.new_upstream_skills,
                    notes=notes,
                ),
                False,
            )

        merged_files: list[str] = []
        conflict_files: list[str] = []

        with tempfile.TemporaryDirectory(prefix="skillctl-merge-") as tmp_root:
            root = Path(tmp_root)
            base_dir = root / "base"
            theirs_dir = root / "theirs"
            dry_local_dir = root / "local-dry"
            base_dir.mkdir(parents=True, exist_ok=True)
            theirs_dir.mkdir(parents=True, exist_ok=True)
            empty_base = root / "empty"
            empty_base.write_bytes(b"")

            try:
                self.transport.fetch_paths(
                    source.repo, source.commit, changed_paths, base_dir
                )
                self.transport.fetch_paths(
                    source.repo, chk.head_commit, changed_paths, theirs_dir
                )
            except UpstreamTransportError as exc:
                return (
                    UpdateItem(
                        source_id=source.id,
                        repo=source.repo,
                        mode=source.mode,
                        old_commit=chk.pinned_commit,
                        new_commit=chk.head_commit,
                        action="error",
                        new_upstream_skills=chk.new_upstream_skills,
                        notes=notes,
                        error=str(exc),
                    ),
                    False,
                )

            for skill_name in chk.changed_skills:
                rel_paths = changed_map[skill_name]
                if len(rel_paths) != 1:
                    notes.append(
                        f"Adapted skill '{skill_name}' maps to multiple paths; skipped merge."
                    )
                    continue
                rel_path = rel_paths[0]
                b_skill = base_dir / rel_path if rel_path else base_dir
                t_skill = theirs_dir / rel_path if rel_path else theirs_dir
                real_local = self.repo_root / "skills" / source.group / skill_name

                if dry_run:
                    l_skill = dry_local_dir / skill_name
                    if real_local.exists():
                        shutil.copytree(
                            real_local, l_skill, copy_function=shutil.copy2
                        )
                    else:
                        l_skill.mkdir(parents=True, exist_ok=True)
                else:
                    l_skill = real_local
                    l_skill.mkdir(parents=True, exist_ok=True)

                for rel_file in _collect_rel_files(b_skill, t_skill, l_skill):
                    b_file = b_skill / rel_file
                    t_file = t_skill / rel_file
                    l_file = l_skill / rel_file
                    display = f"{skill_name}/{rel_file.as_posix()}"

                    if b_file.is_file() and t_file.is_file() and l_file.is_file():
                        b_bytes = b_file.read_bytes()
                        t_bytes = t_file.read_bytes()
                        if b_bytes == t_bytes:
                            continue
                        l_bytes = l_file.read_bytes()
                        if _is_binary_file(b_file) or _is_binary_file(t_file) or _is_binary_file(l_file):
                            if l_bytes == b_bytes:
                                shutil.copy2(t_file, l_file)
                                merged_files.append(display)
                            elif l_bytes != t_bytes:
                                conflict_files.append(display)
                                notes.append(f"Binary file conflict on {display}.")
                            continue
                        proc = subprocess.run(
                            [
                                "git",
                                "merge-file",
                                "-L",
                                "local",
                                "-L",
                                "base",
                                "-L",
                                "upstream",
                                str(l_file),
                                str(b_file),
                                str(t_file),
                            ],
                            text=True,
                            capture_output=True,
                            check=False,
                        )
                        if proc.returncode == 0:
                            merged_files.append(display)
                        elif proc.returncode > 0:
                            conflict_files.append(display)
                        else:
                            return (
                                UpdateItem(
                                    source_id=source.id,
                                    repo=source.repo,
                                    mode=source.mode,
                                    old_commit=chk.pinned_commit,
                                    new_commit=chk.head_commit,
                                    action="error",
                                    merged_files=merged_files,
                                    conflict_files=conflict_files,
                                    new_upstream_skills=chk.new_upstream_skills,
                                    notes=notes,
                                    error=f"`git merge-file` failed on {display}: {proc.stderr.strip()}",
                                ),
                                not dry_run,
                            )
                    elif t_file.is_file() and not b_file.exists() and not l_file.exists():
                        l_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(t_file, l_file)
                        merged_files.append(display)
                    elif t_file.is_file() and not b_file.exists() and l_file.is_file():
                        if t_file.read_bytes() == l_file.read_bytes():
                            continue
                        if _is_binary_file(t_file) or _is_binary_file(l_file):
                            conflict_files.append(display)
                            notes.append(f"Binary add/add conflict on {display}.")
                            continue
                        proc = subprocess.run(
                            [
                                "git",
                                "merge-file",
                                "-L",
                                "local",
                                "-L",
                                "base",
                                "-L",
                                "upstream",
                                str(l_file),
                                str(empty_base),
                                str(t_file),
                            ],
                            text=True,
                            capture_output=True,
                            check=False,
                        )
                        if proc.returncode == 0:
                            merged_files.append(display)
                        elif proc.returncode > 0:
                            conflict_files.append(display)
                    elif b_file.is_file() and not t_file.exists():
                        notes.append(
                            f"Upstream deleted {display}; local copy retained."
                        )

        if dry_run:
            return (
                UpdateItem(
                    source_id=source.id,
                    repo=source.repo,
                    mode=source.mode,
                    old_commit=chk.pinned_commit,
                    new_commit=chk.head_commit,
                    action="dry_run",
                    updated_skills=list(chk.changed_skills),
                    merged_files=merged_files,
                    conflict_files=conflict_files,
                    new_upstream_skills=chk.new_upstream_skills,
                    notes=notes,
                ),
                False,
            )

        if conflict_files:
            notes.append("Merge conflicts detected; upstreams.toml commit not bumped.")
            return (
                UpdateItem(
                    source_id=source.id,
                    repo=source.repo,
                    mode=source.mode,
                    old_commit=chk.pinned_commit,
                    new_commit=chk.head_commit,
                    action="merge_conflicts",
                    updated_skills=list(chk.changed_skills),
                    merged_files=merged_files,
                    conflict_files=conflict_files,
                    new_upstream_skills=chk.new_upstream_skills,
                    notes=notes,
                ),
                True,
            )

        if self._can_advance_source_commit(source, chk.head_commit, skill_filter):
            ManifestStore.update_upstream_commit(
                self.repo_root / "upstreams.toml", source.id, chk.head_commit
            )
        else:
            notes.append("Partial skill merge; shared source commit left unchanged.")

        return (
            UpdateItem(
                source_id=source.id,
                repo=source.repo,
                mode=source.mode,
                old_commit=chk.pinned_commit,
                new_commit=chk.head_commit,
                action="merged",
                updated_skills=list(chk.changed_skills),
                merged_files=merged_files,
                conflict_files=conflict_files,
                new_upstream_skills=chk.new_upstream_skills,
                notes=notes,
            ),
            True,
        )

    def _apply_notify_update(
        self, source: UpstreamSource, chk: CheckItem
    ) -> UpdateItem:
        notes = list(chk.notes)
        if chk.status == "unpinned":
            notes.append("Notify source is unpinned; no diff computed.")
            return UpdateItem(
                source_id=source.id,
                repo=source.repo,
                mode=source.mode,
                old_commit=chk.pinned_commit,
                new_commit=chk.head_commit,
                action="notify_only",
                updated_skills=list(chk.changed_skills),
                new_upstream_skills=chk.new_upstream_skills,
                notes=notes,
                commits=list(chk.commits),
            )

        changed_map = {
            s: _skill_paths_map(source, None)[s] for s in chk.changed_skills
        }
        changed_paths = _unique_paths(changed_map)
        try:
            diff_text = self.transport.diff_paths(
                source.repo, source.commit, chk.head_commit, changed_paths
            )
        except UpstreamTransportError as exc:
            return UpdateItem(
                source_id=source.id,
                repo=source.repo,
                mode=source.mode,
                old_commit=chk.pinned_commit,
                new_commit=chk.head_commit,
                action="error",
                new_upstream_skills=chk.new_upstream_skills,
                notes=notes,
                error=str(exc),
                commits=list(chk.commits),
            )

        return UpdateItem(
            source_id=source.id,
            repo=source.repo,
            mode=source.mode,
            old_commit=chk.pinned_commit,
            new_commit=chk.head_commit,
            action="notify_only",
            updated_skills=list(chk.changed_skills),
            diff=diff_text,
            new_upstream_skills=chk.new_upstream_skills,
            notes=notes,
            commits=list(chk.commits),
        )

    def update(
        self,
        source_filter: str | None = None,
        skill_filter: str | None = None,
        *,
        dry_run: bool = False,
        merge: bool = False,
    ) -> list[UpdateItem]:
        """Apply or preview upstream updates according to each source's mode."""
        checks = self.check(source_filter=source_filter, skill_filter=skill_filter)
        upstreams = ManifestStore.load_upstreams_manifest(self.repo_root)
        items: list[UpdateItem] = []
        did_modify_disk = False

        for chk in checks:
            source = upstreams.sources[chk.source_id]
            if chk.status == "error":
                items.append(
                    UpdateItem(
                        source_id=source.id,
                        repo=source.repo,
                        mode=source.mode,
                        old_commit=chk.pinned_commit,
                        new_commit=chk.head_commit,
                        action="error",
                        new_upstream_skills=chk.new_upstream_skills,
                        notes=list(chk.notes),
                        error=chk.error,
                    )
                )
                continue

            if chk.status == "up_to_date":
                items.append(
                    UpdateItem(
                        source_id=source.id,
                        repo=source.repo,
                        mode=source.mode,
                        old_commit=chk.pinned_commit,
                        new_commit=chk.head_commit,
                        action="up_to_date",
                        new_upstream_skills=chk.new_upstream_skills,
                        notes=list(chk.notes),
                    )
                )
                continue

            if source.mode == "mirror":
                item, modified = self._apply_mirror_update(
                    source,
                    chk,
                    source_filter=source_filter,
                    skill_filter=skill_filter,
                    dry_run=dry_run,
                )
                did_modify_disk = did_modify_disk or modified
                items.append(item)
            elif source.mode == "adapted":
                item, modified = self._apply_adapted_update(
                    source,
                    chk,
                    skill_filter=skill_filter,
                    dry_run=dry_run,
                    merge=merge,
                )
                did_modify_disk = did_modify_disk or modified
                items.append(item)
            elif source.mode == "notify":
                items.append(self._apply_notify_update(source, chk))
            else:
                items.append(
                    UpdateItem(
                        source_id=source.id,
                        repo=source.repo,
                        mode=source.mode,
                        old_commit=chk.pinned_commit,
                        new_commit=chk.head_commit,
                        action="error",
                        new_upstream_skills=chk.new_upstream_skills,
                        notes=list(chk.notes),
                        error=f"Unsupported upstream mode: '{source.mode}'",
                    )
                )

        if did_modify_disk and not dry_run:
            val_errors = RepoValidator.validate_all(self.repo_root)
            if val_errors:
                for item in items:
                    if item.action in ("updated", "merged", "merge_conflicts"):
                        item.notes.extend(f"Validation: {e}" for e in val_errors)
                        if item.error is None:
                            item.error = "; ".join(val_errors)

        return items

    def add(
        self,
        repo: str,
        *,
        ref: str | None = None,
        paths: list[str] | None = None,
        all_skills: bool = False,
        source_id: str | None = None,
        group: str | None = None,
        mode: str = "mirror",
        enable: list[str] | None = None,
        watch_roots: list[str] | None = None,
        license_dest: str | None = None,
        dry_run: bool = False,
    ) -> AddResult:
        """Discover or import skills from a GitHub repository."""
        slug = validate_repo_slug(repo)
        if mode not in VALID_MODES:
            raise ValueError(
                f"Invalid upstream mode '{mode}'; expected one of {VALID_MODES}"
            )

        manifest = ManifestStore.load_skills_manifest(self.repo_root)
        enable_targets = list(enable or [])
        unknown_targets = [t for t in enable_targets if t not in manifest.columns]
        if unknown_targets:
            raise ValueError(
                f"Unknown target column(s): {', '.join(unknown_targets)}"
            )

        info = self.transport.get_repo_info(slug, ref)
        use_ref = ref.strip() if ref else info.default_branch
        discovered = self.transport.list_skill_dirs(
            slug, info.head_commit, watch_roots
        )

        sid = (source_id or slug.split("/")[-1]).lower()
        target_group = group or f"imported/{sid}"

        if paths:
            selected_paths = [normalize_repo_path(p) for p in paths]
        elif all_skills:
            selected_paths = list(discovered)
        else:
            return AddResult(
                repo=slug,
                source_id=sid,
                ref=use_ref,
                commit=info.head_commit,
                group=target_group,
                mode=mode,
                discovered_paths=discovered,
                added_skills=[],
                license_copied=None,
                validation_errors=[],
                dry_run=dry_run,
            )

        if not selected_paths:
            raise ValueError(f"No skill directories found in {slug}@{use_ref}")

        upstreams = ManifestStore.load_upstreams_manifest(
            self.repo_root, allow_missing=True
        )

        with tempfile.TemporaryDirectory(prefix="skillctl-add-") as tmp_root:
            root = Path(tmp_root)
            fetch_dir = root / "skills"
            license_dir = root / "license"
            fetch_dir.mkdir(parents=True, exist_ok=True)
            license_dir.mkdir(parents=True, exist_ok=True)

            self.transport.fetch_paths(
                slug, info.head_commit, selected_paths, fetch_dir
            )

            fetched_license: Path | None = None
            if license_dest or info.license_spdx:
                try:
                    self.transport.fetch_paths(
                        slug, info.head_commit, list(_LICENSE_CANDIDATES), license_dir
                    )
                    for candidate in _LICENSE_CANDIDATES:
                        cand_path = license_dir / candidate
                        if cand_path.is_file():
                            fetched_license = cand_path
                            break
                except UpstreamTransportError:
                    fetched_license = None

            skill_map: dict[str, str] = {}
            for rel_path in selected_paths:
                src_dir = fetch_dir / rel_path if rel_path else fetch_dir
                fm = _validate_fetched_skill_md(
                    src_dir,
                    Path(rel_path).name if rel_path else None,
                    rel_path or slug,
                )
                if rel_path:
                    skill_name = Path(rel_path).name
                else:
                    raw_name = fm.get("name")
                    skill_name = (
                        raw_name.strip()
                        if isinstance(raw_name, str) and raw_name.strip()
                        else sid
                    )

                if skill_name in skill_map:
                    raise ValueError(
                        f"Duplicate skill name '{skill_name}' in selected paths"
                    )
                if skill_name in manifest.declared:
                    raise ValueError(
                        f"Skill '{skill_name}' already exists in skills.toml"
                    )
                if skill_name in manifest.targets:
                    raise ValueError(
                        f"Skill name '{skill_name}' collides with a target name"
                    )
                dest_dir = self.repo_root / "skills" / target_group / skill_name
                if dest_dir.exists():
                    raise ValueError(
                        f"Skill directory already exists: {dest_dir}"
                    )
                for existing_src in upstreams.sources.values():
                    if existing_src.id != sid and skill_name in existing_src.skills:
                        raise ValueError(
                            f"Skill '{skill_name}' is already claimed by upstream '{existing_src.id}'"
                        )

                skill_map[skill_name] = rel_path

            final_license_dest: str | None = None
            if fetched_license is not None:
                if license_dest:
                    final_license_dest = license_dest
                elif info.license_spdx:
                    final_license_dest = f"licenses/{sid}-{info.license_spdx}.txt"
                else:
                    final_license_dest = f"licenses/{sid}-LICENSE.txt"

            row_values = [1 if col in enable_targets else 0 for col in manifest.columns]
            new_rows = {name: list(row_values) for name in skill_map}

            if dry_run:
                return AddResult(
                    repo=slug,
                    source_id=sid,
                    ref=use_ref,
                    commit=info.head_commit,
                    group=target_group,
                    mode=mode,
                    discovered_paths=discovered,
                    added_skills=list(skill_map.keys()),
                    license_copied=final_license_dest,
                    validation_errors=[],
                    dry_run=True,
                )

            for skill_name, rel_path in skill_map.items():
                src_dir = fetch_dir / rel_path if rel_path else fetch_dir
                dest_dir = self.repo_root / "skills" / target_group / skill_name
                dest_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(
                    src_dir,
                    dest_dir,
                    copy_function=shutil.copy2,
                    ignore=shutil.ignore_patterns(".git"),
                )

            if fetched_license is not None and final_license_dest is not None:
                lic_target = self.repo_root / final_license_dest
                lic_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(fetched_license, lic_target)

            new_source = UpstreamSource(
                id=sid,
                repo=slug,
                ref=use_ref,
                commit=info.head_commit,
                group=target_group,
                mode=mode,
                skills=dict(skill_map),
                watch_roots=list(watch_roots or []),
                license_path=fetched_license.name if fetched_license else None,
                license_dest=final_license_dest,
            )
            ManifestStore.upsert_upstream_source(
                self.repo_root / "upstreams.toml", new_source
            )
            ManifestStore.append_skill_rows(
                self.repo_root / "skills.toml",
                target_group,
                new_rows,
                manifest.columns,
            )

            validation_errors = RepoValidator.validate_all(self.repo_root)
            return AddResult(
                repo=slug,
                source_id=sid,
                ref=use_ref,
                commit=info.head_commit,
                group=target_group,
                mode=mode,
                discovered_paths=discovered,
                added_skills=list(skill_map.keys()),
                license_copied=final_license_dest,
                validation_errors=validation_errors,
                dry_run=False,
            )

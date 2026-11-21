"""Repository validation for skills.toml, SKILL.md frontmatter, and upstreams.toml."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

from .config import VALID_UPSTREAM_MODES, ManifestStore
from .frontmatter import SkillDiscovery

_REPO_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _validate_policy_raw(policy_raw: Any) -> list[str]:
    errors: list[str] = []
    if policy_raw is None:
        return errors
    if not isinstance(policy_raw, dict):
        return ["skills.toml [policy] must be a table"]

    str_list_fields = (
        "orca_skills",
        "omarchy_prefixes",
        "system_entries",
        "shared_dirs",
        "strict_shared_dirs",
    )
    for field_name in str_list_fields:
        if field_name in policy_raw:
            val = policy_raw[field_name]
            if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
                errors.append(
                    f"skills.toml [policy].{field_name} must be a list of strings"
                )

    if "claude_skills_dir" in policy_raw and not isinstance(
        policy_raw["claude_skills_dir"], str
    ):
        errors.append("skills.toml [policy].claude_skills_dir must be a string")

    if "leak_configs" in policy_raw:
        leaks = policy_raw["leak_configs"]
        if not isinstance(leaks, list):
            errors.append("skills.toml [policy].leak_configs must be a list of tables")
        else:
            for idx, item in enumerate(leaks):
                if not isinstance(item, dict):
                    errors.append(
                        f"skills.toml [policy].leak_configs[{idx}] must be a table"
                    )
                    continue
                if not isinstance(item.get("path"), str) or not item["path"].strip():
                    errors.append(
                        f"skills.toml [policy].leak_configs[{idx}].path must be a non-empty string"
                    )
                keys = item.get("keys")
                if not isinstance(keys, list) or not all(
                    isinstance(k, str) for k in keys
                ):
                    errors.append(
                        f"skills.toml [policy].leak_configs[{idx}].keys must be a list of strings"
                    )
    return errors


class RepoValidator:
    """Validate skills.toml, on-disk SKILL.md files, and upstreams.toml."""

    @classmethod
    def validate_skills(cls, repo_root: Path) -> list[str]:
        """Validate skills.toml and on-disk skills, returning a list of error strings."""
        errors: list[str] = []
        toml_path = repo_root / "skills.toml"
        try:
            with toml_path.open("rb") as fh:
                raw = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            return [f"Failed to load skills.toml: {exc}"]

        policy_errors = _validate_policy_raw(raw.get("policy"))
        if policy_errors:
            errors.extend(policy_errors)
            return errors

        try:
            manifest = ManifestStore.load_skills_manifest(repo_root)
        except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
            return [f"Failed to load skills.toml: {exc}"]

        if manifest.canonical_root is None:
            errors.append("skills.toml missing 'canonical_root'")

        if not manifest.columns:
            errors.append("skills.toml 'columns' must be non-empty")

        for col in manifest.columns:
            if col not in manifest.targets:
                errors.append(f"Column '{col}' missing from [targets]")

        for skill_name, first_group, second_group in manifest.duplicate_declared:
            errors.append(
                f"Duplicate skill '{skill_name}' declared in groups '{first_group}' and '{second_group}'"
            )

        on_disk = SkillDiscovery.discover(repo_root)
        disk_by_name: dict[str, list[Any]] = {}
        for info in on_disk:
            disk_by_name.setdefault(info.name, []).append(info)

        for skill_name, infos in sorted(disk_by_name.items()):
            if len(infos) > 1:
                groups_str = ", ".join(s.group or "." for s in infos)
                errors.append(
                    f"Duplicate leaf skill '{skill_name}' on disk across groups: {groups_str}"
                )

        for info in on_disk:
            if info.error is not None:
                errors.append(
                    f"{info.group}/{info.name}/SKILL.md frontmatter error: {info.error}"
                )
            else:
                fm_name = info.frontmatter.get("name")
                if fm_name != info.name:
                    errors.append(
                        f"{info.group}/{info.name}/SKILL.md frontmatter name '{fm_name}' != folder name '{info.name}'"
                    )
                fm_desc = info.frontmatter.get("description")
                if not isinstance(fm_desc, str) or not fm_desc.strip():
                    errors.append(
                        f"{info.group}/{info.name}/SKILL.md missing or empty 'description'"
                    )

            if info.name not in manifest.declared:
                errors.append(
                    f"Skill on disk '{info.group}/{info.name}' is not declared in skills.toml"
                )
            else:
                decl_group, _ = manifest.declared[info.name]
                if decl_group != info.group:
                    errors.append(
                        f"Skill '{info.name}' is in '{info.group}' on disk but '{decl_group}' in skills.toml"
                    )

        ncols = len(manifest.columns)
        for group, rows in manifest.groups.items():
            for skill_name, row in rows.items():
                if skill_name not in disk_by_name:
                    errors.append(
                        f"Declared skill '{group}/{skill_name}' not found on disk under skills/{group}/{skill_name}/SKILL.md"
                    )
                if not isinstance(row, list) or len(row) != ncols:
                    errors.append(
                        f"Skill '{group}/{skill_name}' row must be a list of length {ncols}, got {row!r}"
                    )
                    continue
                for idx, val in enumerate(row):
                    if not (
                        isinstance(val, int)
                        and not isinstance(val, bool)
                        and val in (0, 1)
                    ):
                        errors.append(
                            f"Skill '{group}/{skill_name}' column '{manifest.columns[idx]}' has invalid value {val!r} (must be int 0 or 1)"
                        )

        for parent, deps in manifest.requires.items():
            if parent not in manifest.declared:
                errors.append(f"[requires] parent '{parent}' is not a declared skill")
                continue
            _, parent_row = manifest.declared[parent]
            for dep in deps:
                if dep not in manifest.declared:
                    errors.append(
                        f"[requires] dependency '{dep}' for '{parent}' is not a declared skill"
                    )
                    continue
                _, dep_row = manifest.declared[dep]
                if (
                    isinstance(parent_row, list)
                    and isinstance(dep_row, list)
                    and len(parent_row) == len(dep_row)
                ):
                    for idx, p_val in enumerate(parent_row):
                        if p_val == 1 and dep_row[idx] != 1:
                            col_name = (
                                manifest.columns[idx]
                                if idx < len(manifest.columns)
                                else str(idx)
                            )
                            errors.append(
                                f"[requires] violation: '{parent}' is enabled for '{col_name}' without dependency '{dep}'"
                            )

        return errors

    @classmethod
    def validate_upstreams(cls, repo_root: Path) -> list[str]:
        """Validate upstreams.toml against skills.toml when upstreams.toml exists."""
        upstreams_path = repo_root / "upstreams.toml"
        if not upstreams_path.exists():
            return []

        errors: list[str] = []
        try:
            with upstreams_path.open("rb") as fh:
                raw_upstreams = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            return [f"Failed to load upstreams.toml: {exc}"]

        try:
            skills_manifest = ManifestStore.load_skills_manifest(repo_root)
        except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
            return [f"Failed to load skills.toml for upstream validation: {exc}"]

        sources_raw = raw_upstreams.get("sources")
        if sources_raw is None:
            return []
        if not isinstance(sources_raw, dict):
            return ["upstreams.toml [sources] must be a table"]

        claimed_by: dict[str, str] = {}
        for sid, sdata in sources_raw.items():
            if not isinstance(sdata, dict):
                errors.append(f"upstreams.toml [sources.{sid}] must be a table")
                continue

            repo = sdata.get("repo")
            if not isinstance(repo, str) or not _REPO_SLUG_RE.match(repo):
                errors.append(
                    f"upstreams.toml [sources.{sid}].repo must be 'owner/name', got {repo!r}"
                )

            ref = sdata.get("ref")
            if not isinstance(ref, str) or not ref.strip():
                errors.append(
                    f"upstreams.toml [sources.{sid}].ref must be a non-empty string"
                )

            commit = sdata.get("commit", "")
            if not isinstance(commit, str):
                errors.append(
                    f"upstreams.toml [sources.{sid}].commit must be a string"
                )

            group = sdata.get("group")
            if not isinstance(group, str) or not group.strip():
                errors.append(
                    f"upstreams.toml [sources.{sid}].group must be a non-empty string"
                )

            mode = sdata.get("mode")
            if mode not in VALID_UPSTREAM_MODES:
                errors.append(
                    f"upstreams.toml [sources.{sid}].mode must be one of {VALID_UPSTREAM_MODES}, got {mode!r}"
                )

            watch_roots = sdata.get("watch_roots")
            if watch_roots is not None and (
                not isinstance(watch_roots, list)
                or not all(isinstance(w, str) for w in watch_roots)
            ):
                errors.append(
                    f"upstreams.toml [sources.{sid}].watch_roots must be a list of strings"
                )

            skills_map = sdata.get("skills")
            if not isinstance(skills_map, dict):
                errors.append(
                    f"upstreams.toml [sources.{sid}].skills must be a table"
                )
                continue

            for skill_name, upstream_val in skills_map.items():
                if mode == "notify":
                    valid_val = isinstance(upstream_val, str) or (
                        isinstance(upstream_val, list)
                        and len(upstream_val) > 0
                        and all(isinstance(x, str) for x in upstream_val)
                    )
                    if not valid_val:
                        errors.append(
                            f"upstreams.toml [sources.{sid}.skills].{skill_name} must be a string or non-empty list of strings for mode 'notify'"
                        )
                elif not isinstance(upstream_val, str):
                    errors.append(
                        f"upstreams.toml [sources.{sid}.skills].{skill_name} must be a string for mode '{mode}'"
                    )

                if skill_name in claimed_by:
                    errors.append(
                        f"Skill '{skill_name}' is claimed by multiple upstream sources: '{claimed_by[skill_name]}' and '{sid}'"
                    )
                else:
                    claimed_by[skill_name] = sid

                if skill_name not in skills_manifest.declared:
                    errors.append(
                        f"Upstream skill '{skill_name}' in [sources.{sid}] is not declared in skills.toml"
                    )
                elif isinstance(group, str) and group.strip():
                    decl_group, _ = skills_manifest.declared[skill_name]
                    if decl_group != group:
                        errors.append(
                            f"Upstream skill '{skill_name}' in [sources.{sid}] has group '{group}', but skills.toml declares '{decl_group}'"
                        )

        return errors

    @classmethod
    def validate_all(cls, repo_root: Path) -> list[str]:
        """Run both `validate_skills` and `validate_upstreams`."""
        return cls.validate_skills(repo_root) + cls.validate_upstreams(repo_root)


def validate(repo_root: Path) -> list[str]:
    """Module-level wrapper calling `RepoValidator.validate_skills`."""
    return RepoValidator.validate_skills(repo_root)

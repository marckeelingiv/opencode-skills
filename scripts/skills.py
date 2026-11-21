#!/usr/bin/env python3
"""Manage and verify the skill matrix and target harness symlinks."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path(__file__).resolve().parent.parent
ORCA_SKILLS = {"orca-cli", "orchestration"}
OMARCHY_SKILLS_PREFIX = Path("/usr/share/omarchy")


@dataclass
class SkillInfo:
    """Discovered skill metadata from disk."""

    name: str
    group: str
    path: Path
    skill_md: Path
    frontmatter: dict[str, Any] = field(default_factory=dict)
    openai_policy: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def disable_model_invocation(self) -> bool:
        return self.frontmatter.get("disable-model-invocation") is True

    @property
    def allow_implicit_invocation(self) -> bool:
        val = self.openai_policy.get("allow_implicit_invocation")
        return True if val is None else bool(val)


@dataclass
class Manifest:
    """Parsed skills.toml manifest."""

    canonical_root: Path | None
    columns: list[str]
    targets: dict[str, Path]
    requires: dict[str, list[str]]
    groups: dict[str, dict[str, Any]]
    declared: dict[str, tuple[str, Any]]
    duplicate_declared: list[tuple[str, str, str]]


def _unquote(val: str) -> str:
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
        inner = val[1:-1]
        if val[0] == '"':
            return (
                inner.replace('\\"', '"')
                .replace("\\n", "\n")
                .replace("\\t", "\t")
                .replace("\\\\", "\\")
            )
        return inner.replace("''", "'")
    return val


def _parse_scalar(raw: str) -> Any:
    stripped = raw.strip()
    if not stripped:
        return ""
    if (stripped.startswith('"') and stripped.endswith('"') and len(stripped) >= 2) or (
        stripped.startswith("'") and stripped.endswith("'") and len(stripped) >= 2
    ):
        return _unquote(stripped)
    low = stripped.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    return stripped


def _fold_lines(lines: list[str]) -> str:
    """Fold YAML '>' block scalar lines."""
    out: list[str] = []
    para: list[str] = []
    for line in lines:
        if line == "":
            if para:
                out.append(" ".join(para))
                para = []
            out.append("")
        else:
            para.append(line)
    if para:
        out.append(" ".join(para))
    return "\n".join(out)


def _apply_chomp(text: str, chomp: str) -> str:
    if chomp == "-":
        return text.rstrip("\n")
    if chomp == "+":
        return text
    stripped = text.rstrip("\n")
    return f"{stripped}\n" if stripped else ""


def parse_frontmatter(text: str) -> dict[str, Any]:
    """Parse YAML frontmatter between opening and closing '---' using stdlib only."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("Missing opening '---' frontmatter delimiter")

    end_idx: int | None = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break
    if end_idx is None:
        raise ValueError("Missing closing '---' frontmatter delimiter")

    fm_lines = lines[1:end_idx]
    result: dict[str, Any] = {}
    i = 0
    block_re = re.compile(r"^([|>])([+-])?(\d+)?(?:\s+#.*)?$")
    key_re = re.compile(r"^([A-Za-z0-9_-]+):(?:\s*(.*))?$")

    while i < len(fm_lines):
        line = fm_lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if line[0].isspace():
            i += 1
            continue
        m = key_re.match(line)
        if not m:
            i += 1
            continue

        key = m.group(1)
        raw_val = (m.group(2) or "").rstrip()
        block_match = block_re.match(raw_val)

        if block_match:
            style = block_match.group(1)
            chomp = block_match.group(2) or ""
            i += 1
            raw_block: list[str] = []
            while i < len(fm_lines):
                nxt = fm_lines[i]
                if nxt.strip() == "":
                    raw_block.append("")
                    i += 1
                    continue
                if nxt[0].isspace():
                    raw_block.append(nxt)
                    i += 1
                    continue
                break

            non_empty = [b for b in raw_block if b != ""]
            if not non_empty:
                result[key] = ""
                continue
            min_indent = min(len(b) - len(b.lstrip(" ")) for b in non_empty)
            dedented = [b[min_indent:] if b != "" else "" for b in raw_block]
            joined = "\n".join(dedented) if style == "|" else _fold_lines(dedented)
            # Trailing newline semantics match YAML block scalars.
            trailing_nl = "\n" if raw_block else ""
            result[key] = _apply_chomp(joined + trailing_nl, chomp)
            continue

        # Handle indented continuation lines on unquoted scalars.
        if raw_val and not (
            (raw_val.startswith('"') and raw_val.endswith('"'))
            or (raw_val.startswith("'") and raw_val.endswith("'"))
        ):
            parts = [raw_val]
            i += 1
            while i < len(fm_lines):
                nxt = fm_lines[i]
                if nxt.strip() and nxt[0].isspace():
                    parts.append(nxt.strip())
                    i += 1
                    continue
                break
            if len(parts) > 1:
                result[key] = " ".join(parts)
            else:
                result[key] = _parse_scalar(parts[0])
            continue

        result[key] = _parse_scalar(raw_val)
        i += 1

    return result


def parse_openai_yaml(path: Path) -> dict[str, Any]:
    """Parse optional agents/openai.yaml into nested dicts using stdlib only."""
    if not path.is_file():
        return {}
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    key_re = re.compile(r"^(\s*)([A-Za-z0-9_-]+):\s*(.*?)\s*$")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = key_re.match(raw_line)
        if not m:
            continue
        indent = len(m.group(1))
        key = m.group(2)
        val = m.group(3)
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if val == "":
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _parse_scalar(val)
    return root


def _flatten_skill_groups(node: dict[str, Any], prefix: str = "") -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    entries: dict[str, Any] = {}
    for key, val in node.items():
        if isinstance(val, dict):
            sub_prefix = f"{prefix}/{key}" if prefix else key
            groups.update(_flatten_skill_groups(val, sub_prefix))
        else:
            entries[key] = val
    if entries or (prefix and not node):
        groups[prefix] = entries
    return groups


def load_manifest(repo_root: Path) -> Manifest:
    """Load and normalize <repo_root>/skills.toml."""
    toml_path = repo_root / "skills.toml"
    with toml_path.open("rb") as fh:
        raw = tomllib.load(fh)

    canon_raw = raw.get("canonical_root")
    canonical_root = Path(canon_raw).expanduser() if isinstance(canon_raw, str) else None
    columns = list(raw.get("columns", []))
    targets = {
        k: Path(v).expanduser() for k, v in raw.get("targets", {}).items() if isinstance(v, str)
    }
    requires = {
        k: list(v) for k, v in raw.get("requires", {}).items() if isinstance(v, list)
    }
    groups = _flatten_skill_groups(raw.get("skills", {}))

    declared: dict[str, tuple[str, Any]] = {}
    duplicates: list[tuple[str, str, str]] = []
    for group, rows in groups.items():
        for skill_name, row in rows.items():
            if skill_name in declared:
                duplicates.append((skill_name, declared[skill_name][0], group))
            else:
                declared[skill_name] = (group, row)

    return Manifest(
        canonical_root=canonical_root,
        columns=columns,
        targets=targets,
        requires=requires,
        groups=groups,
        declared=declared,
        duplicate_declared=duplicates,
    )


def discover_skills(repo_root: Path) -> list[SkillInfo]:
    """Discover all SKILL.md directories under <repo_root>/skills/."""
    skills_root = repo_root / "skills"
    if not skills_root.is_dir():
        return []

    found: list[SkillInfo] = []
    for current, dirs, files in os.walk(skills_root):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        if "SKILL.md" not in files:
            continue
        skill_dir = Path(current)
        rel = skill_dir.relative_to(skills_root)
        group = rel.parent.as_posix()
        if group == ".":
            group = ""
        name = skill_dir.name
        skill_md = skill_dir / "SKILL.md"
        fm: dict[str, Any] = {}
        err: str | None = None
        try:
            fm = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            err = str(exc)

        openai_cfg = parse_openai_yaml(skill_dir / "agents" / "openai.yaml")
        policy = openai_cfg.get("policy")
        policy_dict = policy if isinstance(policy, dict) else {}

        found.append(
            SkillInfo(
                name=name,
                group=group,
                path=skill_dir,
                skill_md=skill_md,
                frontmatter=fm,
                openai_policy=policy_dict,
                error=err,
            )
        )
    return sorted(found, key=lambda s: (s.group, s.name))


def validate(repo_root: Path) -> list[str]:
    """Validate skills.toml and on-disk skills, returning a list of error strings."""
    errors: list[str] = []
    try:
        manifest = load_manifest(repo_root)
    except (OSError, tomllib.TOMLDecodeError) as exc:
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

    on_disk = discover_skills(repo_root)
    disk_by_name: dict[str, list[SkillInfo]] = {}
    for info in on_disk:
        disk_by_name.setdefault(info.name, []).append(info)

    for skill_name, infos in sorted(disk_by_name.items()):
        if len(infos) > 1:
            groups_str = ", ".join(s.group or "." for s in infos)
            errors.append(
                f"Duplicate leaf skill '{skill_name}' on disk across groups: {groups_str}"
            )

    # Check disk skills against manifest and validate frontmatter.
    for info in on_disk:
        if info.error is not None:
            errors.append(f"{info.group}/{info.name}/SKILL.md frontmatter error: {info.error}")
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
            errors.append(f"Skill on disk '{info.group}/{info.name}' is not declared in skills.toml")
        else:
            decl_group, _ = manifest.declared[info.name]
            if decl_group != info.group:
                errors.append(
                    f"Skill '{info.name}' is in '{info.group}' on disk but '{decl_group}' in skills.toml"
                )

    # Check manifest entries against disk and matrix row shape.
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
                if not (isinstance(val, int) and not isinstance(val, bool) and val in (0, 1)):
                    errors.append(
                        f"Skill '{group}/{skill_name}' column '{manifest.columns[idx]}' has invalid value {val!r} (must be int 0 or 1)"
                    )

    # Check [requires] dependencies.
    for parent, deps in manifest.requires.items():
        if parent not in manifest.declared:
            errors.append(f"[requires] parent '{parent}' is not a declared skill")
            continue
        _, parent_row = manifest.declared[parent]
        for dep in deps:
            if dep not in manifest.declared:
                errors.append(f"[requires] dependency '{dep}' for '{parent}' is not a declared skill")
                continue
            _, dep_row = manifest.declared[dep]
            if isinstance(parent_row, list) and isinstance(dep_row, list) and len(parent_row) == len(dep_row):
                for idx, p_val in enumerate(parent_row):
                    if p_val == 1 and dep_row[idx] != 1:
                        col_name = manifest.columns[idx] if idx < len(manifest.columns) else str(idx)
                        errors.append(
                            f"[requires] violation: '{parent}' is enabled for '{col_name}' without dependency '{dep}'"
                        )

    return errors


def is_linked_worktree(repo_root: Path) -> bool:
    """Return True when <repo_root>/.git is a file (linked git worktree)."""
    return (repo_root / ".git").is_file()


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


def is_repo_owned(entry: Path, roots: list[Path]) -> bool:
    """Return True only if entry is a symlink whose target sits inside <root>/skills."""
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


def _roots_for_manifest(repo_root: Path, manifest: Manifest) -> list[Path]:
    roots = [repo_root]
    if manifest.canonical_root is not None:
        roots.append(manifest.canonical_root)
    return roots


def _exact_link_matches(dest: Path, source: Path) -> bool:
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
    if is_linked_worktree(repo_root):
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


def cmd_sync(
    repo_root: Path,
    *,
    dry_run: bool = False,
    allow_worktree: bool = False,
    no_reload: bool = False,
    root_overridden: bool = False,
) -> int:
    """Sync enabled skills as symlinks into target directories."""
    try:
        manifest = load_manifest(repo_root)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"ERROR: Failed to load skills.toml: {exc}", file=sys.stderr)
        return 1

    guard_err = check_sync_root_guard(
        repo_root,
        manifest.canonical_root,
        dry_run=dry_run,
        allow_worktree=allow_worktree,
        root_overridden=root_overridden,
    )
    if guard_err:
        print(f"ERROR: {guard_err}", file=sys.stderr)
        return 1

    errors = validate(repo_root)
    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    roots = _roots_for_manifest(repo_root, manifest)
    had_errors = False
    opencode_changed = False

    for col_idx, target_name in enumerate(manifest.columns):
        target_dir = manifest.targets[target_name]
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
        for skill_name, (group, row) in manifest.declared.items():
            if row[col_idx] == 1:
                src = repo_root / "skills" / group / skill_name if group else repo_root / "skills" / skill_name
                enabled[skill_name] = src

        target_changed = False

        for skill_name, source in sorted(enabled.items()):
            dest = target_dir / skill_name
            if not dest.exists() and not dest.is_symlink():
                print(f"[CREATE] {target_name}/{skill_name} -> {source}")
                if not dry_run:
                    dest.symlink_to(source)
                target_changed = True
            elif is_repo_owned(dest, roots):
                if _exact_link_matches(dest, source):
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
                if is_repo_owned(entry, roots):
                    print(f"[REMOVE] {target_name}/{entry.name}")
                    if not dry_run:
                        entry.unlink()
                    target_changed = True

        if target_name == "opencode" and target_changed:
            opencode_changed = True

    if opencode_changed and not dry_run and not no_reload and shutil.which("opencode"):
        try:
            proc = subprocess.run(["opencode", "reload"], capture_output=True, text=True, timeout=30)
            if proc.returncode != 0:
                msg = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
                print(f"[WARN] opencode reload failed: {msg}", file=sys.stderr)
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"[WARN] opencode reload failed: {exc}", file=sys.stderr)

    return 1 if had_errors else 0


def _classify_unmanaged(entry: Path) -> str:
    if entry.name == ".system":
        return "system"
    if entry.name in ORCA_SKILLS:
        return "orca"
    if entry.is_symlink():
        try:
            raw = os.readlink(entry)
            joined = Path(os.path.abspath(os.path.join(os.fspath(entry.parent), raw)))
            resolved = joined.resolve(strict=False)
            if _path_matches_or_inside(joined, OMARCHY_SKILLS_PREFIX) or _path_matches_or_inside(
                resolved, OMARCHY_SKILLS_PREFIX
            ):
                return "omarchy"
        except OSError:
            pass
    return "unmanaged"


def cmd_status(repo_root: Path) -> int:
    """Print aligned skill matrix status and unmanaged target entries."""
    try:
        manifest = load_manifest(repo_root)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"ERROR: Failed to load skills.toml: {exc}", file=sys.stderr)
        return 1

    roots = _roots_for_manifest(repo_root, manifest)
    rows_out: list[tuple[str, str, list[str]]] = []

    for group, skills_map in manifest.groups.items():
        for skill_name, row in sorted(skills_map.items()):
            cells: list[str] = []
            source = repo_root / "skills" / group / skill_name if group else repo_root / "skills" / skill_name
            for col_idx, target_name in enumerate(manifest.columns):
                target_dir = manifest.targets[target_name]
                if target_dir.is_symlink():
                    cells.append("TARGET_SYMLINK")
                    continue
                enabled = isinstance(row, list) and col_idx < len(row) and row[col_idx] == 1
                dest = target_dir / skill_name
                if enabled:
                    if not dest.exists() and not dest.is_symlink():
                        cells.append("MISSING")
                    elif is_repo_owned(dest, roots):
                        if not dest.exists():
                            cells.append("BROKEN")
                        elif _exact_link_matches(dest, source):
                            cells.append("1")
                        else:
                            cells.append("DRIFT")
                    else:
                        cells.append("CONFLICT")
                else:
                    if not dest.exists() and not dest.is_symlink():
                        cells.append(".")
                    elif is_repo_owned(dest, roots):
                        cells.append("EXTRA" if dest.exists() else "BROKEN")
                    else:
                        cells.append("CONFLICT")
            rows_out.append((skill_name, group, cells))

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
    print(header)
    print("-" * len(header))
    for skill_name, group, cells in rows_out:
        cols_str = "  ".join(f"{cells[i]:<{col_ws[i]}}" for i in range(len(manifest.columns)))
        print(f"{skill_name:<{skill_w}}  {group:<{group_w}}  {cols_str}")

    print("\nUnmanaged / external entries per target:")
    for target_name in manifest.columns:
        target_dir = manifest.targets[target_name]
        if target_dir.is_symlink():
            print(f"  {target_name} ({target_dir}): TARGET_SYMLINK -> {os.readlink(target_dir)}")
            continue
        if not target_dir.exists():
            print(f"  {target_name} ({target_dir}): (absent)")
            continue
        unmanaged = []
        for entry in sorted(target_dir.iterdir(), key=lambda p: p.name):
            if not is_repo_owned(entry, roots):
                kind = _classify_unmanaged(entry)
                unmanaged.append(f"{entry.name} [{kind}]")
        summary = ", ".join(unmanaged) if unmanaged else "(none)"
        print(f"  {target_name} ({target_dir}): {summary}")

    home = Path.home()
    print("\nShared agent directories:")
    for shared_dir in (home / ".agents" / "skills", home / ".gemini" / "skills"):
        if shared_dir.is_symlink():
            print(f"  {shared_dir}: SYMLINK -> {os.readlink(shared_dir)}")
        elif not shared_dir.exists():
            print(f"  {shared_dir}: (absent)")
        else:
            items = [f"{e.name} [{_classify_unmanaged(e)}]" for e in sorted(shared_dir.iterdir(), key=lambda p: p.name)]
            print(f"  {shared_dir}: {', '.join(items) if items else '(empty)'}")

    return 0


def _collect_text_fragments(obj: Any) -> list[str]:
    texts: list[str] = []
    if isinstance(obj, str):
        texts.append(obj)
    elif isinstance(obj, list):
        for item in obj:
            texts.extend(_collect_text_fragments(item))
    elif isinstance(obj, dict):
        for val in obj.values():
            texts.extend(_collect_text_fragments(val))
    return texts


def parse_codex_prompt_input(data: Any) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Extract skill roots and available skills from `codex debug prompt-input` JSON."""
    roots: dict[str, str] = {}
    skills: list[dict[str, Any]] = []

    root_re = re.compile(r"^\s*-\s*`([^`]+)`\s*=\s*`([^`]+)`\s*$")
    skill_re = re.compile(r"^\s*-\s+(.+?):\s+.*\(file:\s*([^)]+)\)\s*$")

    for text in _collect_text_fragments(data):
        if "### Skill roots" not in text and "### Available skills" not in text:
            continue
        section: str | None = None
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("### "):
                section = stripped[4:].strip()
                continue
            if stripped.startswith("</skills_instructions>"):
                section = None
                continue
            if section == "Skill roots":
                m = root_re.match(line)
                if m:
                    roots[m.group(1)] = m.group(2)
            elif section == "Available skills":
                m = skill_re.match(line)
                if m:
                    name = m.group(1).strip()
                    raw_file = m.group(2).strip()
                    if "/" in raw_file:
                        alias, rest = raw_file.split("/", 1)
                        expanded = Path(roots[alias]) / rest if alias in roots else Path(raw_file)
                    else:
                        expanded = Path(roots[raw_file]) if raw_file in roots else Path(raw_file)
                    skills.append(
                        {
                            "name": name,
                            "file": raw_file,
                            "expanded_path": expanded,
                            "resolved_path": expanded.resolve(strict=False),
                        }
                    )

    return roots, skills


def parse_opencode_skill_list(data: Any) -> list[dict[str, Any]]:
    """Parse `opencode api skill.list` JSON into normalized skill records."""
    items = data.get("data", []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("Unexpected opencode skill.list JSON structure")

    records: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_loc = item.get("path") or item.get("location") or ""
        is_builtin = isinstance(raw_loc, str) and raw_loc.startswith("skill://")
        expanded = Path(raw_loc).expanduser() if raw_loc and not is_builtin else None
        resolved = expanded.resolve(strict=False) if expanded is not None else None
        records.append(
            {
                "id": item.get("id", ""),
                "name": item.get("name", ""),
                "location": raw_loc,
                "is_builtin": is_builtin,
                "raw_path": expanded,
                "resolved_path": resolved,
            }
        )
    return records


def _extract_json_paths(value: Any) -> list[str]:
    paths: list[str] = []
    if isinstance(value, str):
        paths.append(value)
    elif isinstance(value, list):
        for item in value:
            paths.extend(_extract_json_paths(item))
    elif isinstance(value, dict):
        if isinstance(value.get("path"), str):
            paths.append(value["path"])
    return paths


def _check_v1_static(repo_root: Path, home: Path) -> tuple[list[str], list[str]]:
    """Run V1 static & leak audit, returning (failures, warnings)."""
    failures: list[str] = []
    warnings: list[str] = []

    val_errors = validate(repo_root)
    failures.extend(val_errors)

    try:
        manifest = load_manifest(repo_root)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return [f"Cannot load skills.toml: {exc}"], warnings

    roots = _roots_for_manifest(repo_root, manifest)
    disk_skills = {s.name: s for s in discover_skills(repo_root)}

    # Check target directories and symlinks.
    for col_idx, target_name in enumerate(manifest.columns):
        target_dir = manifest.targets[target_name]
        if target_dir.is_symlink():
            failures.append(f"Target '{target_name}' is a symlink: {target_dir}")
            continue
        if not target_dir.exists() or not target_dir.is_dir():
            failures.append(f"Target '{target_name}' directory does not exist: {target_dir}")
            continue

        for skill_name, (group, row) in manifest.declared.items():
            if not isinstance(row, list) or col_idx >= len(row):
                continue
            dest = target_dir / skill_name
            source = repo_root / "skills" / group / skill_name if group else repo_root / "skills" / skill_name
            if row[col_idx] == 1:
                if not is_repo_owned(dest, roots):
                    failures.append(f"[{target_name}] '{skill_name}' missing repo-owned symlink at {dest}")
                else:
                    expected_skill_md = (source / "SKILL.md").resolve(strict=False)
                    actual_skill_md = (dest / "SKILL.md").resolve(strict=False)
                    if not (dest / "SKILL.md").is_file() or actual_skill_md != expected_skill_md:
                        failures.append(
                            f"[{target_name}] '{skill_name}' symlink resolves to {actual_skill_md}, expected {expected_skill_md}"
                        )
            else:
                if is_repo_owned(dest, roots):
                    failures.append(f"[{target_name}] '{skill_name}' is 0 in matrix but has repo symlink at {dest}")

    # Check leak paths in ~/.gemini/config/skills.json and ~/.config/opencode/opencode.json.
    check_roots = [Path(os.path.abspath(os.fspath(repo_root))), repo_root.resolve(strict=False)]
    if manifest.canonical_root is not None:
        check_roots.append(Path(os.path.abspath(os.fspath(manifest.canonical_root))))
        check_roots.append(manifest.canonical_root.resolve(strict=False))

    def points_into_repo(raw_path: str) -> bool:
        p = Path(raw_path).expanduser()
        cands = [Path(os.path.abspath(os.fspath(p))), p.resolve(strict=False)]
        return any(_path_matches_or_inside(c, r) for c in cands for r in check_roots)

    agy_skills_json = home / ".gemini" / "config" / "skills.json"
    if agy_skills_json.is_file():
        try:
            cfg = json.loads(agy_skills_json.read_text(encoding="utf-8"))
            for key in ("entries", "inherits"):
                for p in _extract_json_paths(cfg.get(key)):
                    if points_into_repo(p):
                        failures.append(f"{agy_skills_json} '{key}' points into repo: {p}")
        except (OSError, json.JSONDecodeError) as exc:
            failures.append(f"Failed to parse {agy_skills_json}: {exc}")

    oc_json = home / ".config" / "opencode" / "opencode.json"
    if oc_json.is_file():
        try:
            cfg = json.loads(oc_json.read_text(encoding="utf-8"))
            for p in _extract_json_paths(cfg.get("skills")):
                if points_into_repo(p):
                    failures.append(f"{oc_json} 'skills' points into repo: {p}")
        except (OSError, json.JSONDecodeError) as exc:
            failures.append(f"Failed to parse {oc_json}: {exc}")

    # Audit ~/.agents/skills.
    agents_skills = home / ".agents" / "skills"
    if agents_skills.exists() and agents_skills.is_dir():
        for entry in sorted(agents_skills.iterdir(), key=lambda p: p.name):
            if entry.name in ORCA_SKILLS:
                continue
            if _classify_unmanaged(entry) == "omarchy":
                continue
            failures.append(f"Unexpected non-Omarchy/non-Orca entry in {agents_skills}: {entry.name}")

    # Audit ~/.gemini/skills.
    gemini_skills = home / ".gemini" / "skills"
    if gemini_skills.exists() and gemini_skills.is_dir() and "antigravity" in manifest.columns:
        agy_idx = manifest.columns.index("antigravity")
        agy_enabled = {
            name for name, (_, row) in manifest.declared.items() if isinstance(row, list) and row[agy_idx] == 1
        }
        for entry in sorted(gemini_skills.iterdir(), key=lambda p: p.name):
            if entry.name in agy_enabled:
                failures.append(
                    f"{gemini_skills}/{entry.name} collides with skill managed in antigravity target"
                )

    # Warn if disable-model-invocation: true has codex=1 without allow_implicit_invocation: false.
    if "codex" in manifest.columns:
        codex_idx = manifest.columns.index("codex")
        for skill_name, (_, row) in manifest.declared.items():
            info = disk_skills.get(skill_name)
            if info and isinstance(row, list) and row[codex_idx] == 1:
                if info.disable_model_invocation and info.allow_implicit_invocation:
                    warnings.append(
                        f"'{skill_name}' has disable-model-invocation: true and codex=1 without agents/openai.yaml allow_implicit_invocation: false"
                    )

    return failures, warnings


def _check_v2_codex(repo_root: Path, manifest: Manifest, disk_skills: dict[str, SkillInfo]) -> list[str]:
    failures: list[str] = []
    if "codex" not in manifest.columns:
        return ["'codex' column missing from skills.toml"]
    if not shutil.which("codex"):
        return ["'codex' CLI not found on PATH"]

    try:
        proc = subprocess.run(["codex", "debug", "prompt-input"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"Failed to run codex debug prompt-input: {exc}"]

    if proc.returncode != 0:
        return [f"codex debug prompt-input exited {proc.returncode}: {proc.stderr.strip()}"]

    try:
        data = json.loads(proc.stdout)
        _, available = parse_codex_prompt_input(data)
    except (json.JSONDecodeError, ValueError) as exc:
        return [f"Failed to parse codex prompt-input JSON: {exc}"]

    by_name: dict[str, list[dict[str, Any]]] = {}
    for entry in available:
        by_name.setdefault(entry["name"], []).append(entry)

    for name, entries in sorted(by_name.items()):
        if len(entries) > 1:
            files = ", ".join(e["file"] for e in entries)
            failures.append(f"Duplicate Codex available skill '{name}': {files}")

    codex_idx = manifest.columns.index("codex")
    for skill_name, (group, row) in sorted(manifest.declared.items()):
        info = disk_skills.get(skill_name)
        enabled = isinstance(row, list) and row[codex_idx] == 1
        expected_skill_md = (
            (repo_root / "skills" / group / skill_name / "SKILL.md")
            if group
            else (repo_root / "skills" / skill_name / "SKILL.md")
        ).resolve(strict=False)
        found = by_name.get(skill_name, [])

        if enabled:
            if info and not info.allow_implicit_invocation:
                if found:
                    failures.append(
                        f"Codex skill '{skill_name}' has allow_implicit_invocation: false but appeared in Available skills"
                    )
            else:
                if not found:
                    failures.append(f"Codex skill '{skill_name}' (codex=1) missing from Available skills")
                elif not any(e["resolved_path"] == expected_skill_md for e in found):
                    actual = ", ".join(str(e["resolved_path"]) for e in found)
                    failures.append(
                        f"Codex skill '{skill_name}' resolved to {actual}, expected {expected_skill_md}"
                    )
        else:
            if found:
                failures.append(f"Codex skill '{skill_name}' (codex=0) unexpectedly present in Available skills")

    return failures


def _check_v3_opencode(repo_root: Path, manifest: Manifest, home: Path) -> list[str]:
    failures: list[str] = []
    if "opencode" not in manifest.columns:
        return ["'opencode' column missing from skills.toml"]
    if not shutil.which("opencode"):
        return ["'opencode' CLI not found on PATH"]

    try:
        proc = subprocess.run(["opencode", "api", "skill.list"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"Failed to run opencode api skill.list: {exc}"]

    if proc.returncode != 0:
        return [f"opencode api skill.list exited {proc.returncode}: {proc.stderr.strip()}"]

    try:
        records = parse_opencode_skill_list(json.loads(proc.stdout))
    except (json.JSONDecodeError, ValueError) as exc:
        return [f"Failed to parse opencode skill.list JSON: {exc}"]

    claude_skills = home / ".claude" / "skills"
    claude_abs = Path(os.path.abspath(os.fspath(claude_skills)))
    by_name: dict[str, dict[str, Any]] = {}

    for rec in records:
        name = rec["id"] or rec["name"]
        if name:
            by_name[name] = rec
        raw_path = rec.get("raw_path")
        if raw_path is not None:
            raw_abs = Path(os.path.abspath(os.fspath(raw_path)))
            if _path_matches_or_inside(raw_abs, claude_abs) or _path_matches_or_inside(
                raw_abs.parent, claude_abs
            ):
                failures.append(
                    f"OpenCode loaded skill '{name}' from ~/.claude/skills: {rec['location']}"
                )

    oc_idx = manifest.columns.index("opencode")
    for skill_name, (group, row) in sorted(manifest.declared.items()):
        enabled = isinstance(row, list) and row[oc_idx] == 1
        expected_skill_md = (
            (repo_root / "skills" / group / skill_name / "SKILL.md")
            if group
            else (repo_root / "skills" / skill_name / "SKILL.md")
        ).resolve(strict=False)
        rec = by_name.get(skill_name)
        if enabled:
            if rec is None:
                failures.append(f"OpenCode skill '{skill_name}' (opencode=1) not loaded")
            elif rec["resolved_path"] != expected_skill_md:
                failures.append(
                    f"OpenCode skill '{skill_name}' resolved to {rec['resolved_path']}, expected {expected_skill_md}"
                )
        else:
            if rec is not None:
                failures.append(
                    f"OpenCode skill '{skill_name}' (opencode=0) unexpectedly loaded from {rec['location']}"
                )

    return failures


def _check_v4_agy(manifest: Manifest, disk_skills: dict[str, SkillInfo]) -> list[str]:
    failures: list[str] = []
    if "antigravity" not in manifest.columns:
        return ["'antigravity' column missing from skills.toml"]
    if not shutil.which("agy"):
        return ["'agy' CLI not found on PATH"]

    prompt = (
        "Output ONLY the skill names listed in your <skills> section, "
        "one per line, sorted alphabetically, with no markdown or extra text."
    )
    try:
        proc = subprocess.run(
            ["agy", "--disable-slash-commands", f"-p={prompt}"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"Failed to run agy live discovery: {exc}"]

    if proc.returncode != 0:
        return [f"agy exited {proc.returncode}: {proc.stderr.strip()}"]

    listed = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    agy_idx = manifest.columns.index("antigravity")
    for skill_name, (_, row) in sorted(manifest.declared.items()):
        enabled = isinstance(row, list) and row[agy_idx] == 1
        info = disk_skills.get(skill_name)
        if enabled:
            if info and info.disable_model_invocation:
                continue
            if skill_name not in listed:
                failures.append(f"Antigravity skill '{skill_name}' (antigravity=1) missing from agy output")
        else:
            if skill_name in listed:
                failures.append(f"Antigravity skill '{skill_name}' (antigravity=0) unexpectedly present in agy output")

    return failures


def _report_section(label: str, failures: list[str], warnings: list[str] | None = None) -> bool:
    for w in warnings or []:
        print(f"[WARN] {label}: {w}")
    if failures:
        for f in failures:
            print(f"[FAIL] {label}: {f}")
        return False
    print(f"[PASS] {label}")
    return True


def cmd_verify(repo_root: Path, *, live_agy: bool = False) -> int:
    """Run static leak audit and live harness discovery verification."""
    home = Path.home()
    ok = True

    v1_failures, v1_warnings = _check_v1_static(repo_root, home)
    ok &= _report_section("V1 (Static & Leak Audit)", v1_failures, v1_warnings)

    try:
        manifest = load_manifest(repo_root)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"[FAIL] Cannot load skills.toml: {exc}")
        return 1

    disk_skills = {s.name: s for s in discover_skills(repo_root)}

    v2_failures = _check_v2_codex(repo_root, manifest, disk_skills)
    ok &= _report_section("V2 (Codex live discovery)", v2_failures)

    v3_failures = _check_v3_opencode(repo_root, manifest, home)
    ok &= _report_section("V3 (OpenCode live discovery)", v3_failures)

    if live_agy:
        v4_failures = _check_v4_agy(manifest, disk_skills)
        ok &= _report_section("V4 (Antigravity live discovery)", v4_failures)

    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage skill symlinks and verify harness discovery.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="Sync skill symlinks to target directories")
    p_sync.add_argument("--dry-run", action="store_true", help="Print actions without modifying filesystem")
    p_sync.add_argument("--allow-worktree", action="store_true", help="Allow live sync from linked worktree or non-canonical root")
    p_sync.add_argument("--no-reload", action="store_true", help="Skip `opencode reload` after changes")
    p_sync.add_argument("--root", type=Path, default=None, help="Override repository root")

    p_status = sub.add_parser("status", help="Show skill matrix and target symlink status")
    p_status.add_argument("--root", type=Path, default=None, help="Override repository root")

    p_verify = sub.add_parser("verify", help="Verify static symlinks, leaks, and live harness discovery")
    p_verify.add_argument("--live-agy", action="store_true", help="Also run live Antigravity CLI check")
    p_verify.add_argument("--root", type=Path, default=None, help="Override repository root")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root_overridden = args.root is not None
    repo_root = args.root.expanduser().resolve() if root_overridden else DEFAULT_ROOT

    if args.command == "sync":
        return cmd_sync(
            repo_root,
            dry_run=args.dry_run,
            allow_worktree=args.allow_worktree,
            no_reload=args.no_reload,
            root_overridden=root_overridden,
        )
    if args.command == "status":
        return cmd_status(repo_root)
    if args.command == "verify":
        return cmd_verify(repo_root, live_agy=args.live_agy)
    return 1


if __name__ == "__main__":
    sys.exit(main())

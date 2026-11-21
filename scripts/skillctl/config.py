"""Manifest loading, policy configuration, and surgical TOML persistence."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

VALID_UPSTREAM_MODES = ("mirror", "adapted", "notify")

_COLUMN_ABBREVS = {
    "codex": "cdx",
    "antigravity": "agy",
    "opencode": "oc",
    "claude": "cld",
    "pi": "pi",
}

_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _expand_path(raw: str | Path, home: Path | None = None) -> Path:
    """Expand leading '~' relative to `home` when provided, else `Path.expanduser()`."""
    text = str(raw)
    if home is not None:
        if text == "~":
            return home
        if text.startswith("~/"):
            return home / text[2:]
    return Path(text).expanduser()


@dataclass
class LeakConfigSpec:
    """JSON config path and keys to audit for repo path leaks."""

    path: Path
    keys: tuple[str, ...]


@dataclass
class PolicyConfig:
    """Configurable environment policy for status, sync, and verification."""

    orca_skills: tuple[str, ...] = ("orca-cli", "orchestration")
    omarchy_prefixes: tuple[Path, ...] = (Path("/usr/share/omarchy"),)
    system_entries: tuple[str, ...] = (".system",)
    shared_dirs: tuple[Path, ...] = (
        Path("~/.agents/skills"),
        Path("~/.gemini/skills"),
    )
    strict_shared_dirs: tuple[Path, ...] = (Path("~/.agents/skills"),)
    claude_skills_dir: Path = Path("~/.claude/skills")
    leak_configs: tuple[LeakConfigSpec, ...] = (
        LeakConfigSpec(Path("~/.gemini/config/skills.json"), ("entries", "inherits")),
        LeakConfigSpec(Path("~/.config/opencode/opencode.json"), ("skills",)),
    )

    @classmethod
    def default(cls, home: Path | None = None) -> PolicyConfig:
        """Return default policy with all path fields expanded."""
        return cls.from_dict(None, home=home)

    @classmethod
    def from_dict(
        cls,
        raw: dict[str, Any] | None,
        *,
        home: Path | None = None,
    ) -> PolicyConfig:
        """Build a PolicyConfig from an optional `[policy]` table, expanding `~`."""
        base = cls()
        data = raw or {}
        if not isinstance(data, dict):
            raise ValueError("[policy] must be a table")

        orca_skills = (
            tuple(str(x) for x in data["orca_skills"])
            if "orca_skills" in data
            else base.orca_skills
        )
        omarchy_raw = data.get("omarchy_prefixes", base.omarchy_prefixes)
        omarchy_prefixes = tuple(_expand_path(p, home) for p in omarchy_raw)

        system_entries = (
            tuple(str(x) for x in data["system_entries"])
            if "system_entries" in data
            else base.system_entries
        )

        shared_raw = data.get("shared_dirs", base.shared_dirs)
        shared_dirs = tuple(_expand_path(p, home) for p in shared_raw)

        strict_raw = data.get("strict_shared_dirs", base.strict_shared_dirs)
        strict_shared_dirs = tuple(_expand_path(p, home) for p in strict_raw)

        claude_raw = data.get("claude_skills_dir", base.claude_skills_dir)
        claude_skills_dir = _expand_path(claude_raw, home)

        if "leak_configs" in data:
            leak_specs: list[LeakConfigSpec] = []
            for item in data["leak_configs"]:
                if not isinstance(item, dict):
                    raise ValueError("[policy.leak_configs] items must be tables")
                if not isinstance(item.get("path"), str):
                    raise ValueError("[policy.leak_configs] item missing string 'path'")
                keys = item.get("keys")
                if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
                    raise ValueError("[policy.leak_configs] item 'keys' must be a list of strings")
                leak_specs.append(
                    LeakConfigSpec(_expand_path(item["path"], home), tuple(keys))
                )
            leak_configs = tuple(leak_specs)
        else:
            leak_configs = tuple(
                LeakConfigSpec(_expand_path(spec.path, home), spec.keys)
                for spec in base.leak_configs
            )

        return cls(
            orca_skills=orca_skills,
            omarchy_prefixes=omarchy_prefixes,
            system_entries=system_entries,
            shared_dirs=shared_dirs,
            strict_shared_dirs=strict_shared_dirs,
            claude_skills_dir=claude_skills_dir,
            leak_configs=leak_configs,
        )


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
    policy: PolicyConfig = field(default_factory=PolicyConfig.default)


@dataclass
class UpstreamSource:
    """Declared upstream source entry from upstreams.toml."""

    id: str
    repo: str
    ref: str
    commit: str
    group: str
    mode: str
    skills: dict[str, str | list[str]]
    watch_roots: list[str] = field(default_factory=list)
    license_path: str | None = None
    license_dest: str | None = None
    notes: str | None = None


@dataclass
class UpstreamsManifest:
    """Parsed upstreams.toml manifest."""

    sources: dict[str, UpstreamSource] = field(default_factory=dict)
    defaults: dict[str, Any] = field(default_factory=dict)


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


def _escape_toml_str(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _format_toml_key(key: str) -> str:
    if _BARE_KEY_RE.match(key):
        return key
    return f'"{_escape_toml_str(key)}"'


def _format_toml_value(val: Any) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, int):
        return str(val)
    if isinstance(val, str):
        return f'"{_escape_toml_str(val)}"'
    if isinstance(val, list):
        inner = ", ".join(_format_toml_value(item) for item in val)
        return f"[{inner}]"
    raise TypeError(f"Unsupported TOML value type: {type(val).__name__}")


def _unquote_toml_key(token: str) -> str:
    t = token.strip()
    if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
        inner = t[1:-1]
        return (
            inner.replace('\\"', '"')
            .replace("\\n", "\n")
            .replace("\\r", "\r")
            .replace("\\t", "\t")
            .replace("\\\\", "\\")
        )
    if len(t) >= 2 and t[0] == "'" and t[-1] == "'":
        return t[1:-1]
    return t


def _parse_section_header(line: str) -> tuple[str, ...] | None:
    stripped = line.lstrip()
    if not stripped.startswith("[") or stripped.startswith("[["):
        return None
    in_quote: str | None = None
    escaped = False
    end_idx: int | None = None
    for idx in range(1, len(stripped)):
        ch = stripped[idx]
        if in_quote == '"':
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_quote = None
        elif in_quote == "'":
            if ch == "'":
                in_quote = None
        else:
            if ch in ('"', "'"):
                in_quote = ch
            elif ch == "]":
                end_idx = idx
                break
    if end_idx is None:
        return None
    trailing = stripped[end_idx + 1 :].strip()
    if trailing and not trailing.startswith("#"):
        return None

    inner = stripped[1:end_idx]
    parts: list[str] = []
    buf: list[str] = []
    in_quote = None
    escaped = False
    for ch in inner:
        if in_quote == '"':
            buf.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_quote = None
        elif in_quote == "'":
            buf.append(ch)
            if ch == "'":
                in_quote = None
        else:
            if ch in ('"', "'"):
                in_quote = ch
                buf.append(ch)
            elif ch == ".":
                parts.append(_unquote_toml_key("".join(buf)))
                buf = []
            else:
                buf.append(ch)
    parts.append(_unquote_toml_key("".join(buf)))
    return tuple(parts)


def _find_section_bounds(
    lines: list[str],
    predicate: Any,
) -> tuple[int, int] | None:
    """Return (header_line_idx, end_line_idx_exclusive) for first matching TOML section."""
    start_idx: int | None = None
    for idx, line in enumerate(lines):
        header = _parse_section_header(line)
        if header is None:
            continue
        if start_idx is not None:
            return start_idx, idx
        if predicate(header):
            start_idx = idx
    if start_idx is not None:
        return start_idx, len(lines)
    return None


def _parse_kv_line(line: str) -> tuple[str, str, str, str] | None:
    """Return (prefix_up_to_eq_space, key, raw_value_and_comment, trailing_comment) or None."""
    stripped = line.lstrip()
    if not stripped or stripped.startswith("#") or stripped.startswith("["):
        return None
    indent_len = len(line) - len(stripped)
    pos = indent_len
    n = len(line)

    if line[pos] == '"':
        pos += 1
        escaped = False
        while pos < n:
            ch = line[pos]
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                pos += 1
                break
            pos += 1
    elif line[pos] == "'":
        pos += 1
        while pos < n and line[pos] != "'":
            pos += 1
        if pos < n:
            pos += 1
    else:
        while pos < n and (line[pos].isalnum() or line[pos] in "_-"):
            pos += 1

    key_token = line[indent_len:pos]
    if not key_token:
        return None

    rest = line[pos:]
    eq_match = re.match(r"^(\s*=\s*)(.*)$", rest)
    if not eq_match:
        return None

    prefix = line[:pos] + eq_match.group(1)
    rhs = eq_match.group(2)
    comment = ""
    in_quote: str | None = None
    escaped = False
    for idx, ch in enumerate(rhs):
        if in_quote == '"':
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_quote = None
        elif in_quote == "'":
            if ch == "'":
                in_quote = None
        else:
            if ch in ('"', "'"):
                in_quote = ch
            elif ch == "#":
                val_part = rhs[:idx]
                ws_tail = len(val_part) - len(val_part.rstrip())
                comment = rhs[idx - ws_tail :]
                rhs = val_part.rstrip()
                break
    return prefix, _unquote_toml_key(key_token), rhs.rstrip(), comment


def _split_lines_preserving_eol(text: str) -> tuple[list[str], bool]:
    had_trailing_nl = text.endswith("\n")
    lines = text.splitlines()
    return lines, had_trailing_nl


def _join_lines_preserving_eol(lines: list[str], trailing_nl: bool = True) -> str:
    body = "\n".join(lines)
    return f"{body}\n" if (trailing_nl or lines) and body else body


def _verify_and_write_toml(toml_path: Path, content: str) -> None:
    tomllib.loads(content)
    toml_path.parent.mkdir(parents=True, exist_ok=True)
    toml_path.write_text(content, encoding="utf-8")


def _format_source_block(source: UpstreamSource) -> list[str]:
    lines = [
        f"[sources.{_format_toml_key(source.id)}]",
        f'repo = "{_escape_toml_str(source.repo)}"',
        f'ref = "{_escape_toml_str(source.ref)}"',
        f'commit = "{_escape_toml_str(source.commit)}"',
        f'group = "{_escape_toml_str(source.group)}"',
        f'mode = "{_escape_toml_str(source.mode)}"',
    ]
    if source.watch_roots:
        lines.append(f"watch_roots = {_format_toml_value(source.watch_roots)}")
    if source.license_path is not None:
        lines.append(f'license_path = "{_escape_toml_str(source.license_path)}"')
    if source.license_dest is not None:
        lines.append(f'license_dest = "{_escape_toml_str(source.license_dest)}"')
    if source.notes is not None:
        lines.append(f'notes = "{_escape_toml_str(source.notes)}"')
    lines.append("")
    lines.append(f"[sources.{_format_toml_key(source.id)}.skills]")
    for skill_name, upstream_path in source.skills.items():
        lines.append(f"{_format_toml_key(skill_name)} = {_format_toml_value(upstream_path)}")
    return lines


def _last_content_line_in_section(lines: list[str], start: int, end: int) -> int:
    last_kv = start
    for idx in range(start + 1, end):
        if _parse_kv_line(lines[idx]) is not None:
            last_kv = idx
    return last_kv


def _format_skill_row(
    skill_name: str,
    row: list[int],
    *,
    key_width: int,
    spaced_array: bool,
) -> str:
    key_str = _format_toml_key(skill_name)
    if spaced_array:
        cells = ", ".join(f"{val:>2}" for val in row)
        arr = f"[ {cells} ]"
    else:
        cells = ", ".join(str(val) for val in row)
        arr = f"[{cells}]"
    return f"{key_str:<{key_width}} = {arr}"


def _format_column_header_comment(columns: list[str], key_width: int) -> str:
    abbrevs = [_COLUMN_ABBREVS.get(c, c[:3]) for c in columns]
    prefix = f"# {' ' * (key_width + 3)}"
    # Align column abbreviations over 2-char right-aligned cells separated by ", " (stride 4).
    parts: list[str] = []
    for idx, abbr in enumerate(abbrevs):
        if idx < len(abbrevs) - 1:
            parts.append(f"{abbr:<4}")
        else:
            parts.append(abbr)
    return prefix + "".join(parts).rstrip()


class ManifestStore:
    """Read and surgically update skills.toml and upstreams.toml."""

    @staticmethod
    def load_skills_manifest(repo_root: Path, home: Path | None = None) -> Manifest:
        """Load and normalize `<repo_root>/skills.toml`."""
        toml_path = repo_root / "skills.toml"
        with toml_path.open("rb") as fh:
            raw = tomllib.load(fh)

        canon_raw = raw.get("canonical_root")
        canonical_root = _expand_path(canon_raw, home) if isinstance(canon_raw, str) else None
        columns = list(raw.get("columns", []))
        targets = {
            k: _expand_path(v, home)
            for k, v in raw.get("targets", {}).items()
            if isinstance(v, str)
        }
        requires = {
            k: list(v)
            for k, v in raw.get("requires", {}).items()
            if isinstance(v, list)
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

        policy = PolicyConfig.from_dict(raw.get("policy"), home=home)

        return Manifest(
            canonical_root=canonical_root,
            columns=columns,
            targets=targets,
            requires=requires,
            groups=groups,
            declared=declared,
            duplicate_declared=duplicates,
            policy=policy,
        )

    @staticmethod
    def load_upstreams_manifest(
        repo_root: Path,
        *,
        allow_missing: bool = False,
    ) -> UpstreamsManifest:
        """Load and normalize `<repo_root>/upstreams.toml`."""
        toml_path = repo_root / "upstreams.toml"
        if not toml_path.is_file():
            if allow_missing:
                return UpstreamsManifest()
            raise FileNotFoundError(f"upstreams.toml not found: {toml_path}")

        with toml_path.open("rb") as fh:
            raw = tomllib.load(fh)

        defaults_raw = raw.get("defaults", {})
        defaults = dict(defaults_raw) if isinstance(defaults_raw, dict) else {}

        sources: dict[str, UpstreamSource] = {}
        sources_raw = raw.get("sources", {})
        if not isinstance(sources_raw, dict):
            raise ValueError("upstreams.toml [sources] must be a table")

        for sid, sdata in sources_raw.items():
            if not isinstance(sdata, dict):
                raise ValueError(f"upstreams.toml [sources.{sid}] must be a table")
            skills_raw = sdata.get("skills", {})
            if not isinstance(skills_raw, dict):
                raise ValueError(f"upstreams.toml [sources.{sid}.skills] must be a table")
            skills: dict[str, str | list[str]] = {}
            for k, v in skills_raw.items():
                if isinstance(v, list):
                    skills[str(k)] = [str(item) for item in v]
                else:
                    skills[str(k)] = v if isinstance(v, str) else str(v)

            watch_raw = sdata.get("watch_roots", [])
            watch_roots = (
                [str(x) for x in watch_raw] if isinstance(watch_raw, list) else []
            )

            sources[str(sid)] = UpstreamSource(
                id=str(sid),
                repo=str(sdata.get("repo", "")),
                ref=str(sdata.get("ref", "")),
                commit=str(sdata.get("commit", "")),
                group=str(sdata.get("group", "")),
                mode=str(sdata.get("mode", "")),
                skills=skills,
                watch_roots=watch_roots,
                license_path=sdata.get("license_path"),
                license_dest=sdata.get("license_dest"),
                notes=sdata.get("notes"),
            )

        return UpstreamsManifest(sources=sources, defaults=defaults)

    @staticmethod
    def update_upstream_commit(toml_path: Path, source_id: str, new_commit: str) -> None:
        """Surgically update `commit = "..."` in `[sources.<source_id>]`."""
        text = toml_path.read_text(encoding="utf-8")
        lines, had_nl = _split_lines_preserving_eol(text)
        bounds = _find_section_bounds(lines, lambda h: h == ("sources", source_id))
        if bounds is None:
            raise KeyError(f"Section [sources.{source_id}] not found in {toml_path}")
        start, end = bounds

        commit_val = f'"{_escape_toml_str(new_commit)}"'
        replaced = False
        ref_idx: int | None = None
        for idx in range(start + 1, end):
            parsed = _parse_kv_line(lines[idx])
            if parsed is None:
                continue
            prefix, key, _, comment = parsed
            if key == "ref":
                ref_idx = idx
            elif key == "commit":
                lines[idx] = f"{prefix}{commit_val}{comment}"
                replaced = True
                break

        if not replaced:
            insert_at = (ref_idx + 1) if ref_idx is not None else (_last_content_line_in_section(lines, start, end) + 1)
            lines.insert(insert_at, f"commit = {commit_val}")

        updated = _join_lines_preserving_eol(lines, had_nl)
        _verify_and_write_toml(toml_path, updated)

    @staticmethod
    def upsert_upstream_source(toml_path: Path, source: UpstreamSource) -> None:
        """Insert or update `[sources.<id>]` and `[sources.<id>.skills]` preserving formatting."""
        if not toml_path.exists() or not toml_path.read_text(encoding="utf-8").strip():
            block = _format_source_block(source)
            _verify_and_write_toml(toml_path, _join_lines_preserving_eol(block, True))
            return

        text = toml_path.read_text(encoding="utf-8")
        lines, had_nl = _split_lines_preserving_eol(text)
        src_bounds = _find_section_bounds(lines, lambda h: h == ("sources", source.id))

        if src_bounds is None:
            while lines and lines[-1] == "":
                lines.pop()
            if lines:
                lines.append("")
            lines.extend(_format_source_block(source))
            _verify_and_write_toml(toml_path, _join_lines_preserving_eol(lines, True))
            return

        start, end = src_bounds
        updates: dict[str, Any] = {
            "repo": source.repo,
            "ref": source.ref,
            "commit": source.commit,
            "group": source.group,
            "mode": source.mode,
        }
        if source.watch_roots:
            updates["watch_roots"] = source.watch_roots
        if source.license_path is not None:
            updates["license_path"] = source.license_path
        if source.license_dest is not None:
            updates["license_dest"] = source.license_dest
        if source.notes is not None:
            updates["notes"] = source.notes

        seen_keys: set[str] = set()
        for idx in range(start + 1, end):
            parsed = _parse_kv_line(lines[idx])
            if parsed is None:
                continue
            prefix, key, _, comment = parsed
            seen_keys.add(key)
            if key in updates:
                lines[idx] = f"{prefix}{_format_toml_value(updates[key])}{comment}"

        missing_keys = [k for k in updates if k not in seen_keys]
        if missing_keys:
            insert_at = _last_content_line_in_section(lines, start, end) + 1
            for k in missing_keys:
                lines.insert(insert_at, f"{_format_toml_key(k)} = {_format_toml_value(updates[k])}")
                insert_at += 1

        skills_bounds = _find_section_bounds(
            lines, lambda h: h == ("sources", source.id, "skills")
        )
        if skills_bounds is None:
            # Recompute source bounds after any insertions.
            src_bounds = _find_section_bounds(lines, lambda h: h == ("sources", source.id))
            assert src_bounds is not None
            insert_at = _last_content_line_in_section(lines, src_bounds[0], src_bounds[1]) + 1
            new_lines = ["", f"[sources.{_format_toml_key(source.id)}.skills]"]
            for skill_name, upstream_path in source.skills.items():
                new_lines.append(
                    f"{_format_toml_key(skill_name)} = {_format_toml_value(upstream_path)}"
                )
            for offset, new_line in enumerate(new_lines):
                lines.insert(insert_at + offset, new_line)
        else:
            s_start, s_end = skills_bounds
            existing_skills: set[str] = set()
            for idx in range(s_start + 1, s_end):
                parsed = _parse_kv_line(lines[idx])
                if parsed is not None:
                    existing_skills.add(parsed[1])
            insert_at = _last_content_line_in_section(lines, s_start, s_end) + 1
            for skill_name, upstream_path in source.skills.items():
                if skill_name not in existing_skills:
                    lines.insert(
                        insert_at,
                        f"{_format_toml_key(skill_name)} = {_format_toml_value(upstream_path)}",
                    )
                    insert_at += 1

        updated = _join_lines_preserving_eol(lines, had_nl)
        _verify_and_write_toml(toml_path, updated)

    @staticmethod
    def append_skill_rows(
        toml_path: Path,
        group: str,
        rows: dict[str, list[int]],
        columns: list[str],
    ) -> None:
        """Insert skill matrix rows into `[skills."<group>"]` preserving formatting."""
        if not rows:
            return
        text = toml_path.read_text(encoding="utf-8")
        lines, had_nl = _split_lines_preserving_eol(text)

        # Detect global file padding conventions for skills tables.
        file_key_width = 29
        spaced_array = True
        int_array_re = re.compile(r"^\[\s*\d+(?:\s*,\s*\d+)*\s*\]$")
        for line in lines:
            parsed = _parse_kv_line(line)
            if parsed is None:
                continue
            prefix, _, rhs, _ = parsed
            if int_array_re.match(rhs) and "=" in prefix:
                before_eq = prefix.split("=", 1)[0]
                eq_width = len(before_eq[:-1]) if before_eq.endswith(" ") else len(before_eq)
                file_key_width = max(file_key_width, eq_width)
                if not rhs.startswith("[ "):
                    spaced_array = False

        bounds = _find_section_bounds(
            lines,
            lambda h: len(h) >= 2 and h[0] == "skills" and "/".join(h[1:]) == group,
        )

        if bounds is None:
            key_width = max([file_key_width, *(len(_format_toml_key(k)) for k in rows)])
            while lines and lines[-1] == "":
                lines.pop()
            if lines:
                lines.append("")
            lines.append(_format_column_header_comment(columns, key_width))
            lines.append(f'[skills."{_escape_toml_str(group)}"]')
            for skill_name, row in rows.items():
                lines.append(
                    _format_skill_row(
                        skill_name,
                        row,
                        key_width=key_width,
                        spaced_array=spaced_array,
                    )
                )
            _verify_and_write_toml(toml_path, _join_lines_preserving_eol(lines, True))
            return

        start, end = bounds
        sec_key_width = 0
        sec_spaced: bool | None = None
        existing_indices: dict[str, int] = {}
        for idx in range(start + 1, end):
            parsed = _parse_kv_line(lines[idx])
            if parsed is None:
                continue
            prefix, key, rhs, _ = parsed
            existing_indices[key] = idx
            before_eq = prefix.split("=", 1)[0]
            eq_width = len(before_eq[:-1]) if before_eq.endswith(" ") else len(before_eq)
            sec_key_width = max(sec_key_width, eq_width)
            if int_array_re.match(rhs) and sec_spaced is None:
                sec_spaced = rhs.startswith("[ ")

        key_width = max(
            [sec_key_width or file_key_width, *(len(_format_toml_key(k)) for k in rows)]
        )
        use_spaced = spaced_array if sec_spaced is None else sec_spaced

        insert_at = _last_content_line_in_section(lines, start, end) + 1
        for skill_name, row in rows.items():
            formatted = _format_skill_row(
                skill_name,
                row,
                key_width=key_width,
                spaced_array=use_spaced,
            )
            if skill_name in existing_indices:
                lines[existing_indices[skill_name]] = formatted
            else:
                lines.insert(insert_at, formatted)
                insert_at += 1

        updated = _join_lines_preserving_eol(lines, had_nl)
        _verify_and_write_toml(toml_path, updated)


def load_manifest(repo_root: Path, home: Path | None = None) -> Manifest:
    """Convenience wrapper around `ManifestStore.load_skills_manifest`."""
    return ManifestStore.load_skills_manifest(repo_root, home=home)

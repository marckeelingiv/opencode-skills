"""Stdlib-only YAML frontmatter, openai.yaml, and skill discovery utilities."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .config import SkillInfo

_BLOCK_RE = re.compile(r"^([|>])([+-])?(\d+)?(?:\s+#.*)?$")
_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+):(?:\s+(.*)|)$")


def _match_complete_quoted(text: str) -> tuple[bool, str]:
    """Return (is_complete_quoted, inner_unquoted_text) for YAML single/double quotes."""
    if len(text) < 2:
        return False, text
    quote = text[0]
    if quote not in ('"', "'"):
        return False, text

    if quote == '"':
        escaped = False
        for idx in range(1, len(text)):
            ch = text[idx]
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                if idx == len(text) - 1:
                    inner = text[1:idx]
                    return True, (
                        inner.replace('\\"', '"')
                        .replace("\\n", "\n")
                        .replace("\\t", "\t")
                        .replace("\\\\", "\\")
                    )
                return False, text
        return False, text

    # Single-quoted YAML string: '' represents a literal single quote.
    idx = 1
    while idx < len(text):
        if text[idx] == "'":
            if idx + 1 < len(text) and text[idx + 1] == "'":
                idx += 2
                continue
            if idx == len(text) - 1:
                return True, text[1:idx].replace("''", "'")
            return False, text
        idx += 1
    return False, text


def _parse_scalar(raw: str) -> Any:
    stripped = raw.strip()
    if not stripped:
        return ""
    is_quoted, unquoted = _match_complete_quoted(stripped)
    if is_quoted:
        return unquoted
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


def _bracket_balance(text: str, *, in_str: str | None = None, escaped: bool = False) -> tuple[int, str | None, bool]:
    """Compute net `{`/`[` minus `}`/`]` depth change across `text`."""
    delta = 0
    for ch in text:
        if in_str == '"':
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = None
        elif in_str == "'":
            if ch == "'":
                in_str = None
        else:
            if ch in ('"', "'"):
                in_str = ch
            elif ch in ("{", "["):
                delta += 1
            elif ch in ("}", "]"):
                delta -= 1
    return delta, in_str, escaped


def _consume_bracket_block(
    lines: list[str],
    start_idx: int,
    initial_text: str = "",
) -> tuple[Any, int]:
    """Consume a JSON/flow object or array block across lines until brackets balance."""
    collected: list[str] = []
    depth = 0
    in_str: str | None = None
    escaped = False

    if initial_text:
        collected.append(initial_text)
        delta, in_str, escaped = _bracket_balance(initial_text, in_str=in_str, escaped=escaped)
        depth += delta

    idx = start_idx
    while (depth > 0 or not collected) and idx < len(lines):
        line = lines[idx]
        collected.append(line)
        delta, in_str, escaped = _bracket_balance(line, in_str=in_str, escaped=escaped)
        depth += delta
        idx += 1
        if depth <= 0 and collected:
            break

    raw_block = "\n".join(collected).strip()
    try:
        return json.loads(raw_block), idx
    except json.JSONDecodeError:
        return raw_block, idx


def _parse_indented_mapping(lines: list[str]) -> dict[str, Any]:
    """Parse an indented YAML sub-mapping into a dict."""
    non_empty = [ln for ln in lines if ln.strip()]
    if not non_empty:
        return {}
    min_indent = min(len(ln) - len(ln.lstrip(" ")) for ln in non_empty)
    dedented = [ln[min_indent:] if len(ln) >= min_indent else ln for ln in lines]
    return _parse_yaml_lines(dedented)


def _parse_yaml_lines(fm_lines: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    i = 0

    while i < len(fm_lines):
        line = fm_lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if line[0].isspace():
            i += 1
            continue
        m = _KEY_RE.match(line)
        if not m:
            i += 1
            continue

        key = m.group(1)
        raw_val = (m.group(2) or "").rstrip()
        block_match = _BLOCK_RE.match(raw_val)

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
            trailing_nl = "\n" if raw_block else ""
            result[key] = _apply_chomp(joined + trailing_nl, chomp)
            continue

        # Multiline or single-line JSON/flow object or array starting on the key line.
        if raw_val.startswith(("{", "[")):
            parsed_obj, next_i = _consume_bracket_block(fm_lines, i + 1, raw_val)
            result[key] = parsed_obj
            i = next_i
            continue

        # Empty value on key line: check for indented JSON/YAML sub-block.
        if not raw_val:
            j = i + 1
            while j < len(fm_lines) and not fm_lines[j].strip():
                j += 1
            if j < len(fm_lines) and fm_lines[j][0].isspace():
                first_stripped = fm_lines[j].lstrip()
                if first_stripped.startswith(("{", "[")):
                    parsed_obj, next_i = _consume_bracket_block(fm_lines, j)
                    result[key] = parsed_obj
                    i = next_i
                    continue

                sub_lines: list[str] = []
                k = j
                while k < len(fm_lines):
                    nxt = fm_lines[k]
                    if not nxt.strip():
                        sub_lines.append("")
                        k += 1
                        continue
                    if nxt[0].isspace():
                        sub_lines.append(nxt)
                        k += 1
                        continue
                    break

                if first_stripped.startswith("- "):
                    items: list[Any] = []
                    for sl in sub_lines:
                        s_item = sl.strip()
                        if s_item.startswith("- "):
                            items.append(_parse_scalar(s_item[2:]))
                    result[key] = items
                else:
                    result[key] = _parse_indented_mapping(sub_lines)
                i = k
                continue

            result[key] = ""
            i += 1
            continue

        # Quoted scalar on single line (or multiline quoted scalar).
        is_complete, unquoted = _match_complete_quoted(raw_val)
        if is_complete:
            result[key] = unquoted
            i += 1
            continue

        # Handle indented continuation lines on unquoted (or multiline quoted) scalars.
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
            joined_scalar = " ".join(parts)
            is_complete_multi, unquoted_multi = _match_complete_quoted(joined_scalar)
            result[key] = unquoted_multi if is_complete_multi else joined_scalar
        else:
            result[key] = _parse_scalar(parts[0])

    return result


class FrontmatterParser:
    """Stdlib-only parser for SKILL.md YAML frontmatter and agents/openai.yaml."""

    @staticmethod
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

        return _parse_yaml_lines(lines[1:end_idx])

    @staticmethod
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


class SkillDiscovery:
    """Discover skill directories and parse their metadata."""

    @staticmethod
    def discover(repo_root: Path) -> list[SkillInfo]:
        """Discover all SKILL.md directories under `<repo_root>/skills/`."""
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
                fm = FrontmatterParser.parse_frontmatter(
                    skill_md.read_text(encoding="utf-8")
                )
            except (OSError, ValueError) as exc:
                err = str(exc)

            openai_cfg = FrontmatterParser.parse_openai_yaml(
                skill_dir / "agents" / "openai.yaml"
            )
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


def parse_frontmatter(text: str) -> dict[str, Any]:
    """Module-level wrapper for `FrontmatterParser.parse_frontmatter`."""
    return FrontmatterParser.parse_frontmatter(text)


def parse_openai_yaml(path: Path) -> dict[str, Any]:
    """Module-level wrapper for `FrontmatterParser.parse_openai_yaml`."""
    return FrontmatterParser.parse_openai_yaml(path)


def discover_skills(repo_root: Path) -> list[SkillInfo]:
    """Module-level wrapper for `SkillDiscovery.discover`."""
    return SkillDiscovery.discover(repo_root)

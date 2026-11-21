"""Static leak audit and live harness discovery verifiers."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .config import Manifest, SkillInfo
from .syncer import PathOwnership
from .validator import RepoValidator


def _path_matches_or_inside(candidate: Path, parent: Path) -> bool:
    try:
        return candidate == parent or candidate.is_relative_to(parent)
    except ValueError:
        return False


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
                        expanded = (
                            Path(roots[alias]) / rest
                            if alias in roots
                            else Path(raw_file)
                        )
                    else:
                        expanded = (
                            Path(roots[raw_file])
                            if raw_file in roots
                            else Path(raw_file)
                        )
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


@dataclass
class VerifyContext:
    """Shared input context passed to all harness verifiers."""

    repo_root: Path
    manifest: Manifest
    disk_skills: dict[str, SkillInfo]
    home: Path


@dataclass
class VerificationResult:
    """Outcome of a single harness verifier."""

    label: str
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class HarnessVerifier(Protocol):
    """Protocol implemented by static and live harness verifiers."""

    label: str

    def verify(self, ctx: VerifyContext) -> VerificationResult: ...


class StaticLeakVerifier:
    """V1 static symlink, leak config, and shared-dir policy audit."""

    label = "V1 (Static & Leak Audit)"

    def verify(self, ctx: VerifyContext) -> VerificationResult:
        failures: list[str] = []
        warnings: list[str] = []
        manifest = ctx.manifest
        policy = manifest.policy

        failures.extend(RepoValidator.validate_all(ctx.repo_root))

        roots = PathOwnership.roots_for_manifest(ctx.repo_root, manifest)

        for col_idx, target_name in enumerate(manifest.columns):
            target_dir = manifest.targets[target_name]
            if target_dir.is_symlink():
                failures.append(f"Target '{target_name}' is a symlink: {target_dir}")
                continue
            if not target_dir.exists() or not target_dir.is_dir():
                failures.append(
                    f"Target '{target_name}' directory does not exist: {target_dir}"
                )
                continue

            for skill_name, (group, row) in manifest.declared.items():
                if not isinstance(row, list) or col_idx >= len(row):
                    continue
                dest = target_dir / skill_name
                source = (
                    ctx.repo_root / "skills" / group / skill_name
                    if group
                    else ctx.repo_root / "skills" / skill_name
                )
                if row[col_idx] == 1:
                    if not PathOwnership.is_repo_owned(dest, roots):
                        failures.append(
                            f"[{target_name}] '{skill_name}' missing repo-owned symlink at {dest}"
                        )
                    else:
                        expected_skill_md = (source / "SKILL.md").resolve(strict=False)
                        actual_skill_md = (dest / "SKILL.md").resolve(strict=False)
                        if (
                            not (dest / "SKILL.md").is_file()
                            or actual_skill_md != expected_skill_md
                        ):
                            failures.append(
                                f"[{target_name}] '{skill_name}' symlink resolves to {actual_skill_md}, expected {expected_skill_md}"
                            )
                else:
                    if PathOwnership.is_repo_owned(dest, roots):
                        failures.append(
                            f"[{target_name}] '{skill_name}' is 0 in matrix but has repo symlink at {dest}"
                        )

        check_roots = [
            Path(os.path.abspath(os.fspath(ctx.repo_root))),
            ctx.repo_root.resolve(strict=False),
        ]
        if manifest.canonical_root is not None:
            check_roots.append(
                Path(os.path.abspath(os.fspath(manifest.canonical_root)))
            )
            check_roots.append(manifest.canonical_root.resolve(strict=False))

        def points_into_repo(raw_path: str) -> bool:
            p = Path(raw_path).expanduser()
            cands = [Path(os.path.abspath(os.fspath(p))), p.resolve(strict=False)]
            return any(_path_matches_or_inside(c, r) for c in cands for r in check_roots)

        for spec in policy.leak_configs:
            cfg_path = spec.path
            if cfg_path.is_file():
                try:
                    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                    if isinstance(cfg, dict):
                        for key in spec.keys:
                            for p in _extract_json_paths(cfg.get(key)):
                                if points_into_repo(p):
                                    failures.append(
                                        f"{cfg_path} '{key}' points into repo: {p}"
                                    )
                except (OSError, json.JSONDecodeError) as exc:
                    failures.append(f"Failed to parse {cfg_path}: {exc}")

        strict_abs = {
            Path(os.path.abspath(os.fspath(p))) for p in policy.strict_shared_dirs
        }

        for strict_dir in policy.strict_shared_dirs:
            if strict_dir.exists() and strict_dir.is_dir():
                for entry in sorted(strict_dir.iterdir(), key=lambda p: p.name):
                    if entry.name in policy.orca_skills:
                        continue
                    if PathOwnership.classify_unmanaged(entry, policy) == "omarchy":
                        continue
                    failures.append(
                        f"Unexpected non-Omarchy/non-Orca entry in {strict_dir}: {entry.name}"
                    )

        if "antigravity" in manifest.columns:
            agy_idx = manifest.columns.index("antigravity")
            agy_enabled = {
                name
                for name, (_, row) in manifest.declared.items()
                if isinstance(row, list) and row[agy_idx] == 1
            }
            for shared_dir in policy.shared_dirs:
                if Path(os.path.abspath(os.fspath(shared_dir))) in strict_abs:
                    continue
                if shared_dir.exists() and shared_dir.is_dir():
                    for entry in sorted(shared_dir.iterdir(), key=lambda p: p.name):
                        if entry.name in agy_enabled:
                            failures.append(
                                f"{shared_dir}/{entry.name} collides with skill managed in antigravity target"
                            )

        if "codex" in manifest.columns:
            codex_idx = manifest.columns.index("codex")
            for skill_name, (_, row) in manifest.declared.items():
                info = ctx.disk_skills.get(skill_name)
                if info and isinstance(row, list) and row[codex_idx] == 1:
                    if info.disable_model_invocation and info.allow_implicit_invocation:
                        warnings.append(
                            f"'{skill_name}' has disable-model-invocation: true and codex=1 without agents/openai.yaml allow_implicit_invocation: false"
                        )

        return VerificationResult(
            label=self.label,
            failures=failures,
            warnings=warnings,
        )


class CodexVerifier:
    """V2 live Codex prompt-input discovery verifier."""

    label = "V2 (Codex live discovery)"

    def verify(self, ctx: VerifyContext) -> VerificationResult:
        failures: list[str] = []
        manifest = ctx.manifest
        if "codex" not in manifest.columns:
            return VerificationResult(
                label=self.label,
                failures=["'codex' column missing from skills.toml"],
            )
        if not shutil.which("codex"):
            return VerificationResult(
                label=self.label,
                failures=["'codex' CLI not found on PATH"],
            )

        try:
            proc = subprocess.run(
                ["codex", "debug", "prompt-input"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return VerificationResult(
                label=self.label,
                failures=[f"Failed to run codex debug prompt-input: {exc}"],
            )

        if proc.returncode != 0:
            return VerificationResult(
                label=self.label,
                failures=[
                    f"codex debug prompt-input exited {proc.returncode}: {proc.stderr.strip()}"
                ],
            )

        try:
            data = json.loads(proc.stdout)
            _, available = parse_codex_prompt_input(data)
        except (json.JSONDecodeError, ValueError) as exc:
            return VerificationResult(
                label=self.label,
                failures=[f"Failed to parse codex prompt-input JSON: {exc}"],
            )

        by_name: dict[str, list[dict[str, Any]]] = {}
        for entry in available:
            by_name.setdefault(entry["name"], []).append(entry)

        for name, entries in sorted(by_name.items()):
            if len(entries) > 1:
                files = ", ".join(e["file"] for e in entries)
                failures.append(f"Duplicate Codex available skill '{name}': {files}")

        codex_idx = manifest.columns.index("codex")
        for skill_name, (group, row) in sorted(manifest.declared.items()):
            info = ctx.disk_skills.get(skill_name)
            enabled = isinstance(row, list) and row[codex_idx] == 1
            expected_skill_md = (
                (ctx.repo_root / "skills" / group / skill_name / "SKILL.md")
                if group
                else (ctx.repo_root / "skills" / skill_name / "SKILL.md")
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
                        failures.append(
                            f"Codex skill '{skill_name}' (codex=1) missing from Available skills"
                        )
                    elif not any(e["resolved_path"] == expected_skill_md for e in found):
                        actual = ", ".join(str(e["resolved_path"]) for e in found)
                        failures.append(
                            f"Codex skill '{skill_name}' resolved to {actual}, expected {expected_skill_md}"
                        )
            else:
                if found:
                    failures.append(
                        f"Codex skill '{skill_name}' (codex=0) unexpectedly present in Available skills"
                    )

        return VerificationResult(label=self.label, failures=failures)


class OpenCodeVerifier:
    """V3 live OpenCode skill.list discovery verifier."""

    label = "V3 (OpenCode live discovery)"

    def verify(self, ctx: VerifyContext) -> VerificationResult:
        failures: list[str] = []
        manifest = ctx.manifest
        if "opencode" not in manifest.columns:
            return VerificationResult(
                label=self.label,
                failures=["'opencode' column missing from skills.toml"],
            )
        if not shutil.which("opencode"):
            return VerificationResult(
                label=self.label,
                failures=["'opencode' CLI not found on PATH"],
            )

        try:
            proc = subprocess.run(
                ["opencode", "api", "skill.list"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return VerificationResult(
                label=self.label,
                failures=[f"Failed to run opencode api skill.list: {exc}"],
            )

        if proc.returncode != 0:
            return VerificationResult(
                label=self.label,
                failures=[
                    f"opencode api skill.list exited {proc.returncode}: {proc.stderr.strip()}"
                ],
            )

        try:
            records = parse_opencode_skill_list(json.loads(proc.stdout))
        except (json.JSONDecodeError, ValueError) as exc:
            return VerificationResult(
                label=self.label,
                failures=[f"Failed to parse opencode skill.list JSON: {exc}"],
            )

        claude_skills = manifest.policy.claude_skills_dir
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
                        f"OpenCode loaded skill '{name}' from {claude_skills}: {rec['location']}"
                    )

        oc_idx = manifest.columns.index("opencode")
        for skill_name, (group, row) in sorted(manifest.declared.items()):
            enabled = isinstance(row, list) and row[oc_idx] == 1
            expected_skill_md = (
                (ctx.repo_root / "skills" / group / skill_name / "SKILL.md")
                if group
                else (ctx.repo_root / "skills" / skill_name / "SKILL.md")
            ).resolve(strict=False)
            rec = by_name.get(skill_name)
            if enabled:
                if rec is None:
                    failures.append(
                        f"OpenCode skill '{skill_name}' (opencode=1) not loaded"
                    )
                elif rec["resolved_path"] != expected_skill_md:
                    failures.append(
                        f"OpenCode skill '{skill_name}' resolved to {rec['resolved_path']}, expected {expected_skill_md}"
                    )
            else:
                if rec is not None:
                    failures.append(
                        f"OpenCode skill '{skill_name}' (opencode=0) unexpectedly loaded from {rec['location']}"
                    )

        return VerificationResult(label=self.label, failures=failures)


class AntigravityVerifier:
    """V4 live Antigravity CLI skill discovery verifier."""

    label = "V4 (Antigravity live discovery)"

    def verify(self, ctx: VerifyContext) -> VerificationResult:
        failures: list[str] = []
        manifest = ctx.manifest
        if "antigravity" not in manifest.columns:
            return VerificationResult(
                label=self.label,
                failures=["'antigravity' column missing from skills.toml"],
            )
        if not shutil.which("agy"):
            return VerificationResult(
                label=self.label,
                failures=["'agy' CLI not found on PATH"],
            )

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
            return VerificationResult(
                label=self.label,
                failures=[f"Failed to run agy live discovery: {exc}"],
            )

        if proc.returncode != 0:
            return VerificationResult(
                label=self.label,
                failures=[f"agy exited {proc.returncode}: {proc.stderr.strip()}"],
            )

        listed = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
        agy_idx = manifest.columns.index("antigravity")
        for skill_name, (_, row) in sorted(manifest.declared.items()):
            enabled = isinstance(row, list) and row[agy_idx] == 1
            info = ctx.disk_skills.get(skill_name)
            if enabled:
                if info and info.disable_model_invocation:
                    continue
                if skill_name not in listed:
                    failures.append(
                        f"Antigravity skill '{skill_name}' (antigravity=1) missing from agy output"
                    )
            else:
                if skill_name in listed:
                    failures.append(
                        f"Antigravity skill '{skill_name}' (antigravity=0) unexpectedly present in agy output"
                    )

        return VerificationResult(label=self.label, failures=failures)


class VerificationRunner:
    """Execute selected harness verifiers and report results."""

    def __init__(self, verifiers: list[HarnessVerifier] | None = None) -> None:
        self.verifiers: list[HarnessVerifier] = (
            list(verifiers)
            if verifiers is not None
            else [
                StaticLeakVerifier(),
                CodexVerifier(),
                OpenCodeVerifier(),
            ]
        )

    @staticmethod
    def report(result: VerificationResult) -> bool:
        for w in result.warnings:
            print(f"[WARN] {result.label}: {w}")
        if result.failures:
            for f in result.failures:
                print(f"[FAIL] {result.label}: {f}")
            return False
        print(f"[PASS] {result.label}")
        return True

    def run(
        self,
        ctx: VerifyContext,
        verifiers: list[HarnessVerifier] | None = None,
    ) -> int:
        selected = verifiers if verifiers is not None else self.verifiers
        ok = True
        for verifier in selected:
            res = verifier.verify(ctx)
            ok &= self.report(res)
        return 0 if ok else 1


def check_v2_codex(
    repo_root: Path,
    manifest: Manifest,
    disk_skills: dict[str, SkillInfo],
) -> list[str]:
    """Compatibility helper executing `CodexVerifier`."""
    ctx = VerifyContext(
        repo_root=repo_root,
        manifest=manifest,
        disk_skills=disk_skills,
        home=Path.home(),
    )
    return CodexVerifier().verify(ctx).failures


def check_v4_agy(
    manifest: Manifest,
    disk_skills: dict[str, SkillInfo],
) -> list[str]:
    """Compatibility helper executing `AntigravityVerifier`."""
    ctx = VerifyContext(
        repo_root=Path.cwd(),
        manifest=manifest,
        disk_skills=disk_skills,
        home=Path.home(),
    )
    return AntigravityVerifier().verify(ctx).failures

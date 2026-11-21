#!/usr/bin/env python3
"""Offline unit tests for upstream check, update, merge, add, validation, and CLI JSON."""

from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from skillctl import cli  # noqa: E402
from skillctl.config import ManifestStore  # noqa: E402
from skillctl.github import (  # noqa: E402
    CommitSummary,
    PathCompareResult,
    RepoInfo,
    UpstreamTransportError,
    normalize_repo_path,
    path_matches_prefix,
)
from skillctl.upstreams import UpstreamManager  # noqa: E402
from skillctl.validator import RepoValidator  # noqa: E402


class FakeUpstreamTransport:
    """In-memory UpstreamTransport for offline testing."""

    def __init__(self) -> None:
        self.repo_info: dict[tuple[str, str | None], RepoInfo] = {}
        self.head_commits: dict[tuple[str, str], str] = {}
        self.trees: dict[tuple[str, str], dict[str, tuple[bytes, int]]] = {}
        self.compare_commits_log: dict[
            tuple[str, str, str], list[CommitSummary]
        ] = {}
        self.truncated_pairs: set[tuple[str, str, str]] = set()
        self.errors: dict[tuple[str, str], str] = {}

    def set_repo(
        self,
        repo: str,
        ref: str,
        head_commit: str,
        *,
        default_branch: str = "main",
        license_spdx: str | None = "MIT",
    ) -> None:
        self.head_commits[(repo, ref)] = head_commit
        self.repo_info[(repo, ref)] = RepoInfo(
            repo=repo,
            default_branch=default_branch,
            head_commit=head_commit,
            license_spdx=license_spdx,
        )
        self.repo_info[(repo, None)] = RepoInfo(
            repo=repo,
            default_branch=default_branch,
            head_commit=head_commit,
            license_spdx=license_spdx,
        )

    def set_tree(
        self,
        repo: str,
        commit: str,
        files: dict[str, str | bytes | tuple[str | bytes, int]],
    ) -> None:
        tree: dict[str, tuple[bytes, int]] = {}
        for rel, spec in files.items():
            if isinstance(spec, tuple):
                raw, mode = spec
            else:
                raw, mode = spec, 0o644
            data = raw.encode("utf-8") if isinstance(raw, str) else raw
            tree[normalize_repo_path(rel)] = (data, mode)
        self.trees[(repo, commit)] = tree

    def get_repo_info(self, repo: str, ref: str | None = None) -> RepoInfo:
        if ("get_repo_info", repo) in self.errors:
            raise UpstreamTransportError(self.errors[("get_repo_info", repo)])
        if (repo, ref) in self.repo_info:
            return self.repo_info[(repo, ref)]
        if (repo, None) in self.repo_info:
            info = self.repo_info[(repo, None)]
            target_ref = ref or info.default_branch
            return RepoInfo(
                repo=repo,
                default_branch=info.default_branch,
                head_commit=self.get_head_commit(repo, target_ref),
                license_spdx=info.license_spdx,
            )
        raise UpstreamTransportError(f"Unknown fake repo {repo}@{ref}")

    def get_head_commit(self, repo: str, ref: str) -> str:
        if ("get_head_commit", repo) in self.errors:
            raise UpstreamTransportError(self.errors[("get_head_commit", repo)])
        key = (repo, ref)
        if key not in self.head_commits:
            raise UpstreamTransportError(f"Unknown fake ref {repo}@{ref}")
        return self.head_commits[key]

    def compare_commits(
        self,
        repo: str,
        base_commit: str,
        head_commit: str,
        tracked_paths: list[str],
    ) -> PathCompareResult:
        if base_commit == head_commit:
            return PathCompareResult(base_commit=base_commit, head_commit=head_commit)

        base_tree = self.trees.get((repo, base_commit), {})
        head_tree = self.trees.get((repo, head_commit), {})
        all_files = sorted(set(base_tree) | set(head_tree))
        normalized = [normalize_repo_path(p) for p in tracked_paths]

        changed: list[str] = []
        for fn in all_files:
            if base_tree.get(fn) != head_tree.get(fn):
                if not normalized or any(path_matches_prefix(fn, p) for p in normalized):
                    changed.append(fn)

        commits = list(
            self.compare_commits_log.get(
                (repo, base_commit, head_commit),
                [CommitSummary(sha=head_commit[:12], message="Upstream update", date="2026-11-20T00:00:00Z")],
            )
        )
        return PathCompareResult(
            base_commit=base_commit,
            head_commit=head_commit,
            changed_files=changed,
            commits=commits,
            truncated=(repo, base_commit, head_commit) in self.truncated_pairs,
        )

    def list_skill_dirs(
        self,
        repo: str,
        commit: str,
        roots: list[str] | None = None,
    ) -> list[str]:
        tree = self.trees.get((repo, commit), {})
        norm_roots = [normalize_repo_path(r) for r in (roots or [])]
        filter_by_roots = bool(norm_roots) and "" not in norm_roots

        dirs: set[str] = set()
        for path in tree:
            if path != "SKILL.md" and not path.endswith("/SKILL.md"):
                continue
            if any(seg.startswith(".") for seg in path.split("/")):
                continue
            if filter_by_roots and not any(
                path == f"{root}/SKILL.md" or path.startswith(f"{root}/")
                for root in norm_roots
                if root
            ):
                continue
            parent = "" if path == "SKILL.md" else path[: -len("/SKILL.md")]
            dirs.add(parent)
        return sorted(dirs)

    def fetch_paths(
        self,
        repo: str,
        commit: str,
        paths: list[str],
        dest_dir: Path,
    ) -> None:
        if ("fetch_paths", repo) in self.errors:
            raise UpstreamTransportError(self.errors[("fetch_paths", repo)])
        tree = self.trees.get((repo, commit))
        if tree is None:
            raise UpstreamTransportError(f"Missing fake tree for {repo}@{commit}")
        normalized = [normalize_repo_path(p) for p in paths]
        dest_dir.mkdir(parents=True, exist_ok=True)
        for rel, (content, mode) in tree.items():
            if not normalized or any(path_matches_prefix(rel, p) for p in normalized):
                out = dest_dir / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(content)
                os.chmod(out, mode)

    def diff_paths(
        self,
        repo: str,
        base_commit: str,
        head_commit: str,
        paths: list[str],
    ) -> str:
        import difflib

        base_tree = self.trees.get((repo, base_commit), {})
        head_tree = self.trees.get((repo, head_commit), {})
        normalized = [normalize_repo_path(p) for p in paths]
        all_files = sorted(set(base_tree) | set(head_tree))
        chunks: list[str] = []
        for fn in all_files:
            if normalized and not any(path_matches_prefix(fn, p) for p in normalized):
                continue
            b_bytes = base_tree.get(fn, (b"", 0o644))[0]
            h_bytes = head_tree.get(fn, (b"", 0o644))[0]
            if b_bytes == h_bytes:
                continue
            b_lines = b_bytes.decode("utf-8", errors="replace").splitlines(keepends=True)
            h_lines = h_bytes.decode("utf-8", errors="replace").splitlines(keepends=True)
            chunks.extend(
                difflib.unified_diff(b_lines, h_lines, fromfile=f"a/{fn}", tofile=f"b/{fn}")
            )
        return "".join(chunks)


def _skill_md(name: str, desc: str = "Skill description.", extra: str = "") -> str:
    return f"---\nname: {name}\ndescription: {desc}\n---\n\n# {name}\n{extra}\n"


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    (repo / "skills.toml").write_text(
        f'''canonical_root = "{repo}"
columns = ["codex", "opencode"]

[targets]
codex    = "{tmp_path / 'targets' / 'codex'}"
opencode = "{tmp_path / 'targets' / 'opencode'}"
''',
        encoding="utf-8",
    )
    return repo


def _write_local_skill(
    repo: Path,
    group: str,
    name: str,
    files: dict[str, str | tuple[str, int]],
) -> Path:
    skill_dir = repo / "skills" / group / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    for rel, spec in files.items():
        if isinstance(spec, tuple):
            content, mode = spec
        else:
            content, mode = spec, 0o644
        target = skill_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        os.chmod(target, mode)
    return skill_dir


class UpstreamCheckTests(unittest.TestCase):
    def test_check_statuses_filtering_and_watch_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            c1 = "1111111111111111111111111111111111111111"
            c2 = "2222222222222222222222222222222222222222"

            (repo / "upstreams.toml").write_text(
                f'''[sources.synced]
repo = "org/synced"
ref = "main"
commit = "{c1}"
group = "imported/synced"
mode = "mirror"

[sources.synced.skills]
s1 = "skills/s1"

[sources.mono]
repo = "org/mono"
ref = "main"
commit = "{c1}"
group = "imported/mono"
mode = "mirror"
watch_roots = ["skills"]

[sources.mono.skills]
tracked-a = "skills/tracked-a"
tracked-b = "skills/tracked-b"

[sources.ignored]
repo = "org/ignored"
ref = "main"
commit = "{c1}"
group = "imported/ignored"
mode = "mirror"

[sources.ignored.skills]
keep = "skills/keep"

[sources.unpinned]
repo = "org/unpinned"
ref = "main"
commit = ""
group = "imported/unpinned"
mode = "notify"

[sources.unpinned.skills]
u1 = "skills/u1"
''',
                encoding="utf-8",
            )

            transport = FakeUpstreamTransport()
            transport.set_repo("org/synced", "main", c1)
            transport.set_tree("org/synced", c1, {"skills/s1/SKILL.md": _skill_md("s1")})

            transport.set_repo("org/mono", "main", c2)
            transport.set_tree(
                "org/mono",
                c1,
                {
                    "skills/tracked-a/SKILL.md": _skill_md("tracked-a", "v1"),
                    "skills/tracked-b/SKILL.md": _skill_md("tracked-b", "v1"),
                    "README.md": "v1",
                },
            )
            transport.set_tree(
                "org/mono",
                c2,
                {
                    "skills/tracked-a/SKILL.md": _skill_md("tracked-a", "v2"),
                    "skills/tracked-b/SKILL.md": _skill_md("tracked-b", "v1"),
                    "skills/brand-new/SKILL.md": _skill_md("brand-new", "new"),
                    "README.md": "v2",
                },
            )

            transport.set_repo("org/ignored", "main", c2)
            transport.set_tree(
                "org/ignored",
                c1,
                {
                    "skills/keep/SKILL.md": _skill_md("keep", "v1"),
                    "other-plugin/SKILL.md": _skill_md("other-plugin", "v1"),
                },
            )
            transport.set_tree(
                "org/ignored",
                c2,
                {
                    "skills/keep/SKILL.md": _skill_md("keep", "v1"),
                    "other-plugin/SKILL.md": _skill_md("other-plugin", "v2"),
                },
            )

            transport.set_repo("org/unpinned", "main", c2)
            transport.set_tree("org/unpinned", c2, {"skills/u1/SKILL.md": _skill_md("u1")})

            mgr = UpstreamManager(repo, transport)
            items = {i.source_id: i for i in mgr.check()}

            self.assertEqual(items["synced"].status, "up_to_date")
            self.assertEqual(items["mono"].status, "updates_available")
            self.assertEqual(items["mono"].changed_skills, ["tracked-a"])
            self.assertEqual(items["mono"].new_upstream_skills, ["skills/brand-new"])
            self.assertEqual(items["ignored"].status, "up_to_date")
            self.assertEqual(items["unpinned"].status, "unpinned")
            self.assertEqual(items["unpinned"].changed_skills, ["u1"])


class UpstreamUpdateTests(unittest.TestCase):
    def test_mirror_update_preserves_exec_bit_and_validates_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            c1 = "1111111111111111111111111111111111111111"
            c2 = "2222222222222222222222222222222222222222"
            c3 = "3333333333333333333333333333333333333333"

            _write_local_skill(
                repo,
                "imported/pack",
                "alpha",
                {"SKILL.md": _skill_md("alpha", "v1"), "scripts/run.sh": ("#!/bin/sh\necho v1\n", 0o755)},
            )
            (repo / "skills.toml").write_text(
                (repo / "skills.toml").read_text(encoding="utf-8")
                + '\n[skills."imported/pack"]\nalpha = [1, 1]\n',
                encoding="utf-8",
            )
            (repo / "upstreams.toml").write_text(
                f'''[sources.pack]
repo = "org/pack"
ref = "main"
commit = "{c1}"
group = "imported/pack"
mode = "mirror"

[sources.pack.skills]
alpha = "skills/alpha"
''',
                encoding="utf-8",
            )

            transport = FakeUpstreamTransport()
            transport.set_repo("org/pack", "main", c2)
            transport.set_tree(
                "org/pack",
                c1,
                {
                    "skills/alpha/SKILL.md": _skill_md("alpha", "v1"),
                    "skills/alpha/scripts/run.sh": ("#!/bin/sh\necho v1\n", 0o755),
                },
            )
            transport.set_tree(
                "org/pack",
                c2,
                {
                    "skills/alpha/SKILL.md": _skill_md("alpha", "v2"),
                    "skills/alpha/scripts/run.sh": ("#!/bin/sh\necho v2\n", 0o755),
                },
            )

            mgr = UpstreamManager(repo, transport)
            res = mgr.update()
            self.assertEqual(len(res), 1)
            self.assertEqual(res[0].action, "updated")
            self.assertIsNone(res[0].error)

            script_path = repo / "skills" / "imported" / "pack" / "alpha" / "scripts" / "run.sh"
            self.assertTrue(script_path.stat().st_mode & stat.S_IXUSR)
            self.assertIn("echo v2", script_path.read_text(encoding="utf-8"))
            upstreams = ManifestStore.load_upstreams_manifest(repo)
            self.assertEqual(upstreams.sources["pack"].commit, c2)

            # Broken upstream frontmatter must fail without overwriting local skill or bumping commit.
            transport.set_repo("org/pack", "main", c3)
            transport.set_tree(
                "org/pack",
                c3,
                {"skills/alpha/SKILL.md": "no frontmatter at all"},
            )
            res_bad = mgr.update()
            self.assertEqual(res_bad[0].action, "error")
            self.assertIsNotNone(res_bad[0].error)
            self.assertIn("echo v2", script_path.read_text(encoding="utf-8"))
            self.assertEqual(ManifestStore.load_upstreams_manifest(repo).sources["pack"].commit, c2)

    def test_mirror_partial_skill_update_does_not_bump_commit_when_other_skill_lags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            c1 = "1111111111111111111111111111111111111111"
            c2 = "2222222222222222222222222222222222222222"

            _write_local_skill(repo, "imported/pack", "alpha", {"SKILL.md": _skill_md("alpha", "v1")})
            _write_local_skill(repo, "imported/pack", "beta", {"SKILL.md": _skill_md("beta", "v1")})
            (repo / "skills.toml").write_text(
                (repo / "skills.toml").read_text(encoding="utf-8")
                + '\n[skills."imported/pack"]\nalpha = [1, 1]\nbeta = [1, 1]\n',
                encoding="utf-8",
            )
            (repo / "upstreams.toml").write_text(
                f'''[sources.pack]
repo = "org/pack"
ref = "main"
commit = "{c1}"
group = "imported/pack"
mode = "mirror"

[sources.pack.skills]
alpha = "skills/alpha"
beta = "skills/beta"
''',
                encoding="utf-8",
            )

            transport = FakeUpstreamTransport()
            transport.set_repo("org/pack", "main", c2)
            transport.set_tree(
                "org/pack",
                c1,
                {
                    "skills/alpha/SKILL.md": _skill_md("alpha", "v1"),
                    "skills/beta/SKILL.md": _skill_md("beta", "v1"),
                },
            )
            transport.set_tree(
                "org/pack",
                c2,
                {
                    "skills/alpha/SKILL.md": _skill_md("alpha", "v2"),
                    "skills/beta/SKILL.md": _skill_md("beta", "v2"),
                },
            )

            mgr = UpstreamManager(repo, transport)
            res = mgr.update(skill_filter="alpha")
            self.assertEqual(res[0].action, "updated")
            self.assertEqual(res[0].updated_skills, ["alpha"])
            self.assertIn("v2", (repo / "skills/imported/pack/alpha/SKILL.md").read_text(encoding="utf-8"))
            self.assertIn("v1", (repo / "skills/imported/pack/beta/SKILL.md").read_text(encoding="utf-8"))
            # Shared commit MUST stay at c1 because beta still lags.
            self.assertEqual(ManifestStore.load_upstreams_manifest(repo).sources["pack"].commit, c1)

    def test_mirror_unpinned_requires_explicit_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            c2 = "2222222222222222222222222222222222222222"

            _write_local_skill(repo, "imported/pack", "alpha", {"SKILL.md": _skill_md("alpha", "v1")})
            (repo / "skills.toml").write_text(
                (repo / "skills.toml").read_text(encoding="utf-8")
                + '\n[skills."imported/pack"]\nalpha = [1, 1]\n',
                encoding="utf-8",
            )
            (repo / "upstreams.toml").write_text(
                '''[sources.pack]
repo = "org/pack"
ref = "main"
commit = ""
group = "imported/pack"
mode = "mirror"

[sources.pack.skills]
alpha = "skills/alpha"
''',
                encoding="utf-8",
            )

            transport = FakeUpstreamTransport()
            transport.set_repo("org/pack", "main", c2)
            transport.set_tree("org/pack", c2, {"skills/alpha/SKILL.md": _skill_md("alpha", "v2")})

            mgr = UpstreamManager(repo, transport)
            bulk = mgr.update()
            self.assertEqual(bulk[0].action, "unpinned_manual")
            self.assertIn("v1", (repo / "skills/imported/pack/alpha/SKILL.md").read_text(encoding="utf-8"))
            self.assertEqual(ManifestStore.load_upstreams_manifest(repo).sources["pack"].commit, "")

            explicit = mgr.update(source_filter="pack")
            self.assertEqual(explicit[0].action, "updated")
            self.assertIn("v2", (repo / "skills/imported/pack/alpha/SKILL.md").read_text(encoding="utf-8"))
            self.assertEqual(ManifestStore.load_upstreams_manifest(repo).sources["pack"].commit, c2)

    def test_adapted_diff_only_and_three_way_merge_clean_and_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            c1 = "1111111111111111111111111111111111111111"
            c2 = "2222222222222222222222222222222222222222"
            c3 = "3333333333333333333333333333333333333333"

            base_text = "---\nname: adapted-skill\ndescription: Base\n---\n\nLine 1\nLine 2\nLine 3\nLine 4\nLine 5\n"
            local_text = "---\nname: adapted-skill\ndescription: Base\n---\n\nLine 1 local edit\nLine 2\nLine 3\nLine 4\nLine 5\n"
            upstream_clean = "---\nname: adapted-skill\ndescription: Base\n---\n\nLine 1\nLine 2\nLine 3\nLine 4\nLine 5 upstream edit\n"
            upstream_conflict = "---\nname: adapted-skill\ndescription: Base\n---\n\nLine 1 upstream conflict\nLine 2\nLine 3\nLine 4\nLine 5 upstream edit\n"

            _write_local_skill(repo, "imported/adapted", "adapted-skill", {"SKILL.md": local_text})
            (repo / "skills.toml").write_text(
                (repo / "skills.toml").read_text(encoding="utf-8")
                + '\n[skills."imported/adapted"]\nadapted-skill = [1, 1]\n',
                encoding="utf-8",
            )
            (repo / "upstreams.toml").write_text(
                f'''[sources.adp]
repo = "org/adp"
ref = "main"
commit = "{c1}"
group = "imported/adapted"
mode = "adapted"

[sources.adp.skills]
adapted-skill = "skills/adapted-skill"
''',
                encoding="utf-8",
            )

            transport = FakeUpstreamTransport()
            transport.set_repo("org/adp", "main", c2)
            transport.set_tree("org/adp", c1, {"skills/adapted-skill/SKILL.md": base_text})
            transport.set_tree("org/adp", c2, {"skills/adapted-skill/SKILL.md": upstream_clean})

            mgr = UpstreamManager(repo, transport)
            skill_file = repo / "skills" / "imported" / "adapted" / "adapted-skill" / "SKILL.md"

            # Default adapted update is diff_only and never overwrites local edits.
            diff_res = mgr.update()
            self.assertEqual(diff_res[0].action, "diff_only")
            self.assertIn("Line 5 upstream edit", diff_res[0].diff)
            self.assertEqual(skill_file.read_text(encoding="utf-8"), local_text)
            self.assertEqual(ManifestStore.load_upstreams_manifest(repo).sources["adp"].commit, c1)

            # Clean 3-way merge preserves local edit, applies upstream edit, and bumps commit.
            merged_res = mgr.update(merge=True)
            self.assertEqual(merged_res[0].action, "merged")
            self.assertEqual(merged_res[0].conflict_files, [])
            merged_body = skill_file.read_text(encoding="utf-8")
            self.assertIn("Line 1 local edit", merged_body)
            self.assertIn("Line 5 upstream edit", merged_body)
            self.assertEqual(ManifestStore.load_upstreams_manifest(repo).sources["adp"].commit, c2)

            # Conflict writes conflict markers and does NOT bump commit.
            transport.set_repo("org/adp", "main", c3)
            transport.set_tree("org/adp", c3, {"skills/adapted-skill/SKILL.md": upstream_conflict})
            conflict_res = mgr.update(merge=True)
            self.assertEqual(conflict_res[0].action, "merge_conflicts")
            self.assertEqual(conflict_res[0].conflict_files, ["adapted-skill/SKILL.md"])
            conflict_body = skill_file.read_text(encoding="utf-8")
            self.assertIn("<<<<<<< local", conflict_body)
            self.assertIn(">>>>>>> upstream", conflict_body)
            self.assertEqual(ManifestStore.load_upstreams_manifest(repo).sources["adp"].commit, c2)

    def test_notify_never_modifies_files_and_preserves_notes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            c1 = "1111111111111111111111111111111111111111"
            c2 = "2222222222222222222222222222222222222222"

            local_skill = _write_local_skill(
                repo, "imported/misc", "watch-me", {"SKILL.md": _skill_md("watch-me", "local")}
            )
            (repo / "skills.toml").write_text(
                (repo / "skills.toml").read_text(encoding="utf-8")
                + '\n[skills."imported/misc"]\nwatch-me = [1, 1]\n',
                encoding="utf-8",
            )
            (repo / "upstreams.toml").write_text(
                f'''[sources.watcher]
repo = "org/watcher"
ref = "main"
commit = "{c1}"
group = "imported/misc"
mode = "notify"
notes = "Manual review required."

[sources.watcher.skills]
watch-me = ["SKILL.md", "src"]
''',
                encoding="utf-8",
            )

            transport = FakeUpstreamTransport()
            transport.set_repo("org/watcher", "main", c2)
            transport.set_tree("org/watcher", c1, {"SKILL.md": _skill_md("watch-me", "v1")})
            transport.set_tree("org/watcher", c2, {"SKILL.md": _skill_md("watch-me", "v2")})

            mgr = UpstreamManager(repo, transport)
            res = mgr.update()
            self.assertEqual(res[0].action, "notify_only")
            self.assertIn("Manual review required.", res[0].notes)
            self.assertIn("local", (local_skill / "SKILL.md").read_text(encoding="utf-8"))
            self.assertEqual(ManifestStore.load_upstreams_manifest(repo).sources["watcher"].commit, c1)


class UpstreamAddAndValidationTests(unittest.TestCase):
    def test_add_discovery_import_enable_and_duplicate_collision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            c1 = "aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111"

            transport = FakeUpstreamTransport()
            transport.set_repo("owner/cool-pack", "main", c1, license_spdx="Apache-2.0")
            transport.set_tree(
                "owner/cool-pack",
                c1,
                {
                    "LICENSE": "Apache License 2.0",
                    "skills/skill-one/SKILL.md": _skill_md("skill-one", "One"),
                    "skills/skill-two/SKILL.md": _skill_md("skill-two", "Two"),
                },
            )

            mgr = UpstreamManager(repo, transport)

            # Discovery mode: neither paths nor all_skills -> writes nothing to disk.
            disc = mgr.add("owner/cool-pack")
            self.assertEqual(disc.discovered_paths, ["skills/skill-one", "skills/skill-two"])
            self.assertEqual(disc.added_skills, [])
            self.assertFalse((repo / "upstreams.toml").exists())

            # --all without --enable -> [0, 0] rows + license copied + upstreams.toml created.
            imported = mgr.add("owner/cool-pack", all_skills=True)
            self.assertEqual(imported.added_skills, ["skill-one", "skill-two"])
            self.assertEqual(imported.license_copied, "licenses/cool-pack-Apache-2.0.txt")
            self.assertEqual(imported.validation_errors, [])
            self.assertTrue((repo / "licenses" / "cool-pack-Apache-2.0.txt").is_file())
            self.assertTrue((repo / "skills/imported/cool-pack/skill-one/SKILL.md").is_file())

            skills_toml = tomllib.loads((repo / "skills.toml").read_text(encoding="utf-8"))
            self.assertEqual(
                skills_toml["skills"]["imported/cool-pack"]["skill-one"],
                [0, 0],
            )

            # --enable sets specified columns to 1 for another repo.
            transport.set_repo("owner/second-pack", "main", c1, license_spdx=None)
            transport.set_tree(
                "owner/second-pack",
                c1,
                {"skills/skill-three/SKILL.md": _skill_md("skill-three", "Three")},
            )
            enabled_res = mgr.add(
                "owner/second-pack",
                paths=["skills/skill-three"],
                enable=["opencode"],
            )
            self.assertEqual(enabled_res.validation_errors, [])
            skills_toml = tomllib.loads((repo / "skills.toml").read_text(encoding="utf-8"))
            self.assertEqual(
                skills_toml["skills"]["imported/second-pack"]["skill-three"],
                [0, 1],
            )

            # Duplicate skill collision refused cleanly.
            transport.set_repo("owner/dup-pack", "main", c1)
            transport.set_tree(
                "owner/dup-pack",
                c1,
                {"skills/skill-one/SKILL.md": _skill_md("skill-one", "Dup")},
            )
            with self.assertRaisesRegex(ValueError, "already exists in skills.toml"):
                mgr.add("owner/dup-pack", all_skills=True)

    def test_validate_upstreams_catches_invalid_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            _write_local_skill(repo, "imported/a", "s1", {"SKILL.md": _skill_md("s1")})
            (repo / "skills.toml").write_text(
                (repo / "skills.toml").read_text(encoding="utf-8")
                + '\n[skills."imported/a"]\ns1 = [1, 0]\n',
                encoding="utf-8",
            )
            (repo / "upstreams.toml").write_text(
                '''[sources.one]
repo = "not-a-slug"
ref = "main"
commit = "abc"
group = "imported/a"
mode = "invalid_mode"

[sources.one.skills]
s1 = "skills/s1"
missing-skill = "skills/missing"

[sources.two]
repo = "org/two"
ref = "main"
commit = "def"
group = "imported/a"
mode = "mirror"

[sources.two.skills]
s1 = "skills/s1"
''',
                encoding="utf-8",
            )
            errors = RepoValidator.validate_upstreams(repo)
            self.assertTrue(any("not-a-slug" in e for e in errors), errors)
            self.assertTrue(any("invalid_mode" in e for e in errors), errors)
            self.assertTrue(any("missing-skill" in e for e in errors), errors)
            self.assertTrue(any("multiple upstream sources" in e for e in errors), errors)


class CliJsonWorkflowCompatTests(unittest.TestCase):
    def test_cli_json_matches_github_workflow_formatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            c1 = "1111111111111111111111111111111111111111"
            c2 = "2222222222222222222222222222222222222222"

            _write_local_skill(repo, "imported/m", "m1", {"SKILL.md": _skill_md("m1", "v1")})
            _write_local_skill(repo, "imported/a", "a1", {"SKILL.md": _skill_md("a1", "local")})
            _write_local_skill(repo, "imported/n", "n1", {"SKILL.md": _skill_md("n1", "local")})
            (repo / "skills.toml").write_text(
                (repo / "skills.toml").read_text(encoding="utf-8")
                + '''
[skills."imported/m"]
m1 = [1, 1]

[skills."imported/a"]
a1 = [1, 1]

[skills."imported/n"]
n1 = [1, 1]
''',
                encoding="utf-8",
            )
            (repo / "upstreams.toml").write_text(
                f'''[sources.mir]
repo = "org/mir"
ref = "main"
commit = "{c1}"
group = "imported/m"
mode = "mirror"
watch_roots = ["skills"]

[sources.mir.skills]
m1 = "skills/m1"

[sources.adp]
repo = "org/adp"
ref = "main"
commit = "{c1}"
group = "imported/a"
mode = "adapted"

[sources.adp.skills]
a1 = "skills/a1"

[sources.ntf]
repo = "org/ntf"
ref = "main"
commit = "{c1}"
group = "imported/n"
mode = "notify"

[sources.ntf.skills]
n1 = "skills/n1"
''',
                encoding="utf-8",
            )

            transport = FakeUpstreamTransport()
            transport.set_repo("org/mir", "main", c2)
            transport.set_tree("org/mir", c1, {"skills/m1/SKILL.md": _skill_md("m1", "v1")})
            transport.set_tree(
                "org/mir",
                c2,
                {
                    "skills/m1/SKILL.md": _skill_md("m1", "v2"),
                    "skills/m2/SKILL.md": _skill_md("m2", "new"),
                },
            )
            transport.set_repo("org/adp", "main", c2)
            transport.set_tree("org/adp", c1, {"skills/a1/SKILL.md": _skill_md("a1", "v1")})
            transport.set_tree("org/adp", c2, {"skills/a1/SKILL.md": _skill_md("a1", "v2")})
            transport.set_repo("org/ntf", "main", c2)
            transport.set_tree("org/ntf", c1, {"skills/n1/SKILL.md": _skill_md("n1", "v1")})
            transport.set_tree("org/ntf", c2, {"skills/n1/SKILL.md": _skill_md("n1", "v2")})

            check_out = io.StringIO()
            update_out = io.StringIO()
            with mock.patch("skillctl.cli.GitHubCliTransport", return_value=transport):
                with contextlib.redirect_stdout(check_out):
                    rc_chk = cli.main(["upstream", "check", "--json", "--root", str(repo)])
                with contextlib.redirect_stdout(update_out):
                    rc_upd = cli.main(["upstream", "update", "--json", "--root", str(repo)])

            self.assertEqual(rc_chk, 0)
            self.assertEqual(rc_upd, 0)

            raw_checks = json.loads(check_out.getvalue())
            raw_updates = json.loads(update_out.getvalue())
            checks = raw_checks["items"]
            updates = raw_updates["items"]

            applied = [u for u in updates if u.get("action") == "updated"]
            adapted = [u for u in updates if u.get("action") in ("diff_only", "merge_conflicts")]
            notify = [u for u in updates if u.get("action") == "notify_only"]
            new_skills = [
                (c["source_id"], c["repo"], p)
                for c in checks
                for p in (c.get("new_upstream_skills") or [])
            ]

            self.assertEqual([u["source_id"] for u in applied], ["mir"])
            self.assertEqual([u["source_id"] for u in adapted], ["adp"])
            self.assertTrue(adapted[0]["diff"])
            self.assertEqual([u["source_id"] for u in notify], ["ntf"])
            self.assertEqual(len(notify[0]["commits"]), 1)
            self.assertEqual(new_skills, [("mir", "org/mir", "skills/m2")])

    def test_cli_validate_add_and_update_nonzero_on_error_or_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            c1 = "1111111111111111111111111111111111111111"
            c2 = "2222222222222222222222222222222222222222"

            # validate passes on valid repo
            val_out = io.StringIO()
            with contextlib.redirect_stdout(val_out):
                rc_val = cli.main(["validate", "--root", str(repo)])
            self.assertEqual(rc_val, 0)
            self.assertIn("[PASS] Validation OK", val_out.getvalue())

            # upstream add discovery mode + --enable comma-separated via CLI
            transport = FakeUpstreamTransport()
            transport.set_repo("org/pack", "main", c1, license_spdx=None)
            transport.set_tree(
                "org/pack",
                c1,
                {"skills/s1/SKILL.md": _skill_md("s1", "S1")},
            )
            with mock.patch("skillctl.cli.GitHubCliTransport", return_value=transport):
                disc_out = io.StringIO()
                with contextlib.redirect_stdout(disc_out):
                    rc_disc = cli.main(["upstream", "add", "org/pack", "--root", str(repo)])
                self.assertEqual(rc_disc, 0)
                self.assertIn("skills/s1", disc_out.getvalue())

                add_out = io.StringIO()
                with contextlib.redirect_stdout(add_out):
                    rc_add = cli.main(
                        [
                            "upstream",
                            "add",
                            "org/pack",
                            "--path",
                            "skills/s1",
                            "--enable",
                            "codex,opencode",
                            "--root",
                            str(repo),
                        ]
                    )
                self.assertEqual(rc_add, 0)
                self.assertIn("[PASS] Validation OK", add_out.getvalue())

            # Validate fails when upstreams.toml has an error
            (repo / "upstreams.toml").write_text(
                f'''[sources.bad]
repo = "bad-slug"
ref = "main"
commit = "{c1}"
group = "imported/pack"
mode = "adapted"

[sources.bad.skills]
s1 = "skills/s1"
''',
                encoding="utf-8",
            )
            val_err = io.StringIO()
            with contextlib.redirect_stderr(val_err):
                rc_bad = cli.main(["validate", "--root", str(repo)])
            self.assertEqual(rc_bad, 1)
            self.assertIn("bad-slug", val_err.getvalue())

            # upstream update exits non-zero on merge conflicts
            (repo / "upstreams.toml").write_text(
                f'''[sources.pack]
repo = "org/pack"
ref = "main"
commit = "{c1}"
group = "imported/pack"
mode = "adapted"

[sources.pack.skills]
s1 = "skills/s1"
''',
                encoding="utf-8",
            )
            (repo / "skills/imported/pack/s1/SKILL.md").write_text(
                "---\nname: s1\ndescription: local edit\n---\n",
                encoding="utf-8",
            )
            transport.set_repo("org/pack", "main", c2)
            transport.set_tree(
                "org/pack",
                c2,
                {"skills/s1/SKILL.md": "---\nname: s1\ndescription: upstream edit\n---\n"},
            )
            with mock.patch("skillctl.cli.GitHubCliTransport", return_value=transport):
                upd_out = io.StringIO()
                with contextlib.redirect_stdout(upd_out):
                    rc_conflict = cli.main(
                        ["upstream", "update", "--merge", "--json", "--root", str(repo)]
                    )
            self.assertEqual(rc_conflict, 1)
            payload = json.loads(upd_out.getvalue())
            self.assertEqual(payload["items"][0]["action"], "merge_conflicts")


if __name__ == "__main__":
    unittest.main()

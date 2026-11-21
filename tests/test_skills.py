#!/usr/bin/env python3
"""Unit tests for scripts/skills.py using temporary directories only."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import skills  # noqa: E402


def _write_skill(
    root: Path,
    group: str,
    name: str,
    frontmatter: str,
    *,
    openai_yaml: str | None = None,
) -> Path:
    skill_dir = root / "skills" / group / name if group else root / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"{frontmatter}\nBody.\n", encoding="utf-8")
    if openai_yaml is not None:
        agents_dir = skill_dir / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        (agents_dir / "openai.yaml").write_text(openai_yaml, encoding="utf-8")
    return skill_dir


class FrontmatterAndOpenAIYamlTests(unittest.TestCase):
    def test_block_scalars_quotes_and_boolean(self) -> None:
        text = """---
name: demo-skill
description: >
  Line one
  line two.
summary: >-
  Folded stripped
  across lines
literal: |
  first
  second
single: 'it''s quoted'
double: "escaped \\"quote\\" here"
continued: first part
  second part
disable-model-invocation: true
---
# Heading
"""
        fm = skills.parse_frontmatter(text)
        self.assertEqual(fm["name"], "demo-skill")
        self.assertEqual(fm["description"], "Line one line two.\n")
        self.assertEqual(fm["summary"], "Folded stripped across lines")
        self.assertEqual(fm["literal"], "first\nsecond\n")
        self.assertEqual(fm["single"], "it's quoted")
        self.assertEqual(fm["double"], 'escaped "quote" here')
        self.assertEqual(fm["continued"], "first part second part")
        self.assertIs(fm["disable-model-invocation"], True)

    def test_openai_yaml_allow_implicit_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            yaml_path = Path(tmp) / "openai.yaml"
            yaml_path.write_text(
                'interface:\n  display_name: "Demo"\npolicy:\n  allow_implicit_invocation: false\n',
                encoding="utf-8",
            )
            parsed = skills.parse_openai_yaml(yaml_path)
            self.assertEqual(parsed["interface"]["display_name"], "Demo")
            self.assertIs(parsed["policy"]["allow_implicit_invocation"], False)


class ValidationTests(unittest.TestCase):
    def _make_base_repo(self, tmp_path: Path) -> tuple[Path, Path, Path]:
        repo = tmp_path / "repo"
        t_codex = tmp_path / "targets" / "codex"
        t_agy = tmp_path / "targets" / "agy"
        repo.mkdir(parents=True)
        _write_skill(
            repo,
            "custom",
            "alpha",
            '---\nname: alpha\ndescription: "Alpha skill"\n---',
        )
        _write_skill(
            repo,
            "imported/pack",
            "beta",
            "---\nname: beta\ndescription: >\n  Beta skill.\n---",
        )
        (repo / "skills.toml").write_text(
            f'''canonical_root = "{repo}"
columns = ["codex", "antigravity"]

[targets]
codex = "{t_codex}"
antigravity = "{t_agy}"

[requires]
beta = ["alpha"]

[skills."custom"]
alpha = [1, 1]

[skills."imported/pack"]
beta = [0, 1]
''',
            encoding="utf-8",
        )
        return repo, t_codex, t_agy

    def test_valid_repo_passes_and_supports_nested_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, t_codex, t_agy = self._make_base_repo(Path(tmp))
            self.assertEqual(skills.validate(repo), [])

            # Rewrite with unquoted nested [skills.imported.pack]
            (repo / "skills.toml").write_text(
                f'''canonical_root = "{repo}"
columns = ["codex", "antigravity"]

[targets]
codex = "{t_codex}"
antigravity = "{t_agy}"

[skills.custom]
alpha = [1, 1]

[skills.imported.pack]
beta = [0, 1]
''',
                encoding="utf-8",
            )
            self.assertEqual(skills.validate(repo), [])

    def test_requires_dependency_violation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, t_codex, t_agy = self._make_base_repo(Path(tmp))
            (repo / "skills.toml").write_text(
                f'''canonical_root = "{repo}"
columns = ["codex", "antigravity"]

[targets]
codex = "{t_codex}"
antigravity = "{t_agy}"

[requires]
beta = ["alpha"]

[skills."custom"]
alpha = [0, 1]

[skills."imported/pack"]
beta = [1, 1]
''',
                encoding="utf-8",
            )
            errors = skills.validate(repo)
            self.assertTrue(any("[requires] violation" in e and "beta" in e for e in errors), errors)

    def test_duplicate_skill_names_across_groups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, t_codex, t_agy = self._make_base_repo(Path(tmp))
            _write_skill(repo, "other", "alpha", "---\nname: alpha\ndescription: dup\n---")
            (repo / "skills.toml").write_text(
                f'''canonical_root = "{repo}"
columns = ["codex", "antigravity"]

[targets]
codex = "{t_codex}"
antigravity = "{t_agy}"

[skills."custom"]
alpha = [1, 1]

[skills."other"]
alpha = [1, 1]

[skills."imported/pack"]
beta = [0, 1]
''',
                encoding="utf-8",
            )
            errors = skills.validate(repo)
            self.assertTrue(any("Duplicate" in e and "alpha" in e for e in errors), errors)

    def test_wrong_row_length_and_non_binary_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, t_codex, t_agy = self._make_base_repo(Path(tmp))
            (repo / "skills.toml").write_text(
                f'''canonical_root = "{repo}"
columns = ["codex", "antigravity"]

[targets]
codex = "{t_codex}"
antigravity = "{t_agy}"

[skills."custom"]
alpha = [1]

[skills."imported/pack"]
beta = [true, 2]
''',
                encoding="utf-8",
            )
            errors = skills.validate(repo)
            self.assertTrue(any("length 2" in e for e in errors), errors)
            self.assertTrue(any("must be int 0 or 1" in e for e in errors), errors)

    def test_untracked_dir_missing_dir_and_mismatched_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo, t_codex, t_agy = self._make_base_repo(Path(tmp))
            _write_skill(repo, "custom", "untracked", "---\nname: untracked\ndescription: hi\n---")
            _write_skill(repo, "custom", "badname", "---\nname: wrong\ndescription: hi\n---")
            (repo / "skills.toml").write_text(
                f'''canonical_root = "{repo}"
columns = ["codex", "antigravity"]

[targets]
codex = "{t_codex}"
antigravity = "{t_agy}"

[skills."custom"]
alpha = [1, 1]
badname = [1, 0]
ghost = [0, 1]

[skills."imported/pack"]
beta = [0, 1]
''',
                encoding="utf-8",
            )
            errors = skills.validate(repo)
            self.assertTrue(any("not declared" in e and "untracked" in e for e in errors), errors)
            self.assertTrue(any("not found on disk" in e and "ghost" in e for e in errors), errors)
            self.assertTrue(any("wrong" in e and "badname" in e for e in errors), errors)


class SyncTests(unittest.TestCase):
    def _setup_sync_repo(self, tmp_path: Path) -> tuple[Path, Path, Path]:
        repo = tmp_path / "repo"
        t_codex = tmp_path / "targets" / "codex"
        t_opencode = tmp_path / "targets" / "opencode"
        repo.mkdir(parents=True)
        _write_skill(repo, "custom", "s1", "---\nname: s1\ndescription: s1 desc\n---")
        _write_skill(repo, "imported/misc", "s2", "---\nname: s2\ndescription: s2 desc\n---")
        _write_skill(repo, "custom", "s3", "---\nname: s3\ndescription: s3 desc\n---")
        (repo / "skills.toml").write_text(
            f'''canonical_root = "{repo}"
columns = ["codex", "opencode"]

[targets]
codex = "{t_codex}"
opencode = "{t_opencode}"

[skills."custom"]
s1 = [1, 0]
s3 = [0, 1]

[skills."imported/misc"]
s2 = [1, 1]
''',
            encoding="utf-8",
        )
        return repo, t_codex, t_opencode

    def test_sync_creates_replaces_removes_and_preserves_unmanaged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo, t_codex, t_opencode = self._setup_sync_repo(tmp_path)
            t_codex.mkdir(parents=True)
            t_opencode.mkdir(parents=True)

            # Drifted repo-owned link (e.g., pre-reorg path inside repo/skills)
            (t_codex / "s2").symlink_to(repo / "skills" / "s2")
            # Disabled 0 skill with stale repo-owned link -> should be removed
            (t_codex / "s3").symlink_to(repo / "skills" / "custom" / "s3")
            # Unmanaged real dir and foreign symlink -> must be preserved
            system_dir = t_codex / ".system"
            system_dir.mkdir()
            (system_dir / "keep.txt").write_text("keep", encoding="utf-8")
            foreign_target = tmp_path / "omarchy" / "omarchy"
            foreign_target.mkdir(parents=True)
            (t_codex / "omarchy").symlink_to(foreign_target)

            with mock.patch("shutil.which", return_value=None):
                rc = skills.main(["sync", "--root", str(repo), "--no-reload"])

            self.assertEqual(rc, 0)
            self.assertTrue((t_codex / "s1").is_symlink())
            self.assertEqual(Path(os.readlink(t_codex / "s1")), repo / "skills" / "custom" / "s1")
            self.assertEqual(Path(os.readlink(t_codex / "s2")), repo / "skills" / "imported" / "misc" / "s2")
            self.assertFalse((t_codex / "s3").exists() or (t_codex / "s3").is_symlink())
            self.assertTrue((system_dir / "keep.txt").is_file())
            self.assertEqual(Path(os.readlink(t_codex / "omarchy")), foreign_target)
            self.assertEqual(Path(os.readlink(t_opencode / "s3")), repo / "skills" / "custom" / "s3")

    def test_sync_refuses_conflicts_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo, t_codex, _ = self._setup_sync_repo(tmp_path)
            t_codex.mkdir(parents=True)
            conflict_dir = t_codex / "s1"
            conflict_dir.mkdir()
            marker = conflict_dir / "user_file.txt"
            marker.write_text("do not delete", encoding="utf-8")

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), mock.patch("shutil.which", return_value=None):
                rc = skills.main(["sync", "--root", str(repo), "--no-reload"])

            self.assertNotEqual(rc, 0)
            self.assertIn("CONFLICT", stderr.getvalue())
            self.assertFalse(conflict_dir.is_symlink())
            self.assertEqual(marker.read_text(encoding="utf-8"), "do not delete")

    def test_sync_refuses_symlinked_target_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo, t_codex, t_opencode = self._setup_sync_repo(tmp_path)
            real_other = tmp_path / "agents_skills"
            real_other.mkdir()
            t_codex.parent.mkdir(parents=True, exist_ok=True)
            t_codex.symlink_to(real_other)

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), mock.patch("shutil.which", return_value=None):
                rc = skills.main(["sync", "--root", str(repo), "--no-reload"])

            self.assertNotEqual(rc, 0)
            self.assertIn("symlink", stderr.getvalue())
            self.assertEqual(list(real_other.iterdir()), [])
            # Other non-symlinked targets still sync cleanly
            self.assertTrue((t_opencode / "s2").is_symlink())

    def test_sync_refuses_linked_worktree_unless_allow_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo, t_codex, _ = self._setup_sync_repo(tmp_path)
            # Simulate a linked git worktree (.git is a file)
            (repo / ".git").write_text("gitdir: /some/main/.git/worktrees/wt\n", encoding="utf-8")
            self.assertTrue(skills.is_linked_worktree(repo))

            stderr = io.StringIO()
            with mock.patch.object(skills, "DEFAULT_ROOT", repo), mock.patch("shutil.which", return_value=None):
                with contextlib.redirect_stderr(stderr):
                    rc_blocked = skills.main(["sync"])
                self.assertNotEqual(rc_blocked, 0)
                self.assertIn("worktree", stderr.getvalue().lower())
                self.assertFalse(t_codex.exists())

                rc_allowed = skills.main(["sync", "--allow-worktree", "--no-reload"])
                self.assertEqual(rc_allowed, 0)
                self.assertTrue((t_codex / "s1").is_symlink())


class LiveDiscoveryParserTests(unittest.TestCase):
    def test_parse_codex_prompt_input_duplicates_and_root_expansion(self) -> None:
        fixture = [
            {
                "type": "message",
                "role": "developer",
                "content": [
                    {
                        "type": "input_text",
                        "text": """<skills_instructions>
### Skill roots
- `r0` = `/home/marc/.codex/skills`
- `r1` = `/home/marc/.agents/skills`
### Available skills
- browser-use: Control browser. (file: r1/browser-use/SKILL.md)
- browser-use: Control browser. (file: r0/browser-use/SKILL.md)
- documents:documents: Create docs. (file: r0/documents/SKILL.md)
</skills_instructions>""",
                    }
                ],
            }
        ]
        roots, found = skills.parse_codex_prompt_input(fixture)
        self.assertEqual(roots["r0"], "/home/marc/.codex/skills")
        self.assertEqual(roots["r1"], "/home/marc/.agents/skills")
        self.assertEqual(len(found), 3)
        self.assertEqual(found[0]["name"], "browser-use")
        self.assertEqual(found[0]["expanded_path"], Path("/home/marc/.agents/skills/browser-use/SKILL.md"))
        self.assertEqual(found[1]["expanded_path"], Path("/home/marc/.codex/skills/browser-use/SKILL.md"))
        self.assertEqual(found[2]["name"], "documents:documents")

    def test_parse_opencode_skill_list(self) -> None:
        fixture = {
            "location": {"directory": "/home/marc"},
            "data": [
                {"id": "opencode", "name": "OpenCode", "path": "skill://opencode/SKILL.md"},
                {
                    "id": "browser-use",
                    "name": "browser-use",
                    "path": "/home/marc/.config/opencode/skills/browser-use/SKILL.md",
                },
                {
                    "id": "omarchy-theme",
                    "name": "omarchy-theme",
                    "path": "/home/marc/.claude/skills/omarchy-theme/SKILL.md",
                },
            ],
        }
        records = skills.parse_opencode_skill_list(fixture)
        self.assertEqual(len(records), 3)
        self.assertTrue(records[0]["is_builtin"])
        self.assertIsNone(records[0]["raw_path"])
        self.assertFalse(records[1]["is_builtin"])
        self.assertEqual(
            records[1]["raw_path"],
            Path("/home/marc/.config/opencode/skills/browser-use/SKILL.md"),
        )
        self.assertEqual(
            records[2]["raw_path"],
            Path("/home/marc/.claude/skills/omarchy-theme/SKILL.md"),
        )

    def test_v2_codex_verification_respects_allow_implicit_invocation_false_and_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = tmp_path / "repo"
            t_codex = tmp_path / "codex"
            repo.mkdir()
            _write_skill(
                repo,
                "custom",
                "hidden-skill",
                "---\nname: hidden-skill\ndescription: hidden\ndisable-model-invocation: true\n---",
                openai_yaml="policy:\n  allow_implicit_invocation: false\n",
            )
            _write_skill(
                repo,
                "custom",
                "visible-skill",
                "---\nname: visible-skill\ndescription: visible\n---",
            )
            (repo / "skills.toml").write_text(
                f'''canonical_root = "{repo}"
columns = ["codex"]

[targets]
codex = "{t_codex}"

[skills."custom"]
hidden-skill = [1]
visible-skill = [1]
''',
                encoding="utf-8",
            )
            manifest = skills.load_manifest(repo)
            disk_skills = {s.name: s for s in skills.discover_skills(repo)}

            good_codex = json.dumps(
                [
                    {
                        "content": [
                            {
                                "text": f"### Skill roots\n- `r0` = `{t_codex}`\n### Available skills\n- visible-skill: visible (file: r0/visible-skill/SKILL.md)\n"
                            }
                        ]
                    }
                ]
            )
            # Create symlink so resolved_path matches
            t_codex.mkdir()
            (t_codex / "visible-skill").symlink_to(repo / "skills" / "custom" / "visible-skill")

            with mock.patch("shutil.which", return_value="/usr/bin/codex"), mock.patch(
                "subprocess.run",
                return_value=mock.Mock(returncode=0, stdout=good_codex, stderr=""),
            ):
                self.assertEqual(skills._check_v2_codex(repo, manifest, disk_skills), [])

            dup_codex = json.dumps(
                [
                    {
                        "content": [
                            {
                                "text": f"### Skill roots\n- `r0` = `{t_codex}`\n### Available skills\n- visible-skill: visible (file: r0/visible-skill/SKILL.md)\n- visible-skill: dup (file: r0/visible-skill/SKILL.md)\n"
                            }
                        ]
                    }
                ]
            )
            with mock.patch("shutil.which", return_value="/usr/bin/codex"), mock.patch(
                "subprocess.run",
                return_value=mock.Mock(returncode=0, stdout=dup_codex, stderr=""),
            ):
                errs = skills._check_v2_codex(repo, manifest, disk_skills)
                self.assertTrue(any("Duplicate Codex available skill" in e for e in errs), errs)

    def test_v4_agy_cli_flags_and_filtering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = tmp_path / "repo"
            t_agy = tmp_path / "agy"
            repo.mkdir()
            _write_skill(
                repo,
                "custom",
                "auto-skill",
                "---\nname: auto-skill\ndescription: auto\n---",
            )
            _write_skill(
                repo,
                "custom",
                "manual-skill",
                "---\nname: manual-skill\ndescription: manual\ndisable-model-invocation: true\n---",
            )
            _write_skill(
                repo,
                "custom",
                "off-skill",
                "---\nname: off-skill\ndescription: off\n---",
            )
            (repo / "skills.toml").write_text(
                f'''canonical_root = "{repo}"
columns = ["antigravity"]

[targets]
antigravity = "{t_agy}"

[skills."custom"]
auto-skill = [1]
manual-skill = [1]
off-skill = [0]
''',
                encoding="utf-8",
            )
            manifest = skills.load_manifest(repo)
            disk_skills = {s.name: s for s in skills.discover_skills(repo)}

            # auto-skill present, manual-skill (disable-model-invocation: true) omitted -> passes
            with mock.patch("shutil.which", return_value="/usr/bin/agy"), mock.patch(
                "subprocess.run",
                return_value=mock.Mock(returncode=0, stdout="auto-skill\n", stderr=""),
            ) as run_mock:
                errs = skills._check_v4_agy(manifest, disk_skills)
                self.assertEqual(errs, [])
                cmd = run_mock.call_args[0][0]
                self.assertEqual(cmd[:2], ["agy", "--disable-slash-commands"])
                self.assertTrue(cmd[2].startswith("-p="))

            # off-skill (antigravity=0) present -> fails
            with mock.patch("shutil.which", return_value="/usr/bin/agy"), mock.patch(
                "subprocess.run",
                return_value=mock.Mock(returncode=0, stdout="auto-skill\noff-skill\n", stderr=""),
            ):
                errs = skills._check_v4_agy(manifest, disk_skills)
                self.assertTrue(any("off-skill" in e and "antigravity=0" in e for e in errs), errs)

            # auto-skill (antigravity=1) missing -> fails
            with mock.patch("shutil.which", return_value="/usr/bin/agy"), mock.patch(
                "subprocess.run",
                return_value=mock.Mock(returncode=0, stdout="", stderr=""),
            ):
                errs = skills._check_v4_agy(manifest, disk_skills)
                self.assertTrue(any("auto-skill" in e and "antigravity=1" in e for e in errs), errs)


if __name__ == "__main__":
    unittest.main()

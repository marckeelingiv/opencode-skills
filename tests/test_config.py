#!/usr/bin/env python3
"""Unit tests for PolicyConfig, ManifestStore TOML writers, and FrontmatterParser."""

from __future__ import annotations

import json
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from skillctl.config import (  # noqa: E402
    LeakConfigSpec,
    ManifestStore,
    PolicyConfig,
    UpstreamSource,
)
from skillctl.frontmatter import FrontmatterParser, SkillDiscovery  # noqa: E402
from skillctl.verifiers import (  # noqa: E402
    OpenCodeVerifier,
    StaticLeakVerifier,
    VerifyContext,
)


def _write_skill(root: Path, group: str, name: str, fm_body: str) -> Path:
    skill_dir = root / "skills" / group / name if group else root / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"{fm_body}\nBody.\n", encoding="utf-8")
    return skill_dir


class PolicyConfigTests(unittest.TestCase):
    def test_default_policy_when_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            repo = tmp_path / "repo"
            repo.mkdir()
            (repo / "skills.toml").write_text(
                f'''canonical_root = "{repo}"
columns = ["codex"]

[targets]
codex = "~/.codex/skills"
''',
                encoding="utf-8",
            )
            manifest = ManifestStore.load_skills_manifest(repo, home=home)
            self.assertEqual(manifest.targets["codex"], home / ".codex" / "skills")
            self.assertEqual(manifest.policy.orca_skills, ("orca-cli", "orchestration"))
            self.assertEqual(manifest.policy.omarchy_prefixes, (Path("/usr/share/omarchy"),))
            self.assertEqual(manifest.policy.system_entries, (".system",))
            self.assertEqual(
                manifest.policy.shared_dirs,
                (home / ".agents" / "skills", home / ".gemini" / "skills"),
            )
            self.assertEqual(
                manifest.policy.strict_shared_dirs,
                (home / ".agents" / "skills",),
            )
            self.assertEqual(manifest.policy.claude_skills_dir, home / ".claude" / "skills")
            self.assertEqual(
                manifest.policy.leak_configs,
                (
                    LeakConfigSpec(home / ".gemini" / "config" / "skills.json", ("entries", "inherits")),
                    LeakConfigSpec(home / ".config" / "opencode" / "opencode.json", ("skills",)),
                ),
            )

    def test_custom_policy_overrides_with_home_expansion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "custom-home"
            repo = tmp_path / "repo"
            repo.mkdir()
            (repo / "skills.toml").write_text(
                f'''canonical_root = "{repo}"
columns = ["codex", "opencode"]

[targets]
codex = "~/.codex/skills"
opencode = "~/.config/opencode/skills"

[policy]
orca_skills = ["custom-orca"]
omarchy_prefixes = ["~/vendor/omarchy"]
system_entries = [".sys"]
shared_dirs = ["~/shared/a", "~/shared/b"]
strict_shared_dirs = ["~/shared/a"]
claude_skills_dir = "~/alt-claude/skills"

[[policy.leak_configs]]
path = "~/custom/leak.json"
keys = ["paths", "extra"]
''',
                encoding="utf-8",
            )
            manifest = ManifestStore.load_skills_manifest(repo, home=home)
            p = manifest.policy
            self.assertEqual(p.orca_skills, ("custom-orca",))
            self.assertEqual(p.omarchy_prefixes, (home / "vendor" / "omarchy",))
            self.assertEqual(p.system_entries, (".sys",))
            self.assertEqual(p.shared_dirs, (home / "shared" / "a", home / "shared" / "b"))
            self.assertEqual(p.strict_shared_dirs, (home / "shared" / "a",))
            self.assertEqual(p.claude_skills_dir, home / "alt-claude" / "skills")
            self.assertEqual(
                p.leak_configs,
                (LeakConfigSpec(home / "custom" / "leak.json", ("paths", "extra")),),
            )

    def test_verifiers_honor_custom_policy_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            repo = tmp_path / "repo"
            t_oc = tmp_path / "targets" / "opencode"
            t_agy = tmp_path / "targets" / "agy"
            strict_dir = home / "strict-shared"
            other_shared = home / "other-shared"
            omarchy_root = home / "omarchy-src"
            alt_claude = home / "alt-claude" / "skills"
            leak_file = home / "custom-cfg.json"

            _write_skill(repo, "custom", "s1", "---\nname: s1\ndescription: S1\n---")
            _write_skill(repo, "custom", "s2", "---\nname: s2\ndescription: S2\n---")

            t_oc.mkdir(parents=True)
            t_agy.mkdir(parents=True)
            (t_oc / "s1").symlink_to(repo / "skills" / "custom" / "s1")
            (t_agy / "s2").symlink_to(repo / "skills" / "custom" / "s2")

            strict_dir.mkdir(parents=True)
            (strict_dir / "my-orca").mkdir()
            omarchy_skill = omarchy_root / "omarchy-ok"
            omarchy_skill.mkdir(parents=True)
            (strict_dir / "omarchy-ok").symlink_to(omarchy_skill)
            (strict_dir / "rogue-entry").mkdir()

            other_shared.mkdir(parents=True)
            (other_shared / "s2").mkdir()  # collides with antigravity=1

            leak_file.parent.mkdir(parents=True, exist_ok=True)
            leak_file.write_text(
                json.dumps({"paths": [str(repo / "skills" / "custom" / "s1")]}),
                encoding="utf-8",
            )

            (repo / "skills.toml").write_text(
                f'''canonical_root = "{repo}"
columns = ["opencode", "antigravity"]

[targets]
opencode = "{t_oc}"
antigravity = "{t_agy}"

[policy]
orca_skills = ["my-orca"]
omarchy_prefixes = ["~/omarchy-src"]
shared_dirs = ["~/strict-shared", "~/other-shared"]
strict_shared_dirs = ["~/strict-shared"]
claude_skills_dir = "~/alt-claude/skills"

[[policy.leak_configs]]
path = "~/custom-cfg.json"
keys = ["paths"]

[skills."custom"]
s1 = [1, 0]
s2 = [0, 1]
''',
                encoding="utf-8",
            )

            manifest = ManifestStore.load_skills_manifest(repo, home=home)
            disk_skills = {s.name: s for s in SkillDiscovery.discover(repo)}
            ctx = VerifyContext(repo_root=repo, manifest=manifest, disk_skills=disk_skills, home=home)

            res_v1 = StaticLeakVerifier().verify(ctx)
            self.assertTrue(
                any("rogue-entry" in f for f in res_v1.failures), res_v1.failures
            )
            self.assertFalse(
                any("my-orca" in f or "omarchy-ok" in f for f in res_v1.failures),
                res_v1.failures,
            )
            self.assertTrue(
                any("s2" in f and "collides" in f for f in res_v1.failures),
                res_v1.failures,
            )
            self.assertTrue(
                any("points into repo" in f for f in res_v1.failures),
                res_v1.failures,
            )

            oc_payload = json.dumps(
                {
                    "data": [
                        {
                            "id": "s1",
                            "name": "s1",
                            "path": str(repo / "skills" / "custom" / "s1" / "SKILL.md"),
                        },
                        {
                            "id": "leaked",
                            "name": "leaked",
                            "path": str(alt_claude / "leaked" / "SKILL.md"),
                        },
                    ]
                }
            )
            with mock.patch("shutil.which", return_value="/usr/bin/opencode"), mock.patch(
                "subprocess.run",
                return_value=mock.Mock(returncode=0, stdout=oc_payload, stderr=""),
            ):
                res_v3 = OpenCodeVerifier().verify(ctx)
            self.assertTrue(
                any("leaked" in f and str(alt_claude) in f for f in res_v3.failures),
                res_v3.failures,
            )


class SurgicalTomlWriterTests(unittest.TestCase):
    def test_update_upstream_commit_preserves_comments_and_other_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            toml_path = Path(tmp) / "upstreams.toml"
            original = """# Top header comment
[defaults]
mode = "mirror"

[sources.herdr]
# Inline section comment
repo   = "herdrdev/herdr"
ref    = "main"
commit = "1111111111111111111111111111111111111111"  # pinned sha
group  = "imported/misc"
mode   = "mirror"

[sources.herdr.skills]
herdr = "skills/herdr"
"""
            toml_path.write_text(original, encoding="utf-8")
            new_sha = "2222222222222222222222222222222222222222"
            ManifestStore.update_upstream_commit(toml_path, "herdr", new_sha)

            updated = toml_path.read_text(encoding="utf-8")
            self.assertIn("# Top header comment", updated)
            self.assertIn("# Inline section comment", updated)
            self.assertIn(f'commit = "{new_sha}"  # pinned sha', updated)
            parsed = tomllib.loads(updated)
            self.assertEqual(parsed["sources"]["herdr"]["commit"], new_sha)
            self.assertEqual(parsed["sources"]["herdr"]["skills"]["herdr"], "skills/herdr")

    def test_upsert_upstream_source_insert_and_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            toml_path = Path(tmp) / "upstreams.toml"
            src1 = UpstreamSource(
                id="alpha",
                repo="org/alpha",
                ref="main",
                commit="aaaa1111",
                group="imported/alpha",
                mode="mirror",
                skills={"skill-a": "skills/skill-a"},
                watch_roots=["skills"],
                license_path="LICENSE",
                license_dest="licenses/alpha-MIT.txt",
            )
            ManifestStore.upsert_upstream_source(toml_path, src1)
            first_text = toml_path.read_text(encoding="utf-8")
            parsed1 = tomllib.loads(first_text)
            self.assertEqual(parsed1["sources"]["alpha"]["commit"], "aaaa1111")
            self.assertEqual(parsed1["sources"]["alpha"]["skills"]["skill-a"], "skills/skill-a")

            # Add a comment manually, then upsert with new commit and a second skill.
            toml_path.write_text("# Keep me\n" + first_text, encoding="utf-8")
            src1_updated = UpstreamSource(
                id="alpha",
                repo="org/alpha",
                ref="main",
                commit="bbbb2222",
                group="imported/alpha",
                mode="mirror",
                skills={"skill-a": "skills/skill-a", "skill-b": "skills/skill-b"},
            )
            ManifestStore.upsert_upstream_source(toml_path, src1_updated)
            second_text = toml_path.read_text(encoding="utf-8")
            self.assertIn("# Keep me", second_text)
            parsed2 = tomllib.loads(second_text)
            self.assertEqual(parsed2["sources"]["alpha"]["commit"], "bbbb2222")
            self.assertEqual(
                parsed2["sources"]["alpha"]["skills"],
                {"skill-a": "skills/skill-a", "skill-b": "skills/skill-b"},
            )

    def test_append_skill_rows_preserves_alignment_and_comments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            toml_path = Path(tmp) / "skills.toml"
            toml_path.write_text(
                '''canonical_root = "~/dev/opencode-skills"
columns = ["codex", "antigravity", "opencode", "claude", "pi"]

[targets]
codex = "~/.codex/skills"

#                                  cdx agy oc  cld pi
[skills."imported/misc"]
browser-use                   = [  1,  1,  1,  1,  1 ]
''',
                encoding="utf-8",
            )
            cols = ["codex", "antigravity", "opencode", "claude", "pi"]
            ManifestStore.append_skill_rows(
                toml_path,
                "imported/misc",
                {"new-skill": [0, 1, 1, 0, 0]},
                cols,
            )
            ManifestStore.append_skill_rows(
                toml_path,
                "imported/newpack",
                {"pack-skill": [0, 0, 1, 0, 0]},
                cols,
            )

            text = toml_path.read_text(encoding="utf-8")
            self.assertIn("#                                  cdx agy oc  cld pi", text)
            self.assertIn("new-skill                     = [  0,  1,  1,  0,  0 ]", text)
            self.assertIn('[skills."imported/newpack"]', text)
            self.assertIn("pack-skill                    = [  0,  0,  1,  0,  0 ]", text)
            parsed = tomllib.loads(text)
            self.assertEqual(
                parsed["skills"]["imported/misc"]["new-skill"],
                [0, 1, 1, 0, 0],
            )
            self.assertEqual(
                parsed["skills"]["imported/newpack"]["pack-skill"],
                [0, 0, 1, 0, 0],
            )


class FrontmatterEdgeCaseTests(unittest.TestCase):
    def test_unquoted_description_with_embedded_quotes_and_em_dash(self) -> None:
        text = """---
name: brainstorming
description: You MUST use this before any creative work — creating features, "new components", adding functionality, or modifying behavior: even "small" tweaks.
---
Body.
"""
        fm = FrontmatterParser.parse_frontmatter(text)
        self.assertEqual(fm["name"], "brainstorming")
        self.assertEqual(
            fm["description"],
            'You MUST use this before any creative work — creating features, "new components", adding functionality, or modifying behavior: even "small" tweaks.',
        )

    def test_multiline_json_metadata_block_inline_and_indented(self) -> None:
        inline_text = """---
name: browser-use
description: Browser automation
metadata: {
  "clawdbot": {
    "emoji": "🌐",
    "requires": {
      "bins": ["browser-use"]
    }
  }
}
---
Body.
"""
        fm_inline = FrontmatterParser.parse_frontmatter(inline_text)
        self.assertEqual(fm_inline["name"], "browser-use")
        self.assertEqual(
            fm_inline["metadata"],
            {"clawdbot": {"emoji": "🌐", "requires": {"bins": ["browser-use"]}}},
        )

        indented_text = """---
name: browser-use
description: Browser automation
metadata:
  {
    "clawdbot": {
      "emoji": "🌐"
    }
  }
---
Body.
"""
        fm_indented = FrontmatterParser.parse_frontmatter(indented_text)
        self.assertEqual(fm_indented["metadata"], {"clawdbot": {"emoji": "🌐"}})


if __name__ == "__main__":
    unittest.main()

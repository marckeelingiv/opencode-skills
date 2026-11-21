# Personal multi-harness skills

Single-source repository for user-scoped agent skills deployed across Codex, Antigravity, OpenCode, Claude Code, and Pi via per-skill symlinks.

See [UPSTREAM.md](UPSTREAM.md) for provenance, [LICENSE](LICENSE) for Matt Pocock's MIT license, [licenses/pstack-MIT.txt](licenses/pstack-MIT.txt) for Cursor pstack's MIT notice, and [licenses/herdr-Apache-2.0.txt](licenses/herdr-Apache-2.0.txt) for Herdr's Apache-2.0 license.

## Repository layout

- `skills/imported/matt-pocock/<id>/` — unmodified upstream copies from Matt Pocock's skills repository.
- `skills/imported/pstack/<id>/` — selected skills from Cursor's `pstack` plugin with documented OpenCode adaptations.
- `skills/imported/misc/<id>/` — standalone third-party/adopted skills (`browser-use`, `find-skills`, `graphify`, `herdr`, `ponytail`).
- `skills/custom/<id>/` — personal skills (`omarchy-theme`, `orchestrate`, `ynab-amazon-categorize`).
- `skills.toml` — declarative enablement matrix, target directories, and `[requires]` dependency rules.
- `scripts/skills.py` — validation, symlink sync, status, and live discovery verification tool.

## `skills.toml` manifest

`skills.toml` maps each skill group under `skills/<group>/` to a binary row aligned with `columns`:

```toml
columns = ["codex", "antigravity", "opencode", "claude", "pi"]

[targets]
codex       = "~/.codex/skills"
antigravity = "~/.gemini/config/skills"
opencode    = "~/.config/opencode/skills"
claude      = "~/.claude/skills"
pi          = "~/.pi/agent/skills"

#                  cdx agy oc  cld pi
[skills."custom"]
orchestrate      = [  0,  1,  1,  0,  0 ]
```

- `1` links `<repo>/skills/<group>/<skill>` into the column's target directory; `0` leaves it unlinked (and removes any existing repo-owned symlink there).
- `[requires]` declares prerequisite skills that must also be `1` for every column where the dependent skill is `1`.
- To add a new harness column: append its key to `columns`, add its destination directory under `[targets]`, and append a `0` or `1` to every skill row.

## CLI usage

Run from the canonical checkout (`~/dev/opencode-skills`):

```bash
uv run python scripts/skills.py status
uv run python scripts/skills.py sync --dry-run
uv run python scripts/skills.py sync
uv run python scripts/skills.py verify
uv run python scripts/skills.py verify --live-agy
```

- `sync`: validates `skills.toml` and `SKILL.md` frontmatter, creates missing repo symlinks, updates drifted repo symlinks, unlinks disabled repo symlinks, and leaves unmanaged directories/foreign symlinks untouched. Refuses linked git worktrees unless `--allow-worktree` is passed.
- `status`: prints the aligned matrix alongside any unmanaged entries per target and in `~/.agents/skills` / `~/.gemini/skills`.
- `verify`: audits static links, config-file leaks, `~/.agents/skills` and `~/.gemini/skills` hygiene, and live Codex (`codex debug prompt-input`) and OpenCode (`opencode api skill.list`) discovery. Pass `--live-agy` to include live Antigravity discovery.

## Host isolation policies

- **`~/.agents/skills` is system-only**: reserved for OS/tool-managed skills (Omarchy links under `/usr/share/omarchy/...` and Orca skills `orca-cli` and `orchestration`). Do not install personal or adopted skills there, or multi-harness isolation breaks.
- **Prevent Claude-to-OpenCode leakage**: set `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1` in the shell environment so OpenCode does not read `~/.claude/skills` and load Claude-only skills (such as `omarchy-theme`).

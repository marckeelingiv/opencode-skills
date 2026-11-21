# Personal multi-harness skills

Single-source repository for user-scoped agent skills deployed across Codex, Antigravity, OpenCode, Claude Code, and Pi via per-skill symlinks.

See [UPSTREAM.md](UPSTREAM.md) for provenance, [upstreams.toml](upstreams.toml) for machine-readable upstream tracking, [LICENSE](LICENSE) for Matt Pocock's MIT license, [licenses/pstack-MIT.txt](licenses/pstack-MIT.txt) for Cursor pstack's MIT notice, [licenses/herdr-Apache-2.0.txt](licenses/herdr-Apache-2.0.txt) for Herdr's Apache-2.0 license, and [licenses/superpowers-MIT.txt](licenses/superpowers-MIT.txt) for Obra superpowers' MIT license.

## Repository layout

- `skills/imported/matt-pocock/<id>/` — unmodified upstream copies from Matt Pocock's skills repository.
- `skills/imported/pstack/<id>/` — selected skills from Cursor's `pstack` plugin with documented OpenCode adaptations.
- `skills/imported/superpowers/<id>/` — unmodified upstream copies from `obra/superpowers`.
- `skills/imported/misc/<id>/` — standalone third-party/adopted skills (`browser-use`, `find-skills`, `graphify`, `herdr`, `ponytail`).
- `skills/custom/<id>/` — personal skills (`omarchy-theme`, `orchestrate`, `ynab-amazon-categorize`).
- `skills.toml` — declarative enablement matrix, target directories, `[requires]` dependency rules, and `[policy]` host hygiene configuration.
- `upstreams.toml` — upstream repositories, refs, pinned commits, update modes (`mirror`, `adapted`, `notify`), and skill path mappings.
- `scripts/skills.py` — CLI entry point delegating to `scripts/skillctl/`.
- `scripts/skillctl/` — modular implementation:
  - `cli.py` — command-line parser and dispatch
  - `config.py` — manifest loading (`skills.toml`, `upstreams.toml`), `[policy]` model, and surgical TOML writers
  - `frontmatter.py` — skill discovery, `SKILL.md` frontmatter, and `agents/openai.yaml` parsing
  - `validator.py` — manifest, frontmatter, and dependency validation
  - `syncer.py` — symlink sync and status reporting
  - `verifiers.py` — static hygiene, leak checks, and live harness discovery verification
  - `github.py` — GitHub API / `gh` CLI transport for upstream operations
  - `upstreams.py` — upstream check, update, and skill import workflows
- `.github/workflows/upstream-check.yml` — scheduled weekly and manual GitHub Actions workflow to check/update tracked upstreams and open PRs or review issues.

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

[requires]
grill-with-docs = ["grilling", "domain-modeling"]

[policy]
orca_skills = ["orca-cli", "orchestration"]
omarchy_prefixes = ["/usr/share/omarchy"]
system_entries = [".system"]
shared_dirs = ["~/.agents/skills", "~/.gemini/skills"]
strict_shared_dirs = ["~/.agents/skills"]
claude_skills_dir = "~/.claude/skills"
leak_configs = [
  { path = "~/.gemini/config/skills.json", keys = ["entries", "inherits"] },
  { path = "~/.config/opencode/opencode.json", keys = ["skills"] },
]

#                  cdx agy oc  cld pi
[skills."custom"]
orchestrate      = [  0,  1,  1,  0,  0 ]
```

- `1` links `<repo>/skills/<group>/<skill>` into the column's target directory; `0` leaves it unlinked (and removes any existing repo-owned symlink there).
- `[requires]` declares prerequisite skills that must also be `1` for every column where the dependent skill is `1`.
- `[policy]` configures system skill allowlists, shared-directory hygiene checks, and JSON config files audited for repo path leaks.
- To add a new harness column: append its key to `columns`, add its destination directory under `[targets]`, and append a `0` or `1` to every skill row.

## `upstreams.toml` manifest

`upstreams.toml` tracks each upstream source under `[sources.<id>]` with `repo`, `ref`, `commit`, `group`, `mode` (`mirror`, `adapted`, or `notify`), optional `watch_roots`, `license_path` / `license_dest`, `notes`, and `[sources.<id>.skills]` mapping local skill names to upstream paths.

## CLI usage

Run from the canonical checkout (`~/dev/opencode-skills`):

```bash
uv run python scripts/skills.py validate
uv run python scripts/skills.py status
uv run python scripts/skills.py sync --dry-run
uv run python scripts/skills.py sync
uv run python scripts/skills.py verify
uv run python scripts/skills.py verify --live-agy
uv run python scripts/skills.py upstream check
uv run python scripts/skills.py upstream update
uv run python scripts/skills.py upstream add owner/repo --all --group imported/<id>
```

- `validate`: validates `skills.toml`, `SKILL.md` frontmatter, and `[requires]` dependencies without touching target directories.
- `sync`: validates `skills.toml` and `SKILL.md` frontmatter, creates missing repo symlinks, updates drifted repo symlinks, unlinks disabled repo symlinks, and leaves unmanaged directories/foreign symlinks untouched. Refuses linked git worktrees unless `--allow-worktree` is passed.
- `status`: prints the aligned matrix alongside any unmanaged entries per target and in configured shared directories.
- `verify`: audits static links, config-file leaks, shared-directory hygiene, and live Codex (`codex debug prompt-input`) and OpenCode (`opencode api skill.list`) discovery. Pass `--live-agy` to include live Antigravity discovery.
- `upstream check`: checks tracked sources in `upstreams.toml` against upstream HEAD commits, changed skills, and newly discovered skills under `watch_roots`.
- `upstream update`: updates `mirror` sources in place, generates diffs or 3-way merges for `adapted` sources, and reports alerts for `notify` sources.
- `upstream add`: fetches skills from a GitHub repository, copies files preserving executable permissions, updates `upstreams.toml`, and appends `[0, ...]` rows in `skills.toml`.

## Host isolation policies

- **`~/.agents/skills` is system-only**: reserved for OS/tool-managed skills (Omarchy links under `/usr/share/omarchy/...` and Orca skills `orca-cli` and `orchestration`). Do not install personal or adopted skills there, or multi-harness isolation breaks.
- **Prevent Claude-to-OpenCode leakage**: set `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1` in the shell environment so OpenCode does not read `~/.claude/skills` and load Claude-only skills (such as `omarchy-theme`).

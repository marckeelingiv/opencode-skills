# Upstream provenance

Machine-readable upstream source definitions, commit pins, update modes, and path mappings live in [upstreams.toml](upstreams.toml).

## Herdr

- Source: https://github.com/herdrdev/herdr
- Commit: `ee5bfb5041bb3e4a49d591c72308aae3c81ecdde`
- Source mapping: `skills/herdr/SKILL.md` → `skills/imported/misc/herdr/SKILL.md`.
- License: Apache-2.0; upstream license retained in `licenses/herdr-Apache-2.0.txt`.
- Local modifications to imported files: none.

## Matt Pocock skills

- Source: https://github.com/mattpocock/skills
- Commit: `4ae2317ddb22b046e26455e4f9c9b3aaaf62212d`
- License: MIT; upstream copyright and license retained in `LICENSE`.
- Local modifications to imported files: none.

### Source mapping

| Local directory | Upstream directory |
| --- | --- |
| `skills/imported/matt-pocock/wizard` | `skills/engineering/wizard` |
| `skills/imported/matt-pocock/grill-with-docs` | `skills/engineering/grill-with-docs` |
| `skills/imported/matt-pocock/improve-codebase-architecture` | `skills/engineering/improve-codebase-architecture` |
| `skills/imported/matt-pocock/codebase-design` | `skills/engineering/codebase-design` |
| `skills/imported/matt-pocock/retro` | `skills/engineering/retro` |
| `skills/imported/matt-pocock/domain-modeling` | `skills/engineering/domain-modeling` |
| `skills/imported/matt-pocock/grilling` | `skills/productivity/grilling` |
| `skills/imported/matt-pocock/writing-for-agents` | `skills/productivity/writing-for-agents` |
| `skills/imported/matt-pocock/teach` | `skills/productivity/teach` |
| `skills/imported/matt-pocock/handoff` | `skills/productivity/handoff` |

All files in each selected upstream directory were copied, including templates, references, and agent metadata.

## Cursor pstack skills

- Source: https://github.com/cursor/plugins/tree/main/pstack
- Commit: `2776ddd2355d6d6c8747d3964600f28daf62f049`
- License: MIT; original notice retained in `licenses/pstack-MIT.txt`.
- Source mapping: `pstack/skills/<id>/` → `skills/imported/pstack/<id>/`.
- Selected IDs: `benchmark-checklist`, `create-verification-skill`, `maintain-verification-skill`, `show-me-your-work`, `how`, `why`, `unslop`.
- All supporting files in the selected directories were included.

### OpenCode adaptations

- `how`: replace Cursor Task/model-rule configuration with fresh OpenCode general subagents, user-requested model discovery, read-only investigation, and inline fallback when delegation is unavailable.
- `why`: same delegation adaptation; use exposed OpenCode tools and MCP resource discovery instead of Cursor's MCP directory; report source-access gaps and include CLI-backed evidence.
- `create-verification-skill`: generate project skills under `.opencode/skills/` instead of `.cursor/skills/`; remove the incorrect claim that missing frontmatter prevents registration in OpenCode.
- `maintain-verification-skill`: discover `.opencode/skills/` targets and use fresh OpenCode subagents with an inline fallback.
- `show-me-your-work`: audit scoped OpenCode conversation evidence rather than Cursor transcript directories; use available OpenCode reviewers without hard-coded model families; disclose review gaps; inline the small evidence-reproducibility rule instead of importing an unrelated principle skill.
- `benchmark-checklist` and `unslop`: unchanged upstream contents.

Pstack playbook mentions in `benchmark-checklist` are contextual references, not installed workflows. No pstack mode, model configuration, or other unselected skills are required by these adaptations. Existing repository instructions and permission boundaries take precedence.

## Adopted local skills (`skills/imported/misc/`)

These skills were adopted from local installations in `~/.agents/skills/` without upstream commit history recorded locally; their upstream GitHub repositories are tracked in [upstreams.toml](upstreams.toml):

- `skills/imported/misc/browser-use`: copied from `~/.agents/skills/browser-use/SKILL.md`. Upstream repo: `https://github.com/browser-use/browser-use` (`skills/browser-use`); no local commit hash or license file present, coupled to local `browser-use` CLI.
- `skills/imported/misc/find-skills`: copied from `~/.agents/skills/find-skills/SKILL.md`. Upstream repo: `https://github.com/vercel-labs/skills` (`skills/find-skills`); no local commit hash or license file present.
- `skills/imported/misc/graphify`: copied from `~/.agents/skills/graphify/` (`SKILL.md`, `BUILD.md`, `references/`, `.graphify_version` = `0.9.100`). Upstream repo: `https://github.com/Graphify-Labs/graphify` (`v8` branch, `graphify/skill-agents.md` and `graphify/skills/agents/references`); tracked in `notify` mode because the skill is assembled by the local `graphify` CLI.
- `skills/imported/misc/ponytail`: copied from `~/.agents/skills/ponytail/SKILL.md`. Upstream repo: `https://github.com/DietrichGebert/ponytail` (`skills/ponytail`); frontmatter declares `license: MIT`, no local commit hash present.

## Obra superpowers skills

- Source: https://github.com/obra/superpowers
- Commit: `22f0b463e7a5e579904dd6211a520321035526f7`
- License: MIT; upstream license retained in `licenses/superpowers-MIT.txt`.
- Source mapping: `skills/<id>/` → `skills/imported/superpowers/<id>/`.
- Selected IDs: `brainstorming`, `diagnosing-superpowers`, `dispatching-parallel-agents`, `executing-plans`, `finishing-a-development-branch`, `receiving-code-review`, `requesting-code-review`, `subagent-driven-development`, `systematic-debugging`, `test-driven-development`, `using-git-worktrees`, `using-superpowers`, `verification-before-completion`, `writing-plans`, `writing-skills`.
- Local modifications to imported files: none.

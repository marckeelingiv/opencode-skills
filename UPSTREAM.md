# Upstream provenance

## Matt Pocock skills

- Source: https://github.com/mattpocock/skills
- Commit: `4ae2317ddb22b046e26455e4f9c9b3aaaf62212d`
- License: MIT; upstream copyright and license retained in `LICENSE`.
- Local modifications to imported files: none.

## Source mapping

| Local directory | Upstream directory |
| --- | --- |
| `skills/wizard` | `skills/engineering/wizard` |
| `skills/grill-with-docs` | `skills/engineering/grill-with-docs` |
| `skills/improve-codebase-architecture` | `skills/engineering/improve-codebase-architecture` |
| `skills/codebase-design` | `skills/engineering/codebase-design` |
| `skills/retro` | `skills/engineering/retro` |
| `skills/domain-modeling` | `skills/engineering/domain-modeling` |
| `skills/grilling` | `skills/productivity/grilling` |
| `skills/writing-for-agents` | `skills/productivity/writing-for-agents` |
| `skills/teach` | `skills/productivity/teach` |
| `skills/handoff` | `skills/productivity/handoff` |

All files in each selected upstream directory were copied, including templates, references, and agent metadata.

## Cursor pstack skills

- Source: https://github.com/cursor/plugins/tree/main/pstack
- Commit: `2776ddd2355d6d6c8747d3964600f28daf62f049`
- License: MIT; original notice retained in `licenses/pstack-MIT.txt`.
- Source mapping: `pstack/skills/<id>/` → `skills/<id>/`.
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

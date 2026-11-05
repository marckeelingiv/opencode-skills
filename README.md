# Personal OpenCode skills

User-scoped skills for OpenCode, copied from [Matt Pocock's skills](https://github.com/mattpocock/skills). See [UPSTREAM.md](UPSTREAM.md) for provenance and the retained MIT license in [LICENSE](LICENSE).

## Included skills

- `wizard`
- `grill-with-docs`
- `improve-codebase-architecture`
- `codebase-design`
- `retro`
- `teach`
- `handoff`
- Supporting skills: `grilling`, `domain-modeling`, `writing-for-agents`

## Install for your OpenCode user

Clone this repository to `~/dev/opencode-skills`. Add this entry to the `skills` array in `~/.config/opencode/opencode.json`, preserving other configuration:

```json
"skills": ["~/dev/opencode-skills/skills"]
```

This registers the skills across your OpenCode projects, without installing them in project directories or other agents' shared discovery directories. OpenCode reloads configuration automatically; `/reload` forces a refresh.

Invoke a skill by mentioning its ID, for example `@grill-with-docs` or `@retro`. Upstream's manual-only settings are preserved for `grill-with-docs`, `improve-codebase-architecture`, `retro`, `teach`, and `handoff`.

## Maintenance

Skill contents and supporting files are unmodified upstream copies, including optional `agents/openai.yaml` metadata; OpenCode discovers the `SKILL.md` files. Changes here are independent of upstream and never update automatically.

To update, review upstream changes against the commit recorded in `UPSTREAM.md`, copy only the selected skill directories and their supporting files, and update provenance. Review new skill dependencies before accepting an update. Repository and agent instructions still take precedence over skill instructions.

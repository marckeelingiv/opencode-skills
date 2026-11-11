# Agent adapters

Discover capabilities per live installation. Herdr support for an agent kind
does not imply that agent exposes APIs, context counts, or compaction controls.

| Capability | Record before relying on it |
| --- | --- |
| Identity | Agent kind, session ID, pane, working directory, model |
| Delivery | Structured API or verified empty terminal input; submission acknowledgment |
| Modes | Native Plan/Build switch or an explicitly scoped planning turn |
| Context | Measured token count and source, or unknown |
| Compaction | Request mechanism and observable completion, or unsupported |
| Completion | Task receipt plus pending-job inventory; lifecycle status is advisory |

## OpenCode

Load the OpenCode skill. Prefer its supported tools; use its authenticated API
CLI for uncovered operations. Discover endpoint bodies from the running OpenAPI
contract instead of copying version-specific paths into this skill.

When a human draft occupies the TUI, send through the structured prompt API.
Preserve the draft and avoid terminal navigation that could submit or replace it.
Mode changes and prompt admission are separate observations; verify both.

For message reads, select the current task's final assistant text and needed
status fields. Exclude reasoning/internal metadata and tool payloads unless
diagnosing a particular tool result. CLI stdout can contain launcher chatter:
prefer machine-readable supported output and fail visibly on malformed data.

## Antigravity (`agy`)

Use Herdr's installed help for recognized agent control. Qualify a disposable,
authorized session before relying on unattended operation: identity discovery,
prompt delivery, planning response, completion notification, and background-job
behavior. Record which capabilities actually worked.

Treat API delivery, native modes, context reporting, and compaction as unknown
until verified. When structured delivery is unavailable, use a verified empty
input surface. If a human draft or dialog blocks delivery, coordinate with the
user or use an authorized fresh session. Preserve existing input.

If context cannot be measured, report that uncertainty and checkpoint into a
fresh session at bounded phase boundaries rather than claiming a token limit
was enforced. Require the same task receipts as for OpenCode.

## Other agents and models

Apply the capability table, not assumptions from another adapter. Select models
through the environment's discovery tools. A model's reasoning ability does not
establish its host's control or lifecycle capabilities.

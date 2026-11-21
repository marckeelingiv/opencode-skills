---
name: orchestrate
description: Coordinate user-requested work across agents and worktrees: assign ownership, approve plans, manage shared resources, verify completion, and integrate dependent PRs.
---

# Orchestrate

Act as coordinator: own decomposition, plan approval, cross-stream decisions,
and acceptance. Workers own scoped implementation. Use a strong reasoning model
for coordination when requested; discover available model IDs before selecting
them. Keep model choices configurable, and follow the user's selection.

This skill supplies the workflow, not terminal command syntax. For Herdr control,
load `herdr`, verify its environment requirement, and discover the installed CLI.
If unavailable, stop Herdr operations and report the missing prerequisite.
For OpenCode control, load `opencode` and consult the running API contract.
For provider-specific behavior, read [agent adapters](references/adapters.md).

## 1. Discover and establish ownership

Read repository instructions and inspect live agents, worktrees, dirty state,
PR bases/heads, and existing reports. Preserve unfamiliar changes. Reuse an
existing task owner when allowed by lifecycle instructions; fresh reviewers
must use fresh sessions. Create topology only within the user's authorization.

Create a run manifest using [the templates](references/records.md) in a durable,
user-accessible evidence directory. Record exact session and worktree identities,
not just pane labels. Give each task one owner, owned files, dependencies, and
checkable acceptance criteria. Record source changes, integration, deployment,
and external acceptance as separate deliverables.

**Complete when:** each task has an owner and acceptance criteria; overlapping
files and shared resources have explicit coordination decisions.

## 2. Review plans before Build

Ask each worker for a source-backed plan: files, intended behavior, validation,
resources, and blockers. Use native Plan mode when supported; otherwise require
a read-only planning response. Review plans against dependencies and user scope.
Approve Build explicitly and verify the mode transition where observable.

Parallelize independent tasks. Serialize edits to shared files, or give one
integrator ownership of them. Shared databases, browser targets, servers, and
deployment channels need an owner and a bounded access scope before use.

At dispatch and phase transitions, check context against the user's threshold
(default 150,000 tokens). Above it, request compaction and confirm a completed
checkpoint before continuing. If usage or compaction is unavailable, record that
limit and use an authorized fresh session with a bounded handoff. A request
accepted by the API is not proof that compaction completed.

**Complete when:** the approved plan, execution mode, context checkpoint, and
resource grants are recorded for each dispatched task.

## 3. Dispatch and supervise

Send task ID, owned scope, exact base revision, acceptance criteria, permitted
side effects, dependencies, and evidence destination. Preserve pending human
input: prefer structured delivery when supported. Never paste into an occupied
prompt or approval dialog. Record the submission ID when available.

Treat status as an observation, not a task verdict. `idle` or `done` can coexist
with background work. Wait for the assigned task's completion receipt and verify
its evidence. A timeout or ambiguous submission requires inspection before any
retry. Repeated waits on the same settled state do not establish progress.

Use event-driven notification where available. On a settled signal, inspect once:
accept a complete receipt, identify pending jobs, or request a bounded status
clarification. When jobs remain, subscribe to their completion or ask the worker
to notify the coordinator after receipt publication. Respect host polling rules.

Keep reads targeted: latest final text, concise status fields, or artifact paths.
Avoid dumping whole API messages, internal metadata, or full pane history.

For shared resources, record acquire, allowed operations, known consumers, and
release. Ask actual consumers to hold; if ownership is unknown, discover it or
coordinate broadly before granting access. A process listing supplements owner
confirmation; it does not prove the absence of all clients. On timeout, confirm
jobs have stopped before reassigning the resource. Release on success or failure
and notify affected owners. A release is not authorization for new work.

**Complete when:** each task has either a verifiable receipt or an explicit
blocker with an owner and next action; no background work is silently abandoned.

## 4. Review and integrate

Use a fresh independent reviewer for the exact candidate diff and acceptance
evidence. The reviewer owns judgment-based coding standards and evidence quality.
Use existing deterministic checks for mechanical requirements. Read actual check
scripts and CI before proposing new checks; distinguish failures from unwired
checks, unavailable services, and hosted execution blocks.

After a parent PR merges, refresh descendant bases and source identities. Squash
merges preserve content but change ancestry. Inspect the resulting diff; choose
a merge or scoped replay deliberately. Published-history rewrites require the
applicable authorization. Preserve approved behavior with targeted tests and,
where exact preservation is intended, file/blob comparisons.

Integrate only reviewed revisions. Verify the published head and PR base after
pushing. Record conflict-free mergeability separately from passing CI and human
sign-off. Merge and deployment each require their own user authorization.

For deployment, verify the exact artifact/source, runtime target, channel, and
receipt. A newer branch commit does not change the version already deployed.
Keep historical deployment receipts immutable; append a new receipt per release.

**Complete when:** the published revision matches the reviewed candidate and
every required acceptance condition has evidence or a clearly reported limit.

## 5. Close the run

Collect final receipts, release owned resources, and record retained resources
and their owners. Dispose only within explicit authorization. Update one durable
handoff with task/PR map, revisions, checks, review verdicts, deployment identity,
evidence links, and unresolved acceptance decisions.

Report the result concisely. Separate implementation complete, technically
validated, externally accepted, merged, and deployed. Log incidental tooling
improvements in the existing cleanup register rather than expanding worker scope.

**Complete when:** receipts reconcile with live state, affected owners received
resource releases, and the user can find the evidence and remaining decisions.

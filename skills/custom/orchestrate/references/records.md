# Run records

Keep one manifest per run and one receipt per task attempt. Store them beside
evidence in the user-requested durable location. JSON or YAML is suitable; these
are templates, not an implemented locking service or automatic validator.

## Manifest template

```yaml
run_id: <unique run ID>
goal: <user objective>
evidence_directory: <absolute path>
context_limit_tokens: 150000
tasks:
  - id: <task ID>
    owner:
      kind: <opencode|agy|other>
      session_id: <verified identity>
      pane_id: <verified pane>
      model: <discovered model ID>
      worktree: <absolute path>
    branch: <branch>
    base_sha: <exact SHA>
    owned_files: []
    depends_on: []
    acceptance: []
    authorized_operations: []
    phase: <planning|approved|running|reviewing|blocked|complete>
    context_checkpoint: <count/source or unknown; compaction completion evidence>
    receipt: <path when available>
resources:
  - id: <backend/browser/server/channel identity>
    owner_task: <task ID>
    known_consumers: []
    allowed_operations: []
    hold_acknowledgments: []
    acquired_at: <timestamp>
    review_at: <timestamp for rechecking ownership, not automatic reassignment>
    released_at: null
    release_notifications: []
```

## Completion receipt template

```yaml
run_id: <matching run>
task_id: <matching task>
attempt_id: <unique attempt>
status: <complete|blocked|failed>
source_sha: <exact tested source, or null for non-code tasks>
worktree_status: <clean or explicit remaining changes>
summary: <outcome>
acceptance_evidence:
  - criterion: <manifest criterion>
    result: <pass|fail|unverified>
    evidence: <durable path or URL>
checks:
  - command: <actual command>
    result: <exit/result and relevant count>
    log: <path>
review:
  reviewer_session: <fresh session identity or null>
  reviewed_sha: <SHA or null>
  verdict: <verdict or not performed>
  evidence: <path or null>
publication:
  pr_url: <URL or null>
  base: <verified base or null>
  head_sha: <verified published head or null>
  mergeability: <observed state or unknown>
  ci_status: <observed status or unknown>
deployment_receipt: <path or null; independent of branch head>
pending_jobs: []
resource_releases: []
retained_resources: []
limits: []
completed_at: <timestamp or null>
```

## Acceptance procedure

Match run/task/attempt IDs to the actual dispatch. Verify paths exist and source
identities match the tested/reviewed candidate. Account for every acceptance
criterion and inspect relevant evidence rather than trusting a summary alone.

`complete` requires no pending jobs for that task. Required but unverified
criteria make the task blocked or partial, not fully accepted. Retained resources
need named owners; released resources need notification evidence. A receipt may
record a blocked publication while implementation is finished: keep those states
distinct in the user handoff.

Automate these mechanical checks if a helper is added later, with tests for stale
SHAs, missing criteria/artifacts, pending jobs, and unreleased resources. Until
then, perform them explicitly; this template provides no enforcement by itself.

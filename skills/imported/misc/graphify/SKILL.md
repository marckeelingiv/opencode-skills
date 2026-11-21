---
name: graphify
description: "Use to query an existing graphify-out/graph.json, or when the user explicitly requests Graphify, a knowledge-graph build/update, or graph query/path/explain. Ordinary targeted source lookups without a graph use direct search."
---

# /graphify

Route first; load only the matching workflow. Skill resources are relative to this skill directory; `graphify-out/graph.json` is relative to the current working directory.

## Help

For `/graphify --help` or `/graphify -h` alone, print the `## Usage` section of [BUILD.md](BUILD.md#usage) verbatim and stop. Read only that section; run no commands, detection, or default-path resolution.

## Existing graph → query

For a natural-language question about the corpus, check for `graphify-out/graph.json`. If it exists, use it immediately rather than running detection, checking corpus size, asking to narrow, or rebuilding.

Read the [interpreter guard](references/interpreter.md), then [query, path, and explain guidance](references/query.md). Expand against the graph's vocabulary before `graphify query "<question>"`; the reference retains BFS/DFS, token budgets, NetworkX fallback, source-location citations, saved-result feedback, and reflection lessons. Answer only from graph evidence; never invent an edge.

Explicit `query`, `path`, and `explain` requests use the same guidance. If no graph exists, tell the user to run `/graphify <path>` first; the query request alone does not authorize a build.

## No graph → targeted search

For an ordinary targeted lookup such as “where is X defined?” or “what calls Y?”, use direct search/read and answer from source. Stop there; mention that a graph can be built if wanted.

For repeated questions, a large/unfamiliar corpus, cross-document links, or structural analysis (communities, bridges, import cycles), offer a build and wait for approval. AST extraction is fast and free; semantic extraction of docs, papers, and images can cost minutes and tens of thousands of tokens.

## Explicitly requested or approved build → full workflow

A bare `/graphify`, `/graphify <path>`, or `/graphify <URL>` explicitly requests the full pipeline, even if a graph exists. Default a missing path to `.` without asking. Read [BUILD.md](BUILD.md) and follow it in order; it retains usage, GitHub/multi-path handling, extraction, clustering, exports, reporting, and honesty rules.

## Other explicit operations

- `--update` / `--cluster-only`: read the [interpreter guard](references/interpreter.md), then [incremental update and clustering](references/update.md).
- `add <url>`: read the [interpreter guard](references/interpreter.md), then [add/watch](references/add-watch.md).
- `--watch`: read [add/watch](references/add-watch.md).
- Post-commit hook or native CLAUDE.md integration: read [hooks](references/hooks.md) only when requested.

These explicit operations take precedence over the natural-language query route; retain their supplied flags.

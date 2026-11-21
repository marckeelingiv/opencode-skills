#!/usr/bin/env python3
"""Manage and verify the skill matrix, target symlinks, and upstream sources."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from skillctl.cli import main as _cli_main  # noqa: E402
from skillctl.config import load_manifest  # noqa: E402
from skillctl.frontmatter import (  # noqa: E402
    discover_skills,
    parse_frontmatter,
    parse_openai_yaml,
)
from skillctl.syncer import PathOwnership  # noqa: E402
from skillctl.validator import validate  # noqa: E402
from skillctl.verifiers import (  # noqa: E402
    check_v2_codex as _check_v2_codex,
    check_v4_agy as _check_v4_agy,
    parse_codex_prompt_input,
    parse_opencode_skill_list,
)

DEFAULT_ROOT = Path(__file__).resolve().parent.parent
is_linked_worktree = PathOwnership.is_linked_worktree


def main(argv: Sequence[str] | None = None) -> int:
    """Delegate CLI execution using `DEFAULT_ROOT` as the fallback repo root."""
    return _cli_main(argv, default_root=DEFAULT_ROOT)


if __name__ == "__main__":
    sys.exit(main())

"""Stdlib-only skill matrix, validation, syncing, and verification package."""

from __future__ import annotations

from .config import (
    VALID_UPSTREAM_MODES,
    LeakConfigSpec,
    Manifest,
    ManifestStore,
    PolicyConfig,
    SkillInfo,
    UpstreamSource,
    UpstreamsManifest,
    load_manifest,
)
from .frontmatter import (
    FrontmatterParser,
    SkillDiscovery,
    discover_skills,
    parse_frontmatter,
    parse_openai_yaml,
)
from .syncer import PathOwnership, SkillSyncer, StatusReporter
from .validator import RepoValidator, validate
from .verifiers import (
    AntigravityVerifier,
    CodexVerifier,
    HarnessVerifier,
    OpenCodeVerifier,
    StaticLeakVerifier,
    VerificationResult,
    VerificationRunner,
    VerifyContext,
    check_v2_codex,
    check_v4_agy,
    parse_codex_prompt_input,
    parse_opencode_skill_list,
)

__all__ = [
    "AntigravityVerifier",
    "CodexVerifier",
    "FrontmatterParser",
    "HarnessVerifier",
    "LeakConfigSpec",
    "Manifest",
    "ManifestStore",
    "OpenCodeVerifier",
    "PathOwnership",
    "PolicyConfig",
    "RepoValidator",
    "SkillDiscovery",
    "SkillInfo",
    "SkillSyncer",
    "StaticLeakVerifier",
    "StatusReporter",
    "UpstreamSource",
    "UpstreamsManifest",
    "VALID_UPSTREAM_MODES",
    "VerificationResult",
    "VerificationRunner",
    "VerifyContext",
    "check_v2_codex",
    "check_v4_agy",
    "discover_skills",
    "load_manifest",
    "parse_codex_prompt_input",
    "parse_frontmatter",
    "parse_openai_yaml",
    "parse_opencode_skill_list",
    "validate",
]

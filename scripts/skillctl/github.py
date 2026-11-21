"""GitHub CLI and git transport for upstream skill tracking."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.parse import quote

_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_COMPARE_FILE_LIMIT = 300


class UpstreamTransportError(RuntimeError):
    """Raised when an upstream GitHub API or git operation fails."""


@dataclass
class RepoInfo:
    """Repository metadata resolved from GitHub."""

    repo: str
    default_branch: str
    head_commit: str
    license_spdx: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "default_branch": self.default_branch,
            "head_commit": self.head_commit,
            "license_spdx": self.license_spdx,
        }


@dataclass
class CommitSummary:
    """Compact summary of an upstream commit."""

    sha: str
    message: str
    date: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sha": self.sha,
            "message": self.message,
            "date": self.date,
        }


@dataclass
class PathCompareResult:
    """Comparison result scoped to tracked upstream paths."""

    base_commit: str
    head_commit: str
    changed_files: list[str] = field(default_factory=list)
    commits: list[CommitSummary] = field(default_factory=list)
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_commit": self.base_commit,
            "head_commit": self.head_commit,
            "changed_files": list(self.changed_files),
            "commits": [c.to_dict() for c in self.commits],
            "truncated": self.truncated,
        }


@runtime_checkable
class UpstreamTransport(Protocol):
    """Transport abstraction for querying and fetching upstream skill repositories."""

    def get_repo_info(self, repo: str, ref: str | None = None) -> RepoInfo: ...

    def get_head_commit(self, repo: str, ref: str) -> str: ...

    def compare_commits(
        self,
        repo: str,
        base_commit: str,
        head_commit: str,
        tracked_paths: list[str],
    ) -> PathCompareResult: ...

    def list_skill_dirs(
        self,
        repo: str,
        commit: str,
        roots: list[str] | None = None,
    ) -> list[str]: ...

    def fetch_paths(
        self,
        repo: str,
        commit: str,
        paths: list[str],
        dest_dir: Path,
    ) -> None: ...

    def diff_paths(
        self,
        repo: str,
        base_commit: str,
        head_commit: str,
        paths: list[str],
    ) -> str: ...


def validate_repo_slug(repo: str) -> str:
    """Validate and return an 'owner/name' GitHub repository slug."""
    cleaned = repo.strip().strip("/")
    if not _REPO_RE.match(cleaned):
        raise UpstreamTransportError(
            f"Invalid GitHub repository '{repo}'; expected 'owner/name'"
        )
    return cleaned


def normalize_repo_path(path: str) -> str:
    """Normalize a repository-relative path ('/' and '.' become '')."""
    cleaned = path.strip().strip("/")
    return "" if cleaned == "." else cleaned


def path_matches_prefix(filename: str, prefix: str) -> bool:
    """Return True if filename is inside or equal to normalized prefix."""
    norm_file = normalize_repo_path(filename)
    norm_prefix = normalize_repo_path(prefix)
    if norm_prefix == "":
        return True
    return norm_file == norm_prefix or norm_file.startswith(f"{norm_prefix}/")


def _summarize_commit(raw_commit: dict[str, Any]) -> CommitSummary:
    sha = str(raw_commit.get("sha") or "")[:12]
    commit_obj = raw_commit.get("commit") or {}
    raw_msg = str(commit_obj.get("message") or "")
    first_line = raw_msg.splitlines()[0].strip() if raw_msg else ""
    committer = commit_obj.get("committer") or {}
    author = commit_obj.get("author") or {}
    date = str(committer.get("date") or author.get("date") or "")
    return CommitSummary(sha=sha, message=first_line, date=date)


def _rewrite_diff_prefixes(diff_text: str, base_dir: Path, head_dir: Path) -> str:
    """Strip temporary directory prefixes from `git diff --no-index` output."""
    if not diff_text:
        return ""
    base_abs = str(base_dir).rstrip("/")
    head_abs = str(head_dir).rstrip("/")
    base_no_slash = base_abs.lstrip("/")
    head_no_slash = head_abs.lstrip("/")

    out_lines: list[str] = []
    for line in diff_text.splitlines(keepends=True):
        line = line.replace(f"a/{base_no_slash}/", "a/")
        line = line.replace(f"b/{head_no_slash}/", "b/")
        line = line.replace(f"{base_abs}/", "a/")
        line = line.replace(f"{head_abs}/", "b/")
        out_lines.append(line)
    return "".join(out_lines)


class GitHubCliTransport:
    """UpstreamTransport backed by `gh api` and `git` sparse-checkout."""

    def _run(
        self,
        cmd: list[str],
        *,
        cwd: Path | None = None,
        stdin_text: str | None = None,
        allowed_codes: tuple[int, ...] = (0,),
    ) -> subprocess.CompletedProcess[str]:
        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                input=stdin_text,
                text=True,
                capture_output=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise UpstreamTransportError(f"Required executable not found: {cmd[0]}") from exc
        except OSError as exc:
            raise UpstreamTransportError(f"Failed running {' '.join(cmd)}: {exc}") from exc

        if proc.returncode not in allowed_codes:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise UpstreamTransportError(
                f"Command failed ({proc.returncode}): {' '.join(cmd)}"
                + (f": {detail}" if detail else "")
            )
        return proc

    def _gh_api_json(self, endpoint: str) -> Any:
        proc = self._run(["gh", "api", endpoint])
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise UpstreamTransportError(
                f"Invalid JSON from `gh api {endpoint}`: {exc}"
            ) from exc

    def get_repo_info(self, repo: str, ref: str | None = None) -> RepoInfo:
        slug = validate_repo_slug(repo)
        data = self._gh_api_json(f"repos/{slug}")
        if not isinstance(data, dict):
            raise UpstreamTransportError(f"Unexpected repo response for {slug}")
        default_branch = str(data.get("default_branch") or "").strip()
        if not default_branch:
            raise UpstreamTransportError(f"Missing default_branch for {slug}")
        license_obj = data.get("license")
        license_spdx: str | None = None
        if isinstance(license_obj, dict):
            raw_spdx = license_obj.get("spdx_id")
            if isinstance(raw_spdx, str) and raw_spdx and raw_spdx != "NOASSERTION":
                license_spdx = raw_spdx
        target_ref = ref.strip() if ref else default_branch
        head_commit = self.get_head_commit(slug, target_ref)
        return RepoInfo(
            repo=slug,
            default_branch=default_branch,
            head_commit=head_commit,
            license_spdx=license_spdx,
        )

    def get_head_commit(self, repo: str, ref: str) -> str:
        slug = validate_repo_slug(repo)
        if not ref or not ref.strip():
            raise UpstreamTransportError(f"Empty ref for repository {slug}")
        encoded_ref = quote(ref.strip(), safe="")
        data = self._gh_api_json(f"repos/{slug}/commits/{encoded_ref}")
        if not isinstance(data, dict) or not isinstance(data.get("sha"), str) or not data["sha"]:
            raise UpstreamTransportError(
                f"Could not resolve commit SHA for {slug}@{ref}"
            )
        return data["sha"]

    def compare_commits(
        self,
        repo: str,
        base_commit: str,
        head_commit: str,
        tracked_paths: list[str],
    ) -> PathCompareResult:
        slug = validate_repo_slug(repo)
        normalized = [normalize_repo_path(p) for p in tracked_paths]
        if base_commit == head_commit:
            return PathCompareResult(
                base_commit=base_commit,
                head_commit=head_commit,
                changed_files=[],
                commits=[],
                truncated=False,
            )

        base_q = quote(base_commit, safe="")
        head_q = quote(head_commit, safe="")
        data = self._gh_api_json(f"repos/{slug}/compare/{base_q}...{head_q}")
        if not isinstance(data, dict):
            raise UpstreamTransportError(f"Unexpected compare response for {slug}")

        files = data.get("files") or []
        raw_commits = data.get("commits") or []
        commits = [
            _summarize_commit(c) for c in raw_commits if isinstance(c, dict)
        ]

        if len(files) < _COMPARE_FILE_LIMIT:
            changed_files: list[str] = []
            for entry in files:
                if not isinstance(entry, dict):
                    continue
                filename = entry.get("filename")
                if not isinstance(filename, str) or not filename:
                    continue
                if not normalized or any(
                    path_matches_prefix(filename, p) for p in normalized
                ):
                    changed_files.append(filename)
            return PathCompareResult(
                base_commit=base_commit,
                head_commit=head_commit,
                changed_files=changed_files,
                commits=commits,
                truncated=False,
            )

        # Fallback for >300-file compares (large monorepos such as cursor/plugins).
        changed_paths: list[str] = []
        fallback_commits: list[CommitSummary] = []
        seen_shas: set[str] = set()
        for path in normalized:
            endpoint = f"repos/{slug}/commits?sha={head_q}&per_page=1"
            if path:
                endpoint += f"&path={quote(path, safe='')}"
            items = self._gh_api_json(endpoint)
            if not isinstance(items, list) or not items:
                continue
            latest = items[0]
            if not isinstance(latest, dict):
                continue
            path_sha = str(latest.get("sha") or "")
            if not path_sha or path_sha == base_commit:
                continue
            path_cmp = self._gh_api_json(
                f"repos/{slug}/compare/{base_q}...{quote(path_sha, safe='')}"
            )
            if isinstance(path_cmp, dict) and path_cmp.get("status") in (
                "ahead",
                "diverged",
            ):
                changed_paths.append(path)
                summary = _summarize_commit(latest)
                if summary.sha and summary.sha not in seen_shas:
                    seen_shas.add(summary.sha)
                    fallback_commits.append(summary)

        return PathCompareResult(
            base_commit=base_commit,
            head_commit=head_commit,
            changed_files=changed_paths,
            commits=commits or fallback_commits,
            truncated=True,
        )

    def list_skill_dirs(
        self,
        repo: str,
        commit: str,
        roots: list[str] | None = None,
    ) -> list[str]:
        slug = validate_repo_slug(repo)
        commit_q = quote(commit, safe="")
        data = self._gh_api_json(f"repos/{slug}/git/trees/{commit_q}?recursive=1")
        if not isinstance(data, dict):
            raise UpstreamTransportError(f"Unexpected tree response for {slug}@{commit}")

        norm_roots = [normalize_repo_path(r) for r in (roots or [])]
        filter_by_roots = bool(norm_roots) and "" not in norm_roots

        dirs: set[str] = set()
        for entry in data.get("tree") or []:
            if not isinstance(entry, dict):
                continue
            if entry.get("type") not in (None, "blob"):
                continue
            path = entry.get("path")
            if not isinstance(path, str):
                continue
            if path != "SKILL.md" and not path.endswith("/SKILL.md"):
                continue
            if any(seg.startswith(".") for seg in path.split("/")):
                continue
            if filter_by_roots and not any(
                path == f"{root}/SKILL.md" or path.startswith(f"{root}/")
                for root in norm_roots
                if root
            ):
                continue
            parent = "" if path == "SKILL.md" else path[: -len("/SKILL.md")]
            dirs.add(parent)
        return sorted(dirs)

    def fetch_paths(
        self,
        repo: str,
        commit: str,
        paths: list[str],
        dest_dir: Path,
    ) -> None:
        slug = validate_repo_slug(repo)
        dest_dir.mkdir(parents=True, exist_ok=True)
        normalized = [normalize_repo_path(p) for p in paths]
        if not normalized:
            return

        patterns: list[str] = []
        for path in normalized:
            if path == "":
                patterns.append("/*")
            else:
                patterns.extend((f"/{path}", f"/{path}/**"))

        remote_url = f"https://github.com/{slug}.git"
        self._run(["git", "init"], cwd=dest_dir)
        self._run(["git", "remote", "add", "origin", remote_url], cwd=dest_dir)
        self._run(["git", "sparse-checkout", "init", "--no-cone"], cwd=dest_dir)
        self._run(
            ["git", "sparse-checkout", "set", "--no-cone", "--stdin"],
            cwd=dest_dir,
            stdin_text="\n".join(patterns) + "\n",
        )

        # Try shallow fetch first; fall back to blobless/full fetch if commit is not a ref tip.
        fetch_cmds = [
            ["git", "-c", "advice.detachedHead=false", "fetch", "--depth", "1", "origin", commit],
            ["git", "-c", "advice.detachedHead=false", "fetch", "--filter=blob:none", "origin", commit],
            ["git", "-c", "advice.detachedHead=false", "fetch", "origin", commit],
        ]
        last_err: UpstreamTransportError | None = None
        for cmd in fetch_cmds:
            try:
                self._run(cmd, cwd=dest_dir)
                last_err = None
                break
            except UpstreamTransportError as exc:
                last_err = exc
        if last_err is not None:
            raise last_err

        self._run(
            ["git", "-c", "advice.detachedHead=false", "checkout", "--force", "FETCH_HEAD"],
            cwd=dest_dir,
        )

    def diff_paths(
        self,
        repo: str,
        base_commit: str,
        head_commit: str,
        paths: list[str],
    ) -> str:
        if base_commit == head_commit or not paths:
            return ""
        with tempfile.TemporaryDirectory(prefix="skillctl-diff-") as tmp_root:
            root = Path(tmp_root)
            base_dir = root / "base"
            head_dir = root / "head"
            self.fetch_paths(repo, base_commit, paths, base_dir)
            self.fetch_paths(repo, head_commit, paths, head_dir)
            shutil.rmtree(base_dir / ".git", ignore_errors=True)
            shutil.rmtree(head_dir / ".git", ignore_errors=True)

            proc = self._run(
                ["git", "diff", "--no-index", "--", str(base_dir), str(head_dir)],
                allowed_codes=(0, 1),
            )
            return _rewrite_diff_prefixes(proc.stdout, base_dir, head_dir)

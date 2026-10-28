"""GitHub-issue queue provider: file an issue, poll for a `## Handback` comment, return it.

Useful when a human or another bot watches the repo and posts the answer. Not required for
the direct AI providers (xai, openai, openai_compatible, anthropic).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid

from .base import Provider, ProviderError, http_json


class GitHubIssueProvider(Provider):
    def token(self) -> str:
        for v in (self.cfg.get("api_key_env") or "GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN", "BATON_TOKEN", "CLAUDE_HANDOFF_TOKEN"):
            if os.environ.get(v):
                return os.environ[v]
        for gh in ("gh", "/opt/homebrew/bin/gh", "/usr/local/bin/gh"):
            try:
                out = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=10).stdout.strip()
                if out:
                    return out
            except (OSError, subprocess.SubprocessError):
                continue
        return ""

    def repo(self) -> str:
        r = self.cfg.get("repo") or os.environ.get("BATON_REPO") or os.environ.get("CLAUDE_HANDOFF_REPO") or ""
        if not r:
            raise ProviderError("github_issue: set providers.<name>.repo (owner/name)")
        return r

    def _call(self, method: str, path: str, body=None):
        tok = self.token()
        if not tok:
            raise ProviderError("github_issue: set GITHUB_TOKEN (or run `gh auth login`)")
        return http_json("https://api.github.com" + path, method,
                         {"Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json"},
                         body, timeout=30)

    def complete(self, task: str, context: dict = None, system: str = "") -> str:
        hid = uuid.uuid4().hex[:12]
        body = {"handoff_id": hid, "task": task[:8000], "kind": "decline"}
        if context:
            body["context"] = context
        while len(json.dumps(body)) > 60000 and body.get("context", {}).get("files"):
            f = next((f for f in reversed(body["context"]["files"]) if "content" in f), None)
            if not f:
                break
            f.pop("content", None)
            f["omitted"] = "too large for the issue"
        if len(json.dumps(body)) > 60000:
            body.pop("context", None)
        assignees = []
        if self.cfg.get("assignee"):
            assignees = [self.cfg["assignee"]]
        issue = self._call("POST", "/repos/%s/issues" % self.repo(), {
            "title": "handoff %s: %s" % (hid, task[:80].replace("\n", " ")),
            "labels": self.cfg.get("labels") or ["handoff"],
            "assignees": assignees,
            "body": "```json\n" + json.dumps(body, indent=2) + "\n```\n\n"
                    "When done, post a comment that starts with `## Handback` and close this issue.",
        })
        num = str(issue["number"])
        timeout = int(self.cfg.get("timeout_seconds") or self.cfg.get("wait_seconds") or 540)
        poll = int(self.cfg.get("poll_seconds") or 10)
        end = time.time() + timeout
        while True:
            d = self._call("GET", "/repos/%s/issues/%s" % (self.repo(), num))
            if d.get("state") == "closed":
                comments = self._call("GET", "/repos/%s/issues/%s/comments?per_page=100" % (self.repo(), num)) or []
                backs = [c.get("body") or "" for c in comments if "## Handback" in (c.get("body") or "")]
                if backs:
                    return backs[-1].replace("## Handback", "", 1).strip()
                return "(issue #%s closed with no ## Handback comment)" % num
            if time.time() + poll > end:
                raise ProviderError("github_issue: timed out waiting on #%s" % num)
            time.sleep(poll)

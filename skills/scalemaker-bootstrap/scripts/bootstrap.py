#!/usr/bin/env python3
"""Scalemaker Platform Bootstrap — skill router, syncer, and setup initializer.

Self-contained: stdlib only. No external dependencies.

Called by Claude Code in three modes:

  --setup <TOKEN>
      Invoked by the /scalemaker-setup slash command after plugin install.
      Writes the token, creates the ~/.claude/scalemaker/ directory layout,
      registers the Scalemaker Platform MCP server in ~/.claude/settings.json, and
      performs the initial skill sync.

  --sync-on-start
      Invoked by the SessionStart hook. Fast check+update with a short
      timeout. Fails silently if the API is unreachable.

  --query "<user request>"
      Called from within Claude Code before Scalemaker Platform workflows.
      Matches intent against the local workflow registry and routes.
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HOME = Path.home()
SCALEMAKER_DIR = HOME / ".claude" / "scalemaker"
TOKEN_FILE = SCALEMAKER_DIR / "token"
CONFIG_FILE = SCALEMAKER_DIR / "config.json"
LOCKFILE = SCALEMAKER_DIR / "sync.lock"
WORKFLOW_REGISTRY = SCALEMAKER_DIR / "workflow-registry.json"
SKILLS_DIR = HOME / ".claude" / "skills"
SETTINGS_FILE = HOME / ".claude" / "settings.json"

DEFAULT_MCP_URL = "https://mcp.scalemaker.frondorf.co"
SYNC_TIMEOUT = 15.0
SESSION_START_TIMEOUT = 10.0


def load_config() -> dict[str, Any]:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_config(cfg: dict[str, Any]) -> None:
    SCALEMAKER_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def load_token() -> str | None:
    if TOKEN_FILE.exists():
        try:
            return TOKEN_FILE.read_text().strip() or None
        except OSError:
            return None
    return None


def save_token(token: str) -> None:
    SCALEMAKER_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token.strip() + "\n")
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass


def mcp_url() -> str:
    cfg = load_config()
    return cfg.get("mcp_url") or os.environ.get("SCALEMAKER_MCP_URL") or DEFAULT_MCP_URL


def _http_get(url: str, token: str, *, timeout: float = SYNC_TIMEOUT) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read()


def _http_json(url: str, token: str, *, timeout: float = SYNC_TIMEOUT) -> Any:
    raw = _http_get(url, token, timeout=timeout)
    return json.loads(raw.decode("utf-8"))


def load_lockfile() -> dict[str, Any]:
    if LOCKFILE.exists():
        try:
            return json.loads(LOCKFILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {"packages": {}}
    return {"packages": {}}


def save_lockfile(data: dict[str, Any]) -> None:
    SCALEMAKER_DIR.mkdir(parents=True, exist_ok=True)
    LOCKFILE.write_text(json.dumps(data, indent=2))


def load_registry() -> dict:
    if WORKFLOW_REGISTRY.exists():
        try:
            return json.loads(WORKFLOW_REGISTRY.read_text())
        except (json.JSONDecodeError, OSError):
            return {"workflows": {}}
    return {"workflows": {}}


def save_registry(registry: dict) -> None:
    SCALEMAKER_DIR.mkdir(parents=True, exist_ok=True)
    WORKFLOW_REGISTRY.write_text(json.dumps(registry, indent=2))


def match_workflow(registry: dict, query: str) -> tuple[str | None, float]:
    query_lower = query.lower()
    best_slug, best_score = None, 0.0
    for slug, meta in registry.get("workflows", {}).items():
        score = 0.0
        if slug in query_lower:
            score += 10.0
        for phrase in meta.get("intent_phrases", []):
            if phrase.lower() in query_lower:
                score += 5.0
        for kw in meta.get("include_keywords", []):
            if kw.lower() in query_lower:
                score += 1.0
        for kw in meta.get("exclude_keywords", []):
            if kw.lower() in query_lower:
                score -= 2.0
        if score > best_score:
            best_score = score
            best_slug = slug
    return best_slug, best_score


def sync_skills(*, silent: bool = False, timeout: float = SYNC_TIMEOUT) -> int:
    token = load_token()
    if not token:
        if not silent:
            print("ERROR: No Scalemaker Platform token found. Re-install the plugin or run with --post-install.",
                  file=sys.stderr)
        return 1

    base = mcp_url().rstrip("/").replace("/mcp", "")
    lock = load_lockfile()
    registry = load_registry()

    try:
        resp = _http_json(f"{base}/v1/skills/", token, timeout=timeout)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError) as exc:
        if not silent:
            print(f"WARN: Could not reach Scalemaker Platform ({exc}). Using cached skills.", file=sys.stderr)
        return 0

    packages = resp.get("skills", []) if isinstance(resp, dict) else resp
    if not isinstance(packages, list):
        return 0

    installed = 0
    for pkg in packages:
        slug = pkg.get("slug")
        version = pkg.get("version")
        if not slug or not version:
            continue
        current = lock.get("packages", {}).get(slug, {})
        if current.get("version") == version and current.get("checksum") == pkg.get("checksum_sha256"):
            continue
        if _install_package(base, token, pkg, timeout=timeout, silent=silent):
            installed += 1
            lock.setdefault("packages", {})[slug] = {
                "version": version,
                "checksum": pkg.get("checksum_sha256"),
                "installed_at": int(time.time()),
            }
            manifest = pkg.get("manifest_json") or {}
            routing = manifest.get("routing") or {}
            if routing:
                registry.setdefault("workflows", {})[slug] = {
                    "description": routing.get("description", ""),
                    "intent_phrases": routing.get("intent_phrases", []),
                    "include_keywords": routing.get("include_keywords", []),
                    "exclude_keywords": routing.get("exclude_keywords", []),
                }

    save_lockfile(lock)
    save_registry(registry)

    if installed and not silent:
        print(f"Scalemaker Platform: synced {installed} skill(s).")
    return 0


def _install_package(base: str, token: str, pkg: dict, *, timeout: float, silent: bool) -> bool:
    slug = pkg["slug"]
    version = pkg["version"]
    manifest = pkg.get("manifest_json") or {}
    files = manifest.get("files") or []
    if not files:
        return False
    target = SKILLS_DIR / slug
    target.mkdir(parents=True, exist_ok=True)
    for f in files:
        relpath = f.get("path")
        if not relpath:
            continue
        try:
            url = f"{base}/v1/skills/{slug}/{version}/download?path={relpath}"
            data = _http_get(url, token, timeout=timeout)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            if not silent:
                print(f"WARN: Failed to download {slug}/{relpath}: {exc}", file=sys.stderr)
            return False
        dest = target / relpath
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return True


def register_mcp_server(token: str, url: str) -> None:
    """Add or update the `scalemaker` MCP server entry in ~/.claude/settings.json.

    Preserves all other existing settings (hooks, env vars, permissions, etc.).
    Creates the file if it does not exist.
    """
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    settings: dict[str, Any] = {}
    if SETTINGS_FILE.exists():
        try:
            raw = SETTINGS_FILE.read_text().strip()
            if raw:
                settings = json.loads(raw)
                if not isinstance(settings, dict):
                    print(f"WARN: {SETTINGS_FILE} is not a JSON object; overwriting with fresh settings.",
                          file=sys.stderr)
                    settings = {}
        except (json.JSONDecodeError, OSError) as exc:
            print(f"WARN: Could not parse existing {SETTINGS_FILE}: {exc}. Overwriting.",
                  file=sys.stderr)
            settings = {}

    mcp_servers = settings.get("mcpServers")
    if not isinstance(mcp_servers, dict):
        mcp_servers = {}
    mcp_servers["scalemaker"] = {
        "type": "http",
        "url": url,
        "headers": {
            "Authorization": f"Bearer {token}",
        },
    }
    settings["mcpServers"] = mcp_servers
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2) + "\n")


def setup(token: str) -> int:
    token = (token or "").strip()
    if not token:
        print("ERROR: No token provided. Usage: --setup <smp_token>", file=sys.stderr)
        return 1
    if not token.startswith("smp_"):
        print("ERROR: Invalid token format. Scalemaker Platform tokens start with 'smp_'.",
              file=sys.stderr)
        return 1
    url = os.environ.get("SCALEMAKER_MCP_URL", DEFAULT_MCP_URL).strip() or DEFAULT_MCP_URL
    SCALEMAKER_DIR.mkdir(parents=True, exist_ok=True)
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    save_token(token)
    save_config({"mcp_url": url, "installed_at": int(time.time())})
    print("Scalemaker Platform: token stored.")
    print(f"Scalemaker Platform: MCP URL set to {url}")
    try:
        register_mcp_server(token, url)
        print(f"Scalemaker Platform: MCP server registered in {SETTINGS_FILE}")
    except OSError as exc:
        print(f"ERROR: Could not write {SETTINGS_FILE}: {exc}", file=sys.stderr)
        return 1
    print("Scalemaker Platform: performing initial skill sync...")
    sync_skills(silent=False, timeout=SYNC_TIMEOUT)
    print("Scalemaker Platform configured. Restart Claude Code to activate.")
    return 0


def sync_on_start() -> int:
    if not TOKEN_FILE.exists():
        return 0
    try:
        return sync_skills(silent=True, timeout=SESSION_START_TIMEOUT)
    except Exception:
        return 0


def route_query(query: str) -> int:
    registry = load_registry()
    slug, confidence = match_workflow(registry, query)
    if slug and confidence >= 3.0:
        sync_skills(silent=True, timeout=SESSION_START_TIMEOUT)
        skill_md = SKILLS_DIR / slug / "SKILL.md"
        if skill_md.exists():
            print(f"Routing to skill: {slug} (confidence {confidence:.1f})")
            print(f"SKILL.md: {skill_md}")
            return 0
        print(f"Skill '{slug}' matched but not installed; forcing sync...")
        sync_skills(silent=False, timeout=SYNC_TIMEOUT)
        return 0 if skill_md.exists() else 1
    return 0


def list_skills() -> int:
    registry = load_registry()
    workflows = registry.get("workflows", {})
    if not workflows:
        print("No Scalemaker Platform skills installed yet. Run with --force-sync or restart Claude Code.")
        return 0
    print(f"Available Scalemaker Platform skills ({len(workflows)}):")
    for slug in sorted(workflows):
        print(f"  {slug}: {workflows[slug].get('description', '')}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Scalemaker Platform Bootstrap")
    p.add_argument("--setup", metavar="TOKEN",
                   help="Store the given smp_* token, register the MCP server in ~/.claude/settings.json, and perform the initial skill sync.")
    p.add_argument("--sync-on-start", action="store_true")
    p.add_argument("--force-sync", action="store_true")
    p.add_argument("--list-skills", action="store_true")
    p.add_argument("--query", default="")
    args = p.parse_args()

    if args.setup is not None:
        return setup(args.setup)
    if args.sync_on_start:
        return sync_on_start()
    if args.force_sync:
        return sync_skills(silent=False)
    if args.list_skills:
        return list_skills()
    if args.query:
        return route_query(args.query)
    return 0


if __name__ == "__main__":
    sys.exit(main())

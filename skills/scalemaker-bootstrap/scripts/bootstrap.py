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
import uuid
from pathlib import Path
from typing import Any

HOME = Path.home()
SCALEMAKER_DIR = HOME / ".claude" / "scalemaker"
TOKEN_FILE = SCALEMAKER_DIR / "token"
CONFIG_FILE = SCALEMAKER_DIR / "config.json"
LOCKFILE = SCALEMAKER_DIR / "sync.lock"
BRAND_DIR = SCALEMAKER_DIR / "brand"
BRAND_LOCKFILE = BRAND_DIR / ".lock"
CLIENT_ID_FILE = SCALEMAKER_DIR / "client.id"
WORKFLOW_REGISTRY = SCALEMAKER_DIR / "workflow-registry.json"
SKILLS_DIR = HOME / ".claude" / "skills"
SETTINGS_FILE = HOME / ".claude" / "settings.json"

DEFAULT_MCP_URL = "https://mcp.scalemaker.frondorf.co"
SYNC_TIMEOUT = 15.0
SESSION_START_TIMEOUT = 10.0

CLIENT_VERSION = "0.4.0"


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


def _http_post_json(url: str, token: str, payload: dict[str, Any], *, timeout: float) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        resp.read()


def client_id() -> str:
    """Return the stable install UUID, generating it on first call."""
    if CLIENT_ID_FILE.exists():
        try:
            value = CLIENT_ID_FILE.read_text().strip()
            if value:
                return value
        except OSError:
            pass
    SCALEMAKER_DIR.mkdir(parents=True, exist_ok=True)
    new_id = str(uuid.uuid4())
    try:
        CLIENT_ID_FILE.write_text(new_id + "\n")
        CLIENT_ID_FILE.chmod(0o600)
    except OSError:
        pass
    return new_id


def emit_event(
    token: str,
    base: str,
    event_type: str,
    *,
    event_status: str = "ok",
    payload: dict[str, Any] | None = None,
    error_message: str | None = None,
    silent: bool = True,
) -> None:
    """Fire-and-forget client event. Never raises; failures are swallowed silently."""
    try:
        _http_post_json(
            f"{base}/v1/client/event",
            token,
            {
                "client_id": client_id(),
                "client_version": CLIENT_VERSION,
                "event_type": event_type,
                "event_status": event_status,
                "payload": payload or {},
                "error_message": error_message,
            },
            timeout=SESSION_START_TIMEOUT,
        )
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError) as exc:
        if not silent:
            print(f"WARN: emit_event({event_type}) failed: {exc}", file=sys.stderr)


def load_brand_lock() -> dict[str, Any]:
    if BRAND_LOCKFILE.exists():
        try:
            return json.loads(BRAND_LOCKFILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {"entries": {}}
    return {"entries": {}}


def save_brand_lock(data: dict[str, Any]) -> None:
    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    BRAND_LOCKFILE.write_text(json.dumps(data, indent=2))


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

    emit_event(token, base, "sync_start")

    # Fetch brand pack first so requires_brand checks have data.
    brand_keys = sync_brand(token, base, timeout=timeout, silent=silent)

    try:
        resp = _http_json(f"{base}/v1/skills/", token, timeout=timeout)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError) as exc:
        if not silent:
            print(f"WARN: Could not reach Scalemaker Platform ({exc}). Using cached skills.", file=sys.stderr)
        emit_event(
            token, base, "error",
            event_status="error",
            payload={"context": "skill_list_fetch"},
            error_message=str(exc),
        )
        return 0

    if isinstance(resp, dict):
        packages = resp.get("packages") or resp.get("skills") or []
    else:
        packages = resp or []
    if not isinstance(packages, list):
        return 0

    installed = 0
    skipped = 0
    missing_brand_for: list[dict[str, Any]] = []
    for pkg in packages:
        slug = pkg.get("slug")
        version = pkg.get("version")
        if not slug or not version:
            continue

        manifest = pkg.get("manifest_json") or {}
        required = manifest.get("requires_brand") or []
        missing = [k for k in required if k not in brand_keys]
        if missing:
            missing_brand_for.append({"slug": slug, "missing": missing})
            emit_event(
                token, base, "error",
                event_status="error",
                payload={"context": "requires_brand", "slug": slug, "missing": missing},
                error_message=f"Skill {slug} requires brand keys {missing}",
            )
            continue

        current = lock.get("packages", {}).get(slug, {})
        if current.get("version") == version and current.get("checksum") == pkg.get("checksum_sha256"):
            emit_event(
                token, base, "skill_update_skipped",
                payload={"slug": slug, "version": version},
            )
            skipped += 1
            continue

        if _install_package(base, token, pkg, timeout=timeout, silent=silent):
            installed += 1
            lock.setdefault("packages", {})[slug] = {
                "version": version,
                "checksum": pkg.get("checksum_sha256"),
                "installed_at": int(time.time()),
            }
            routing = manifest.get("routing") or {}
            if routing:
                registry.setdefault("workflows", {})[slug] = {
                    "description": routing.get("description", ""),
                    "intent_phrases": routing.get("intent_phrases", []),
                    "include_keywords": routing.get("include_keywords", []),
                    "exclude_keywords": routing.get("exclude_keywords", []),
                }
            emit_event(
                token, base, "skill_installed",
                payload={
                    "slug": slug,
                    "from_version": current.get("version"),
                    "to_version": version,
                },
            )

    save_lockfile(lock)
    save_registry(registry)

    emit_event(
        token, base, "sync_complete",
        payload={
            "installed": installed,
            "skipped": skipped,
            "brand_keys": sorted(brand_keys),
            "missing_brand_for": missing_brand_for,
        },
    )

    if installed and not silent:
        print(f"Scalemaker Platform: synced {installed} skill(s).")
    return 0


def sync_brand(token: str, base: str, *, timeout: float, silent: bool) -> set[str]:
    """Sync tenant brand pack to ~/.claude/scalemaker/brand/. Returns the set of known keys."""
    try:
        resp = _http_json(f"{base}/v1/brand/", token, timeout=timeout)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError) as exc:
        if not silent:
            print(f"WARN: Could not reach brand API ({exc}). Using cached brand pack.", file=sys.stderr)
        emit_event(
            token, base, "error",
            event_status="error",
            payload={"context": "brand_list_fetch"},
            error_message=str(exc),
        )
        local_lock = load_brand_lock()
        return set(local_lock.get("entries", {}).keys())

    entries = resp.get("entries", []) if isinstance(resp, dict) else []
    if not isinstance(entries, list):
        return set()

    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    lock = load_brand_lock()
    known: set[str] = set()
    for entry in entries:
        key = entry.get("key")
        checksum = entry.get("checksum_sha256") or entry.get("checksum")
        if not key or not checksum:
            continue
        known.add(key)
        current = lock.get("entries", {}).get(key, {})
        if current.get("checksum") == checksum:
            continue
        try:
            doc = _http_json(f"{base}/v1/brand/{key}", token, timeout=timeout)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError) as exc:
            if not silent:
                print(f"WARN: Failed to fetch brand/{key}: {exc}", file=sys.stderr)
            emit_event(
                token, base, "error",
                event_status="error",
                payload={"context": "brand_fetch", "key": key},
                error_message=str(exc),
            )
            continue

        value = doc.get("value") if isinstance(doc, dict) else None
        if value is None:
            continue
        target = BRAND_DIR / f"{key}.json"
        body = json.dumps(value, indent=2, ensure_ascii=False)
        target.write_text(body)
        lock.setdefault("entries", {})[key] = {
            "checksum": checksum,
            "fetched_at": int(time.time()),
        }
        emit_event(
            token, base, "brand_fetched",
            payload={"key": key, "size_bytes": len(body.encode("utf-8"))},
        )

    save_brand_lock(lock)
    return known


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
    # Ensure client.id exists so subsequent events are correlated.
    client_id()

    base = url.rstrip("/").replace("/mcp", "")
    emit_event(
        token, base, "setup",
        payload={"os": sys.platform, "python": sys.version.split()[0]},
        silent=False,
    )

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
    token = load_token()
    base = mcp_url().rstrip("/").replace("/mcp", "")
    if slug and confidence >= 3.0:
        if token:
            emit_event(
                token, base, "query_match",
                payload={"matched_slug": slug, "score": confidence},
            )
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

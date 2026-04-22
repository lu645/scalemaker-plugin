---
name: scalemaker-bootstrap
version: "1.0.0"
description: Scalemaker Platform skill router, syncer, and updater for Claude Code
visibility: public
wrapper_declarations: []
---

# Scalemaker Platform Bootstrap

This skill is the local router and updater for all Scalemaker Platform skills. It is installed once
by the Scalemaker Platform installer and runs automatically before any Scalemaker Platform workflow.

## What this skill does

1. **Workflow detection**: identifies when the user's request matches a Scalemaker Platform workflow using the local workflow registry
2. **Just-in-time sync**: checks for skill updates before executing a matched workflow
3. **Explicit sync**: updates all entitled skill packages when the user asks to update or sync Scalemaker Platform skills
4. **Bootstrap self-update**: safely replaces itself when a new version is available (two-phase replacement)

## Workflow detection

The bootstrap skill reads `~/.claude/scalemaker/workflow-registry.json` to match user intent:
- Exact slug/name match wins
- Keyword/intent scoring match second
- Ambiguous matches ask the user to choose
- Unknown requests are passed through to Claude normally

## Usage

Claude Code calls this skill automatically before Scalemaker Platform workflows. Users can also:

- **Sync/update skills**: "update my Scalemaker Platform skills" or "sync Scalemaker Platform"
- **Check available skills**: "what Scalemaker Platform skills do I have?"
- **Run a specific skill**: just describe what you want — the bootstrap will route it

## Sync behavior

The bootstrap script (`bootstrap.py`, stdlib-only) communicates directly with the
Scalemaker Platform MCP server using the stored `smp_*` bearer token.

Token location: `~/.claude/scalemaker/token`
Lockfile: `~/.claude/scalemaker/sync.lock`
Workflow registry: `~/.claude/scalemaker/workflow-registry.json`
Skills directory: `~/.claude/skills/`

### Session-start auto-sync (optional)

Claude Code does not honor a `hooks:` block in SKILL.md frontmatter — hooks
must be wired at the CC-settings level. To get an automatic check+update on
every new CC session, add the following to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "command": "python ~/.claude/skills/scalemaker-bootstrap/scripts/bootstrap.py --sync-on-start"
      }
    ]
  }
}
```

The `--sync-on-start` flag runs a short (10s timeout) check against the MCP
server and downloads any updated skill packages. It fails silently on network
errors so a slow or unreachable API never blocks CC startup.

In-skill fallback: on any skill invocation, the router already calls
`run_sync()` before dispatching to a matched workflow, so updates are always
pulled just-in-time even without the SessionStart hook. The hook is a
latency optimization, not a correctness requirement.

## Self-update

The bootstrap skill itself may receive updates. Updates are handled conservatively:
1. New version is downloaded to `~/.claude/scalemaker/bootstrap-next/`
2. User is notified that a bootstrap update is available
3. On next explicit sync command, the update is applied and the session restarted

Do not apply bootstrap updates silently during an active workflow.

## Error handling

- **Token expired/revoked**: "Your Scalemaker Platform token has expired. Please re-run the installer or contact your administrator."
- **No network**: "Could not reach Scalemaker Platform servers. Proceeding with cached skills."
- **Skill checksum mismatch**: "Skill download verification failed. Retrying..." (max 3 retries, then error)
- **No matching workflow**: Pass through to Claude normally — do not error.

## Routing metadata

```json
{
  "title": "Scalemaker Platform Bootstrap",
  "description": "Routes and updates Scalemaker Platform skills for Claude Code",
  "intent_phrases": [
    "update scalemaker skills",
    "sync scalemaker",
    "what scalemaker skills do i have",
    "check for skill updates"
  ],
  "include_keywords": ["scalemaker", "skills", "sync", "update"],
  "exclude_keywords": [],
  "examples": [
    "Update my Scalemaker Platform skills",
    "Sync Scalemaker Platform skills",
    "What skills do I have available?",
    "Check for Scalemaker Platform updates"
  ]
}
```

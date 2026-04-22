---
name: scalemaker-setup
description: Complete Scalemaker Platform plugin setup — store your MCP bearer token and sync skills.
argument-hint: "<smp_token>"
---

# Scalemaker Platform Setup

The user has provided their Scalemaker Platform MCP bearer token via the argument `$ARGUMENTS`. Run the setup script to:

1. Store the token at `~/.claude/scalemaker/token`
2. Register the Scalemaker Platform MCP server in Claude Code settings
3. Perform the initial skill sync
4. Tell the user to restart Claude Code

Execute this command (replace `$ARGUMENTS` with the literal token the user provided):

```bash
python "${CLAUDE_PLUGIN_ROOT}/skills/scalemaker-bootstrap/scripts/bootstrap.py" --setup "$ARGUMENTS"
```

After the script runs successfully, tell the user:

> ✅ Scalemaker Platform is configured. Please restart Claude Code for the MCP server to activate. After restart, just describe what you want and the appropriate skill will run.

If the script fails with a token error, ask the user to verify their token format (should start with `smp_`).

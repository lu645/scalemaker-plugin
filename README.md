# Scalemaker Platform — Claude Code Plugin

AI-powered marketing creative workflows for Claude Code. Generate product-aware ad images, copy, and campaign assets using the Scalemaker Platform Skill CDN.

## What you get

- **Skill CDN** — auto-sync of marketing skills entitled to your tenant (angle generator, creative producer, competitor cloner, etc.)
- **Wrapper APIs** — provider-isolated REST endpoints for Nano Banana (images), Gemini (text), Meta Ads (performance)
- **Product boost** — your Shopify / Meta products enrich every prompt with brand context
- **Asset gallery** — every generated creative is stored, tagged, and publishable to Meta Ads Manager

## Install

In Claude Code, run two commands:

```
/plugin install scalemaker/scalemaker-plugin
/scalemaker-setup smp_abc123...
```

Replace `smp_abc123...` with the bearer token your Scalemaker Platform admin issued to you.

Then **restart Claude Code**. The setup command registers the Scalemaker Platform MCP server in `~/.claude/settings.json`, and Claude Code only loads new MCP servers on startup.

After restart, skills auto-sync on every session start.

## Getting a token

Ask your Scalemaker Platform admin. They create it via:

```
POST /v1/admin/mcp-tokens
{ "display_name": "YourName", "scope": "public", "tenant_id": <your_tenant> }
```

You will receive a `smp_...` string. Pass it to `/scalemaker-setup`.

## Usage

Once installed and restarted, just describe what you want:

```
Generate a hero image for my Woodstick product in a minimalist style
```

Claude routes to the matching Scalemaker Platform skill, checks if your stored product data applies, offers to enrich the prompt, and delivers the asset.

Or invoke skills directly via slash commands:

```
/angle-generator
/creative-producer
/competitor-cloner
```

(Available slash commands depend on which skills are entitled to your tenant.)

## Manually sync skills

```
/scalemaker-bootstrap sync
```

Or:

```
Update my Scalemaker Platform skills
```

## Re-running setup / rotating the token

Just run `/scalemaker-setup <new_token>` again. The setup command is idempotent:
it overwrites the token file and the `mcpServers.scalemaker` entry in
`~/.claude/settings.json` while preserving all your other settings. Restart
Claude Code after running it again.

## Privacy & data

- All prompts, generated assets, and product context stay in the Scalemaker Platform tenant-scoped DB
- Asset bytes are stored in two Supabase buckets: `public-assets` (24h public URL) + `internal-assets` (private permanent)
- API keys for LLM providers are held server-side — never on your machine
- Your MCP token is stored at `~/.claude/scalemaker/token` (chmod 0600)
- The token is also written into `~/.claude/settings.json` under `mcpServers.scalemaker.headers.Authorization` so Claude Code can authenticate with the Scalemaker Platform MCP server

## Troubleshooting

**"Your Scalemaker Platform token has expired"**
Ask your admin to issue a new token, then run `/scalemaker-setup <new_token>` and restart Claude Code.

**"Could not reach Scalemaker Platform servers"**
Skills still work with your last synced cache. Check https://status.scalemaker.com.

**Skills out of date**
Run `sync Scalemaker Platform skills` in Claude Code, or restart your session.

**MCP server not available after install**
Make sure you ran `/scalemaker-setup <token>` and then fully restarted Claude Code. New MCP servers are only loaded on startup.

## License

Proprietary. See LICENSE.

## Support

- Docs: https://docs.scalemaker.com
- Issues: https://github.com/scalemaker/scalemaker-plugin/issues
- Email: support@scalemaker.com

# Release Notes

## v0.1.0-dev — Sandbox Build (2026-04-18)

> **Status**: Development / Sandbox testing  
> **Branch**: `dev`  
> **Environment**: Sandbox (`apisb.etrade.com`)

---

### What's Working

- **Claude.ai** — connects via `/sse`, full OAuth 2.1 flow (Authorization Code + PKCE), PIN or Client ID + Secret approval, refresh tokens ✅
- **ChatGPT / OpenAI** — connects via `/mcp` (Streamable-HTTP), OAuth 2.1 client credentials grant ✅
- **E\*TRADE Sandbox** — account listing, balances, portfolio, quotes, option chains, order preview + placement, transactions, alerts ✅
- **OpenBao** — all credentials (API keys, access tokens, JWT secret, OAuth clients) stored and retrieved correctly ✅
- **Helm** — deployed to K3s cluster, `etrade` namespace, LoadBalancer at `10.50.0.49:8767` ✅
- **Cloudflare** — external access via `https://mcp.heimdallai.co` ✅

---

### Known Limitations

- **Live trading not yet tested** — sandbox only; switch with `etrade_set_sandbox(False)` and re-authorize
- **Gemini** — not yet tested
- **Token auto-refresh** — clients must handle refresh token rotation; no server-side proactive renewal
- **E\*TRADE OAuth 1.0a tokens** expire after 2 hours of inactivity — must call `etrade_renew_access_token()` or re-authorize

---

### Pre-Release Checklist

- [ ] Test live E\*TRADE API (`etrade_set_sandbox(False)`)
- [ ] Test Gemini MCP connection
- [ ] Test order placement end-to-end in live mode
- [ ] Confirm refresh token rotation works across sessions
- [ ] Add rate limiting / request throttling
- [ ] Add structured logging / observability
- [ ] Security review of OAuth 2.1 implementation
- [ ] Helm chart versioning (bump `appVersion` when promoting to `main`)

---

### Deployment

| Component | Value |
|---|---|
| Image | `localhost/etrade-oauth-mcp:latest` |
| Namespace | `etrade` |
| Internal IP | `10.50.0.49:8767` |
| External URL | `https://mcp.heimdallai.co` |
| SSE endpoint | `https://mcp.heimdallai.co/sse` |
| MCP endpoint | `https://mcp.heimdallai.co/mcp` |
| Auth metadata | `https://mcp.heimdallai.co/.well-known/oauth-authorization-server` |

---

### Registered OAuth Clients

| Client ID | Name | Grant Types |
|---|---|---|
| `mcp-heimdall` | HeimdallAI | `authorization_code`, `client_credentials` |
| Dynamic (Claude.ai) | Auto-registered | `authorization_code` |
| Dynamic (ChatGPT) | Auto-registered | `authorization_code` |

---

### Promoting to Production (`main`)

1. Complete pre-release checklist above
2. Bump `version` and `appVersion` in `helm/etrade-oauth-mcp/Chart.yaml`
3. Tag the commit: `git tag v1.0.0`
4. Merge `dev` → `main`
5. Rebuild image with versioned tag
6. Helm upgrade with `--set image.tag=1.0.0`

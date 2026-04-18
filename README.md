# etrade-oauth-mcp

An MCP (Model Context Protocol) server for the official E\*TRADE API, built with FastMCP and rauth (OAuth 1.0a). Exposes all major E\*TRADE endpoints as MCP tools, secured behind an OAuth 2.1 authorization server.

---

## Architecture

```
AI Client (Claude / ChatGPT / Gemini)
        │
        │  Bearer JWT
        ▼
┌─────────────────────────────┐
│   OAuth 2.1 Auth Server     │  /.well-known/oauth-authorization-server
│   (auth.py)                 │  /oauth/register  /oauth/authorize  /oauth/token
└────────────┬────────────────┘
             │
      ┌──────┴───────┐
      │              │
   /sse           /mcp
  (Claude)     (ChatGPT)
  SSE transp.  Streamable-HTTP
      │              │
      └──────┬───────┘
             │
   ┌─────────▼─────────┐
   │   FastMCP Server   │  mcp_server.py
   │   (mcp_server.py)  │
   └─────────┬──────────┘
             │  OAuth 1.0a (rauth)
             ▼
   ┌─────────────────────┐
   │  E*TRADE API        │  api.etrade.com / apisb.etrade.com
   │  (etrade_client.py) │
   └─────────────────────┘
             │
   ┌─────────▼─────────┐
   │  OpenBao           │  Credentials, tokens, OAuth clients
   │  (bao.py)          │  secret/data/etrade/{live,sandbox,mcp_auth,mcp_clients,...}
   └────────────────────┘
```

### Deployment

- **Runtime**: Python 3.12, FastMCP 1.27.0, uvicorn
- **Container**: Podman → imported into K3s containerd
- **Orchestration**: Kubernetes (K3s) via Helm chart (`helm/etrade-oauth-mcp/`)
- **Namespace**: `etrade`
- **External access**: Cloudflare → `https://mcp.heimdallai.co`
- **Secrets**: OpenBao (Vault-compatible) — AppRole or static token auth

---

## MCP Endpoints

| Transport | URL | Client |
|---|---|---|
| SSE | `https://mcp.heimdallai.co/sse` | Claude.ai |
| Streamable-HTTP | `https://mcp.heimdallai.co/mcp` | ChatGPT / OpenAI |

---

## OAuth 2.1 Authentication

All MCP endpoints require a Bearer JWT. Clients obtain tokens via the OAuth 2.1 Authorization Server embedded in the MCP server.

### Discovery

```
GET /.well-known/oauth-authorization-server
GET /.well-known/openid-configuration
GET /.well-known/oauth-protected-resource
```

### Flows

**Authorization Code + PKCE** (Claude.ai, browser-based clients)

1. Client discovers auth server metadata
2. Client registers via `POST /oauth/register`
3. User is redirected to `GET /oauth/authorize` — browser shows approval page
4. User authenticates with **PIN** or **Client ID + Secret**
5. Server issues authorization code → redirects to client callback
6. Client exchanges code at `POST /oauth/token` (PKCE verified)
7. Client receives `access_token` (1-hour JWT) + `refresh_token` (30-day, rotating)

**Client Credentials** (server-to-server, ChatGPT)

```http
POST /oauth/token
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
&client_id=mcp-heimdall
&client_secret=<secret>
```

### Approval Page

The browser approval page at `/oauth/authorize` supports two authentication methods:

- **PIN tab** — single shared PIN set via `etrade_set_mcp_pin()`
- **Client Credentials tab** — pre-registered `client_id` + `client_secret`

---

## E*TRADE Tools

### Auth & Session
| Tool | Description |
|---|---|
| `etrade_set_sandbox(enabled)` | Switch between sandbox and live mode |
| `etrade_sandbox_status()` | Check current mode |
| `etrade_session_status()` | Check OAuth token status |
| `etrade_authorize_start()` | Step 1: get OAuth 1.0a request token + authorization URL |
| `etrade_authorize_complete(verifier)` | Step 2: exchange verifier for access tokens |
| `etrade_store_api_keys(key, secret)` | Store E\*TRADE consumer key/secret in OpenBao |
| `etrade_renew_access_token()` | Renew OAuth 1.0a access token |
| `etrade_revoke_access_token()` | Revoke access token (logout) |

### MCP Auth Management
| Tool | Description |
|---|---|
| `etrade_set_mcp_pin(pin)` | Set PIN for OAuth approval page |
| `etrade_create_oauth_client(name)` | Create client_id + client_secret for server-to-server auth |

### Accounts
| Tool | Description |
|---|---|
| `etrade_list_accounts()` | List all accounts |
| `etrade_get_balance(account_id_key)` | Get account balance |
| `etrade_get_balances()` | Get all account balances in one call |
| `etrade_get_portfolio(account_id_key)` | Get portfolio positions |
| `etrade_list_transactions(account_id_key)` | List transactions (dates: MMDDYYYY) |
| `etrade_get_transaction(account_id_key, transaction_id)` | Get transaction detail |

### Market Data
| Tool | Description |
|---|---|
| `etrade_get_quotes(symbols)` | Get quotes (comma-separated, up to 25) |
| `etrade_symbol_lookup(search)` | Look up securities by name |
| `etrade_get_option_chains(symbol)` | Get option chains |
| `etrade_get_option_expire_dates(symbol)` | Get option expiration dates |

### Orders
| Tool | Description |
|---|---|
| `etrade_list_orders(account_id_key)` | List orders |
| `etrade_preview_order(...)` | Preview an order (required before placing) |
| `etrade_place_order(...)` | Place a previewed order |
| `etrade_cancel_order(account_id_key, order_id)` | Cancel an open order |
| `etrade_change_order_preview(...)` | Preview a change to an existing order |
| `etrade_change_order_place(...)` | Apply a previewed order change |

### Alerts
| Tool | Description |
|---|---|
| `etrade_list_alerts()` | List alerts |
| `etrade_get_alert(alert_id)` | Get alert detail |
| `etrade_delete_alerts(alert_ids)` | Delete alerts |

---

## Credential Storage (OpenBao)

| Path | Keys |
|---|---|
| `secret/data/etrade/sandbox` | `consumer_key`, `consumer_secret`, `access_token`, `access_token_secret` |
| `secret/data/etrade/live` | `consumer_key`, `consumer_secret`, `access_token`, `access_token_secret` |
| `secret/data/etrade/mcp_auth` | `jwt_secret`, `mcp_pin` |
| `secret/data/etrade/mcp_clients` | `{client_id: client_data}` |
| `secret/data/etrade/mcp_refresh_tokens` | `{token: {client_id, scope, expires}}` |

---

## Helm Deployment

```bash
helm upgrade --install etrade-oauth-mcp ./helm/etrade-oauth-mcp \
  --namespace etrade \
  --set openbao.token=<token> \
  --set mcpIssuer=https://mcp.yourdomain.com
```

### Key Values

| Value | Default | Description |
|---|---|---|
| `image.repository` | `localhost/etrade-oauth-mcp` | Container image |
| `image.tag` | `latest` | Image tag |
| `port` | `8767` | Server port |
| `sandbox` | `true` | Start in sandbox mode |
| `mcpIssuer` | `""` | External URL (required for OAuth) |
| `openbao.addr` | cluster internal | OpenBao address |
| `openbao.token` | `""` | Static token (or use roleId + secretId) |
| `service.type` | `LoadBalancer` | Kubernetes service type |
| `service.loadBalancerIP` | `10.50.0.49` | Static IP |

---

## Environment Variables

| Variable | Description |
|---|---|
| `BAO_ADDR` | OpenBao server address |
| `BAO_TOKEN` | Static OpenBao token |
| `BAO_ROLE_ID` | AppRole role ID (alternative to token) |
| `BAO_SECRET_ID` | AppRole secret ID |
| `MCP_ISSUER` | External base URL (e.g. `https://mcp.heimdallai.co`) |

---

## Development

```bash
# Build
podman build -t localhost/etrade-oauth-mcp:latest .

# Run locally
docker-compose up

# Import to K3s and redeploy
sudo k3s ctr images import /tmp/etrade-oauth-mcp.tar
kubectl rollout restart deployment/etrade-oauth-mcp -n etrade
```

---

## Notes

- Order quantities are always `int` — E\*TRADE does not support fractional shares
- All order placements require a preview step first (`etrade_preview_order`)
- OAuth 1.0a tokens expire after 2 hours of inactivity — use `etrade_renew_access_token()`
- Mobile/internal E\*TRADE endpoints are intentionally excluded (not in official spec)
- Sandbox (`apisb.etrade.com`) is the default mode; switch with `etrade_set_sandbox(False)`

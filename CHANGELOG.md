# Changelog

All notable changes to `etrade-oauth-mcp` are documented here.

---

## [Unreleased] — dev branch

### Summary
Complete rewrite of the E\*TRADE MCP server using the official E\*TRADE Python client (rauth / OAuth 1.0a) and the official E\*TRADE OpenAPI spec. Replaced the old server's mobile/internal API endpoints with the official `/v1/` REST API. Added full OAuth 2.1 authorization server, dual-transport support (SSE + Streamable-HTTP), and OpenBao-backed credential management.

---

### Added

#### Core Server (`mcp_server.py`)
- Built on **FastMCP 1.27.0** with `uvicorn` as the ASGI server
- **Dual transport**: SSE at `/sse` (Claude.ai) and Streamable-HTTP at `/mcp` (ChatGPT/OpenAI) served from the same port via `PathRouter`
- Live mode default (`api.etrade.com`); sandbox URL (`apisb.etrade.com`) commented out in `etrade_client.py`
- All E\*TRADE tools built against the **official OpenAPI spec** — no mobile/internal endpoints
- `asyncio.to_thread()` wrapping for all synchronous rauth calls

#### OAuth 2.1 Authorization Server (`auth.py`)
- **RFC 8414** — `/.well-known/oauth-authorization-server` metadata endpoint
- **RFC 8414** — `/.well-known/openid-configuration` (alias, for ChatGPT compatibility)
- **RFC 9470** — `/.well-known/oauth-protected-resource` (auth server discovery)
- **RFC 7591** — Dynamic Client Registration at `POST /oauth/register`
- **RFC 6749** — Authorization Code flow at `GET/POST /oauth/authorize`
- **RFC 7636** — PKCE (S256 mandatory) at `POST /oauth/token`
- **RFC 6749** — `client_credentials` grant for server-to-server auth
- **RFC 6749** — `refresh_token` grant with rotation (30-day TTL)
- HS256 JWT access tokens (1-hour TTL), signed with secret stored in OpenBao
- Browser approval page with two auth tabs: **PIN** and **Client ID + Secret**
- `BearerAuthMiddleware` protecting `/sse`, `/messages`, and `/mcp`
- PBKDF2-HMAC-SHA256 hashing for stored client secrets

#### E\*TRADE Client (`etrade_client.py`)
- Replaced custom HMAC-SHA1 httpx implementation with **rauth** (`OAuth1Session`)
- All endpoints target official `/v1/` REST API (not mobile/internal APIs)
- `header_auth=True` on all requests per E\*TRADE spec
- Null-filtered params (optional params omitted from query string when `None`)
- XML-based order placement (`PreviewOrderRequest` / `PlaceOrderRequest`)
- `quantity` enforced as `int` throughout — E\*TRADE does not support fractional shares

#### Credential Manager (`bao.py`)
- Separate OpenBao paths for sandbox and live E\*TRADE credentials
- JWT signing secret auto-generated and stored on first use
- MCP PIN storage for OAuth approval page
- OAuth client registry with PBKDF2-hashed client secrets
- Refresh token store with expiry tracking
- AppRole support (`BAO_ROLE_ID` + `BAO_SECRET_ID`) as alternative to static token

#### MCP Tools
- `etrade_set_sandbox` / `etrade_sandbox_status` — environment switching
- `etrade_store_api_keys` — store E\*TRADE consumer key/secret in OpenBao
- `etrade_authorize_start` / `etrade_authorize_complete` — OAuth 1.0a dance
- `etrade_session_status` / `etrade_renew_access_token` / `etrade_revoke_access_token`
- `etrade_list_accounts` / `etrade_get_balance` / `etrade_get_balances` / `etrade_get_portfolio`
- `etrade_list_transactions` / `etrade_get_transaction`
- `etrade_get_quotes` / `etrade_symbol_lookup`
- `etrade_get_option_chains` / `etrade_get_option_expire_dates`
- `etrade_list_orders` / `etrade_preview_order` / `etrade_place_order`
- `etrade_cancel_order` / `etrade_change_order_preview` / `etrade_change_order_place`
- `etrade_list_alerts` / `etrade_get_alert` / `etrade_delete_alerts`
- `etrade_set_mcp_pin` — set PIN for OAuth approval page
- `etrade_create_oauth_client` — generate client_id + client_secret for server-to-server auth

#### Helm Chart (`helm/etrade-oauth-mcp/`)
- Deployment, Service, Secret templates
- `sandbox` value controls `--live` flag (default: live mode)
- `mcpIssuer` value sets `MCP_ISSUER` environment variable for OAuth metadata
- LoadBalancer service with static IP (`10.50.0.49`)
- OpenBao credentials passed via Kubernetes Secret

#### Infrastructure
- `Dockerfile` — Python 3.12-slim, all deps from `requirements.txt`
- `docker-compose.yml` — local dev with env var passthrough
- `.gitignore` — excludes `__pycache__`, `.env`, `*.tar`

---

### Changed vs Old Server (`power-etrade` / `ETRADE Android File/`)

| Area | Old Server | New Server |
|---|---|---|
| API base | `mobiletrade.etrade.com` (internal) | `api.etrade.com` / `apisb.etrade.com` (official) |
| OAuth | Custom HMAC-SHA1 via httpx + device spoofing | rauth `OAuth1Session` per spec |
| Auth | Static bearer token only | Full OAuth 2.1 (PKCE, refresh tokens, client credentials) |
| Transport | SSE only (`/sse`) | SSE + Streamable-HTTP (`/sse` + `/mcp`) |
| Order quantities | `float` | `int` (E\*TRADE does not allow fractional shares) |
| Credentials | Env vars / Kubernetes secret | OpenBao (sandbox + live paths separate) |
| Mobile endpoints | Included (device spoofing) | Excluded (not in official spec) |
| Default mode | Sandbox | Live (`api.etrade.com`) |

---

### Fixed (continued)

- **Streamable-HTTP 424 Failed Dependency** — `PathRouter` now runs both SSE and streamable-http app lifespans concurrently via anyio, so the streamable-http session manager's `TaskGroup` initializes before ChatGPT makes its first `/mcp` request
- **`/.well-known/openid-configuration` 404** — added alias endpoint returning same metadata as `oauth-authorization-server` (required by ChatGPT)
- **`/.well-known/oauth-protected-resource` 404** — added endpoint advertising auth server location (required by some MCP clients)
- **`/see` typo** — Claude.ai was configured with `/see` instead of `/sse`; documented correct endpoint

### Fixed (original list)

- `symbol_lookup` — was using query param `?query=`, now uses path param `/v1/market/lookup/{search}` per spec
- `delete_alerts` — was incorrectly using PUT, now uses DELETE per spec
- `list_transactions` date format — corrected to `MMDDYYYY` per spec
- Order preview/place URLs — removed erroneous `.json` suffix
- `--sandbox` CLI flag causing CrashLoopBackOff — removed; sandbox is now the default, only `--live` flag exists
- SSE transport "Session not found" error — resolved by switching from streamable-http to SSE for Claude.ai
- `/see` typo in Claude.ai MCP URL — documented correct endpoint as `/sse`
- `redirect_uri` validation too strict — authorization page now accepts any redirect_uri from registered clients (PIN/secret is the security gate)

---

### Security

- Client secrets stored as PBKDF2-HMAC-SHA256 (200k iterations) — never stored in plaintext
- JWT signing secret auto-generated with `secrets.token_hex(32)`, stored in OpenBao
- PKCE S256 mandatory for all Authorization Code flows
- Refresh tokens rotated on every use
- `secrets.compare_digest` used for all credential comparisons (timing-safe)
- All MCP transport endpoints require valid Bearer JWT
- OAuth flow endpoints and `/.well-known/*` are public (required for client discovery)

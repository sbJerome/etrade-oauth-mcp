"""
OAuth 2.1 Authorization Server for MCP.

Implements:
  - RFC 8414  /.well-known/oauth-authorization-server
  - RFC 7591  Dynamic Client Registration  POST /oauth/register
  - RFC 6749  Authorization Code           GET/POST /oauth/authorize
  - RFC 6749  Token Endpoint               POST /oauth/token
  - RFC 6749  client_credentials grant     (pre-registered clients, no browser needed)
  - RFC 7636  PKCE (S256, mandatory for authorization_code)
  - RFC 6750  Bearer Token (middleware)

Access tokens: HS256 JWT, 1-hour TTL.
Refresh tokens: opaque, stored in OpenBao, rotated on each use.
Security gate: PIN (authorization_code) or client_secret (client_credentials).
"""

import hashlib
import base64
import hmac
import logging
import os
import secrets
import time
from typing import Optional

import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

logger = logging.getLogger(__name__)

MCP_ISSUER = os.environ.get("MCP_ISSUER", "http://localhost:8767")
JWT_ALG = "HS256"
ACCESS_TOKEN_TTL  = 3600        # 1 hour
REFRESH_TOKEN_TTL = 30 * 86400  # 30 days
CODE_TTL          = 300         # 5 minutes

# Short-lived in-memory code store — losing these on restart is fine
_codes: dict[str, dict] = {}


# ── Bearer middleware ─────────────────────────────────────────────────────────

class BearerAuthMiddleware(BaseHTTPMiddleware):
    _PUBLIC = ("/.well-known", "/oauth", "/health")
    _PROTECTED = ("/sse", "/messages", "/mcp")

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        for pub in self._PUBLIC:
            if path.startswith(pub):
                return await call_next(request)
        for prot in self._PROTECTED:
            if path.startswith(prot):
                auth = request.headers.get("Authorization", "")
                if not auth.startswith("Bearer "):
                    return _bearer_error(401, "unauthorized",
                                         "Bearer token required",
                                         realm=MCP_ISSUER)
                if not await _validate_token(auth[7:]):
                    return _bearer_error(401, "invalid_token",
                                         "Token is invalid or expired")
        return await call_next(request)


def _bearer_error(status: int, error: str, desc: str,
                  realm: Optional[str] = None) -> JSONResponse:
    www = f'Bearer error="{error}", error_description="{desc}"'
    if realm:
        www = f'Bearer realm="{realm}", ' + www[7:]
    return JSONResponse({"error": error, "error_description": desc},
                        status_code=status,
                        headers={"WWW-Authenticate": www})


# ── Token helpers ─────────────────────────────────────────────────────────────

async def _jwt_secret() -> str:
    from bao import get_jwt_secret
    return await get_jwt_secret()


async def _issue_access_token(client_id: str, scope: str) -> str:
    secret = await _jwt_secret()
    now = int(time.time())
    return jwt.encode(
        {"iss": MCP_ISSUER, "sub": "owner", "aud": client_id,
         "iat": now, "exp": now + ACCESS_TOKEN_TTL, "scope": scope},
        secret, algorithm=JWT_ALG,
    )


async def _validate_token(token: str) -> bool:
    try:
        secret = await _jwt_secret()
        jwt.decode(token, secret, algorithms=[JWT_ALG],
                   options={"require": ["exp", "iat", "sub"]},
                   audience=jwt.decode(token, options={"verify_signature": False})["aud"])
        return True
    except Exception:
        return False


async def _issue_refresh_token(client_id: str, scope: str) -> str:
    from bao import store_refresh_token
    token = secrets.token_urlsafe(48)
    await store_refresh_token(token, {
        "client_id": client_id,
        "scope": scope,
        "expires": int(time.time()) + REFRESH_TOKEN_TTL,
    })
    return token


# ── OAuth 2.1 endpoint handlers ───────────────────────────────────────────────

async def handle_metadata(request: Request) -> Response:
    """RFC 8414 — Authorization Server Metadata."""
    return JSONResponse({
        "issuer": MCP_ISSUER,
        "authorization_endpoint": f"{MCP_ISSUER}/oauth/authorize",
        "token_endpoint": f"{MCP_ISSUER}/oauth/token",
        "registration_endpoint": f"{MCP_ISSUER}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token", "client_credentials"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["mcp"],
    })


async def handle_register(request: Request) -> Response:
    """RFC 7591 — Dynamic Client Registration."""
    try:
        body = await request.json()
    except Exception:
        return _oauth_error(400, "invalid_request", "Body must be JSON")

    redirect_uris = body.get("redirect_uris", [])
    if not redirect_uris:
        return _oauth_error(400, "invalid_request", "redirect_uris required")

    client_id = "mcp-" + secrets.token_urlsafe(12)
    client = {
        "client_id": client_id,
        "client_name": body.get("client_name", ""),
        "redirect_uris": redirect_uris,
        "created_at": int(time.time()),
    }
    from bao import register_oauth_client
    await register_oauth_client(client_id, client)
    logger.info("Client registered: %s  redirect_uris=%s", client_id, redirect_uris)
    return JSONResponse({
        "client_id": client_id,
        "client_name": client["client_name"],
        "redirect_uris": redirect_uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }, status_code=201)


async def handle_authorize(request: Request) -> Response:
    """Authorization endpoint — shows PIN approval page (GET) or processes it (POST)."""
    from bao import get_oauth_client, get_mcp_pin

    if request.method == "GET":
        p = request.query_params
        client_id            = p.get("client_id", "")
        redirect_uri         = p.get("redirect_uri", "")
        state                = p.get("state", "")
        code_challenge       = p.get("code_challenge", "")
        code_challenge_method = p.get("code_challenge_method", "S256")
        scope                = p.get("scope", "mcp")
        response_type        = p.get("response_type", "")

        if response_type != "code":
            return _oauth_error(400, "unsupported_response_type",
                                "Only response_type=code is supported")
        if not code_challenge:
            return _oauth_error(400, "invalid_request", "PKCE code_challenge required")
        if code_challenge_method != "S256":
            return _oauth_error(400, "invalid_request", "Only S256 is supported")

        client = await get_oauth_client(client_id)
        client_name = client.get("client_name") or client_id if client else client_id

        return HTMLResponse(_approval_html(
            client_name=client_name,
            client_id=client_id, redirect_uri=redirect_uri, state=state,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method, scope=scope,
        ))

    # POST — validate via PIN or client_id + client_secret
    form = await request.form()
    client_id            = form.get("client_id", "")
    redirect_uri         = form.get("redirect_uri", "")
    state                = form.get("state", "")
    code_challenge       = form.get("code_challenge", "")
    code_challenge_method = form.get("code_challenge_method", "S256")
    scope                = form.get("scope", "mcp")
    auth_method          = form.get("auth_method", "pin")
    pin                  = form.get("pin", "")
    auth_client_id       = form.get("auth_client_id", "")
    auth_client_secret   = form.get("auth_client_secret", "")

    def _page(error: str = ""):
        return HTMLResponse(
            _approval_html(client_name=client_id, client_id=client_id,
                           redirect_uri=redirect_uri, state=state,
                           code_challenge=code_challenge,
                           code_challenge_method=code_challenge_method,
                           scope=scope, error=error),
            status_code=401,
        )

    if auth_method == "pin":
        from bao import get_mcp_pin
        stored_pin = await get_mcp_pin()
        logger.info("PIN auth: received_len=%d stored_len=%d", len(pin), len(stored_pin))
        if not stored_pin:
            return _page("PIN not configured — call etrade_set_mcp_pin() first")
        if not secrets.compare_digest(pin.encode(), stored_pin.encode()):
            return _page("Wrong PIN")
    else:
        from bao import get_oauth_client as _get_client
        auth_client = await _get_client(auth_client_id) if auth_client_id else {}
        stored_hash = auth_client.get("client_secret_hash", "")
        logger.info("Client auth: id=%s has_secret=%s", auth_client_id, bool(stored_hash))
        if not auth_client or not stored_hash:
            return _page("Unknown Client ID")
        if not _verify_secret(auth_client_secret, stored_hash):
            return _page("Wrong Client Secret")

    code = secrets.token_urlsafe(32)
    _codes[code] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "expires": time.time() + CODE_TTL,
    }
    logger.info("Authorization code issued for client %s", client_id)
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}code={code}&state={state}",
                            status_code=302)


async def handle_token(request: Request) -> Response:
    """Token endpoint — authorization_code, refresh_token, client_credentials grants."""
    form = await request.form()
    grant_type = form.get("grant_type", "")

    if grant_type == "authorization_code":
        return await _grant_authorization_code(form)
    if grant_type == "refresh_token":
        return await _grant_refresh_token(form)
    if grant_type == "client_credentials":
        return await _grant_client_credentials(form)
    return _oauth_error(400, "unsupported_grant_type",
                        f"grant_type '{grant_type}' not supported")


async def _grant_authorization_code(form) -> Response:
    code         = form.get("code", "")
    code_verifier = form.get("code_verifier", "")
    redirect_uri = form.get("redirect_uri", "")
    client_id    = form.get("client_id", "")

    entry = _codes.pop(code, None)
    if not entry:
        return _oauth_error(400, "invalid_grant", "Code not found or already used")
    if time.time() > entry["expires"]:
        return _oauth_error(400, "invalid_grant", "Code expired")
    if entry["client_id"] != client_id:
        return _oauth_error(400, "invalid_client", "client_id mismatch")
    if entry["redirect_uri"] != redirect_uri:
        return _oauth_error(400, "invalid_grant", "redirect_uri mismatch")

    # PKCE verification (mandatory in OAuth 2.1)
    if not code_verifier:
        return _oauth_error(400, "invalid_request", "code_verifier required")
    digest = hashlib.sha256(code_verifier.encode()).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    if not secrets.compare_digest(computed, entry["code_challenge"]):
        return _oauth_error(400, "invalid_grant", "PKCE verification failed")

    scope = entry["scope"]
    access_token  = await _issue_access_token(client_id, scope)
    refresh_token = await _issue_refresh_token(client_id, scope)
    logger.info("Tokens issued for client %s", client_id)
    return JSONResponse({
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": ACCESS_TOKEN_TTL,
        "refresh_token": refresh_token,
        "scope": scope,
    })


async def _grant_client_credentials(form) -> Response:
    from bao import get_oauth_client
    client_id     = form.get("client_id", "")
    client_secret = form.get("client_secret", "")
    scope         = form.get("scope", "mcp")

    if not client_id or not client_secret:
        return _oauth_error(400, "invalid_request", "client_id and client_secret required")

    client = await get_oauth_client(client_id)
    if not client:
        return _oauth_error(401, "invalid_client", "Unknown client_id")

    stored_hash = client.get("client_secret_hash", "")
    if not stored_hash or not _verify_secret(client_secret, stored_hash):
        return _oauth_error(401, "invalid_client", "Invalid client_secret")

    access_token = await _issue_access_token(client_id, scope)
    logger.info("client_credentials token issued for %s", client_id)
    return JSONResponse({
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": ACCESS_TOKEN_TTL,
        "scope": scope,
    })


async def _grant_refresh_token(form) -> Response:
    from bao import get_refresh_token, delete_refresh_token
    token     = form.get("refresh_token", "")
    client_id = form.get("client_id", "")

    entry = await get_refresh_token(token)
    if not entry:
        return _oauth_error(400, "invalid_grant", "Refresh token not found or revoked")
    if int(time.time()) > entry.get("expires", 0):
        await delete_refresh_token(token)
        return _oauth_error(400, "invalid_grant", "Refresh token expired")
    if entry.get("client_id") != client_id:
        return _oauth_error(400, "invalid_client", "client_id mismatch")

    # Rotate refresh token
    await delete_refresh_token(token)
    scope = entry["scope"]
    new_access  = await _issue_access_token(client_id, scope)
    new_refresh = await _issue_refresh_token(client_id, scope)
    logger.info("Tokens rotated for client %s", client_id)
    return JSONResponse({
        "access_token": new_access,
        "token_type": "Bearer",
        "expires_in": ACCESS_TOKEN_TTL,
        "refresh_token": new_refresh,
        "scope": scope,
    })


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hash_secret(secret: str) -> str:
    """PBKDF2-HMAC-SHA256 hash of a client secret (salt:hash, hex-encoded)."""
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", secret.encode(), salt.encode(), 200_000)
    return f"{salt}:{h.hex()}"


def _verify_secret(secret: str, stored: str) -> bool:
    parts = stored.split(":", 1)
    if len(parts) != 2:
        return False
    salt, expected = parts
    h = hashlib.pbkdf2_hmac("sha256", secret.encode(), salt.encode(), 200_000)
    return hmac.compare_digest(h.hex(), expected)


def _oauth_error(status: int, error: str, desc: str) -> JSONResponse:
    return JSONResponse({"error": error, "error_description": desc}, status_code=status)


def _e(v: str) -> str:
    return v.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def _approval_html(client_name: str, client_id: str, redirect_uri: str,
                   state: str, code_challenge: str, code_challenge_method: str,
                   scope: str, error: str = "") -> str:
    error_html = f'<p class="err">{_e(error)}</p>' if error else ""
    hid = (f'<input type="hidden" name="{k}" value="{_e(v)}">'
           for k, v in [("client_id", client_id), ("redirect_uri", redirect_uri),
                        ("state", state), ("code_challenge", code_challenge),
                        ("code_challenge_method", code_challenge_method),
                        ("scope", scope)])
    hidden = "\n    ".join(hid)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>E*TRADE MCP &mdash; Authorize</title>
  <style>
    *,*::before,*::after{{box-sizing:border-box}}
    body{{margin:0;font-family:system-ui,sans-serif;background:#f0f2f5;
         display:flex;align-items:center;justify-content:center;min-height:100vh}}
    .card{{background:#fff;border-radius:12px;box-shadow:0 4px 24px rgba(0,0,0,.10);
           padding:36px 40px;max-width:420px;width:100%;margin:20px}}
    .logo{{font-size:1.4rem;font-weight:700;color:#0070f3;margin-bottom:2px}}
    .tagline{{color:#888;font-size:.82rem;margin:0 0 20px}}
    h2{{margin:0 0 4px;font-size:1.05rem;color:#333;font-weight:500}}
    .sub{{color:#666;font-size:.87rem;margin:0 0 20px;line-height:1.5}}
    .app{{color:#0070f3;font-weight:600}}
    .divider{{border:none;border-top:1px solid #f0f0f0;margin:16px 0}}
    .tabs{{display:flex;border-bottom:2px solid #f0f0f0;margin-bottom:20px}}
    .tab{{flex:1;padding:9px;text-align:center;font-size:.85rem;font-weight:600;
          color:#999;cursor:pointer;border:none;background:none;
          border-bottom:2px solid transparent;margin-bottom:-2px;transition:all .15s}}
    .tab.active{{color:#0070f3;border-bottom-color:#0070f3}}
    .tab-panel{{display:none}}.tab-panel.active{{display:block}}
    .field{{margin-bottom:14px}}
    label{{display:block;font-size:.8rem;font-weight:600;color:#555;margin-bottom:5px;
           text-transform:uppercase;letter-spacing:.05em}}
    input[type=text],input[type=password]{{width:100%;padding:10px 13px;
      border:1.5px solid #e0e0e0;border-radius:7px;font-size:.93rem;
      transition:border .15s,box-shadow .15s}}
    input[type=text]{{font-family:monospace}}
    input:focus{{outline:none;border-color:#0070f3;box-shadow:0 0 0 3px rgba(0,112,243,.1)}}
    .btn{{margin-top:6px;width:100%;padding:11px;background:#0070f3;color:#fff;
          border:none;border-radius:8px;font-size:.97rem;font-weight:600;
          cursor:pointer;transition:background .2s}}
    .btn:hover{{background:#005bce}}
    .err{{color:#c00;font-size:.83rem;background:#fff5f5;border:1px solid #fcc;
          border-radius:6px;padding:8px 12px;margin-bottom:14px}}
    .scope{{font-size:.75rem;color:#bbb;margin-top:16px;text-align:center}}
  </style>
</head>
<body>
<div class="card">
  <div class="logo">E*TRADE MCP</div>
  <p class="tagline">OAuth 2.1 Authorization Server</p>
  <h2>Authorization Request</h2>
  <p class="sub"><span class="app">{_e(client_name)}</span> is requesting access
     to your E*TRADE account.</p>
  <hr class="divider">
  {error_html}
  <div class="tabs">
    <button class="tab active" onclick="switchTab('pin',this)">PIN</button>
    <button class="tab" onclick="switchTab('creds',this)">Client Credentials</button>
  </div>
  <form method="POST">
    {hidden}
    <input type="hidden" name="auth_method" id="auth_method" value="pin">

    <div class="tab-panel active" id="panel-pin">
      <div class="field">
        <label for="pin">PIN</label>
        <input type="password" id="pin" name="pin"
               placeholder="Enter your MCP PIN"
               autocomplete="current-password">
      </div>
    </div>

    <div class="tab-panel" id="panel-creds">
      <div class="field">
        <label for="auth_client_id">Client ID</label>
        <input type="text" id="auth_client_id" name="auth_client_id"
               placeholder="mcp-xxxxxxxxxxxx"
               autocomplete="username" spellcheck="false">
      </div>
      <div class="field">
        <label for="auth_client_secret">Client Secret</label>
        <input type="password" id="auth_client_secret" name="auth_client_secret"
               placeholder="client secret"
               autocomplete="current-password">
      </div>
    </div>

    <button class="btn" type="submit">Authorize Access</button>
  </form>
  <p class="scope">Scope: {_e(scope)}</p>
</div>
<script>
function switchTab(name, btn) {{
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('panel-' + name).classList.add('active');
  document.getElementById('auth_method').value = name;
  const first = document.querySelector('#panel-' + name + ' input');
  if (first) first.focus();
}}
</script>
</body>
</html>"""

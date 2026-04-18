"""
OpenBao credential manager — supports separate sandbox and live credential paths.

Paths:
  secret/data/etrade/live      → consumer_key, consumer_secret, access_token, access_token_secret
  secret/data/etrade/sandbox   → consumer_key, consumer_secret, access_token, access_token_secret
  secret/data/mcp/auth         → bearer_token, jwt_secret, mcp_pin
  secret/data/mcp/clients      → {client_id: {client_data}} (OAuth 2.0 clients)

Environment:
  BAO_ADDR        OpenBao address  (default: http://openbao-service.services.svc.cluster.local)
  BAO_TOKEN       Static token     (or use BAO_ROLE_ID + BAO_SECRET_ID for AppRole)
  BAO_ROLE_ID     AppRole role_id
  BAO_SECRET_ID   AppRole secret_id
"""

import os
import httpx
import logging
from typing import Optional

logger = logging.getLogger(__name__)

BAO_ADDR = os.environ.get("BAO_ADDR", "http://openbao-service.services.svc.cluster.local")
BAO_TOKEN = os.environ.get("BAO_TOKEN", "")
BAO_ROLE_ID = os.environ.get("BAO_ROLE_ID", "")
BAO_SECRET_ID = os.environ.get("BAO_SECRET_ID", "")

_token_cache: Optional[str] = None


async def _get_token() -> str:
    global _token_cache
    if BAO_TOKEN:
        return BAO_TOKEN
    if _token_cache:
        return _token_cache
    if BAO_ROLE_ID and BAO_SECRET_ID:
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"{BAO_ADDR}/v1/auth/approle/login",
                json={"role_id": BAO_ROLE_ID, "secret_id": BAO_SECRET_ID},
            )
            r.raise_for_status()
            _token_cache = r.json()["auth"]["client_token"]
            return _token_cache
    raise RuntimeError("No OpenBao token. Set BAO_TOKEN or BAO_ROLE_ID+BAO_SECRET_ID.")


def _path(sandbox: bool) -> str:
    return "secret/data/etrade/sandbox" if sandbox else "secret/data/etrade/live"


async def _read(sandbox: bool) -> dict:
    token = await _get_token()
    async with httpx.AsyncClient() as c:
        r = await c.get(
            f"{BAO_ADDR}/v1/{_path(sandbox)}",
            headers={"X-Vault-Token": token},
        )
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        return r.json()["data"]["data"]


async def _write(sandbox: bool, data: dict) -> None:
    token = await _get_token()
    try:
        existing = await _read(sandbox)
    except Exception:
        existing = {}
    existing.update(data)
    async with httpx.AsyncClient() as c:
        r = await c.post(
            f"{BAO_ADDR}/v1/{_path(sandbox)}",
            headers={"X-Vault-Token": token},
            json={"data": existing},
        )
        r.raise_for_status()


async def get_api_keys(sandbox: bool = False) -> tuple[str, str]:
    data = await _read(sandbox)
    ck = data.get("consumer_key") or data.get("api_key", "")
    cs = data.get("consumer_secret") or data.get("api_secret", "")
    if not ck or not cs:
        raise RuntimeError(
            f"No consumer_key/consumer_secret in OpenBao ({'sandbox' if sandbox else 'live'} path). "
            "Call etrade_store_api_keys() first."
        )
    return ck, cs


async def store_api_keys(consumer_key: str, consumer_secret: str, sandbox: bool = False) -> None:
    await _write(sandbox, {"consumer_key": consumer_key, "consumer_secret": consumer_secret})
    logger.info("API keys stored (%s)", "sandbox" if sandbox else "live")


async def get_access_tokens(sandbox: bool = False) -> tuple[str, str]:
    data = await _read(sandbox)
    return data.get("access_token", ""), data.get("access_token_secret", "")


async def store_access_tokens(access_token: str, access_token_secret: str, sandbox: bool = False) -> None:
    await _write(sandbox, {"access_token": access_token, "access_token_secret": access_token_secret})
    logger.info("Access tokens stored (%s)", "sandbox" if sandbox else "live")


async def store_request_token(token: str, secret: str, sandbox: bool = False) -> None:
    await _write(sandbox, {"_req_token": token, "_req_token_secret": secret})


async def get_request_token(sandbox: bool = False) -> tuple[str, str]:
    data = await _read(sandbox)
    return data.get("_req_token", ""), data.get("_req_token_secret", "")


# ── MCP auth (bearer token, JWT secret, PIN, OAuth clients) ──────────────────

async def _read_auth() -> dict:
    token = await _get_token()
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BAO_ADDR}/v1/secret/data/etrade/mcp_auth",
                        headers={"X-Vault-Token": token})
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        return r.json()["data"]["data"]


async def _write_auth(data: dict) -> None:
    token = await _get_token()
    try:
        existing = await _read_auth()
    except Exception:
        existing = {}
    existing.update(data)
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{BAO_ADDR}/v1/secret/data/etrade/mcp_auth",
                         headers={"X-Vault-Token": token},
                         json={"data": existing})
        r.raise_for_status()


async def get_bearer_token() -> str:
    data = await _read_auth()
    return data.get("bearer_token", "")


async def set_bearer_token(token: str) -> None:
    await _write_auth({"bearer_token": token})
    logger.info("MCP bearer token stored")


async def get_jwt_secret() -> str:
    import secrets as _sec
    data = await _read_auth()
    secret = data.get("jwt_secret", "")
    if not secret:
        secret = _sec.token_hex(32)
        await _write_auth({"jwt_secret": secret})
        logger.info("JWT signing secret generated and stored")
    return secret


async def get_mcp_pin() -> str:
    data = await _read_auth()
    return data.get("mcp_pin", "")


async def set_mcp_pin(pin: str) -> None:
    await _write_auth({"mcp_pin": pin})
    logger.info("MCP PIN updated")


async def _read_clients() -> dict:
    token = await _get_token()
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BAO_ADDR}/v1/secret/data/etrade/mcp_clients",
                        headers={"X-Vault-Token": token})
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        return r.json()["data"]["data"]


async def get_oauth_client(client_id: str) -> dict:
    clients = await _read_clients()
    return clients.get(client_id, {})


async def register_oauth_client(client_id: str, client_data: dict) -> None:
    token = await _get_token()
    try:
        existing = await _read_clients()
    except Exception:
        existing = {}
    existing[client_id] = client_data
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{BAO_ADDR}/v1/secret/data/etrade/mcp_clients",
                         headers={"X-Vault-Token": token},
                         json={"data": existing})
        r.raise_for_status()
    logger.info("OAuth client registered: %s", client_id)


# ── Refresh token storage ─────────────────────────────────────────────────────

async def _read_refresh_tokens() -> dict:
    token = await _get_token()
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BAO_ADDR}/v1/secret/data/etrade/mcp_refresh_tokens",
                        headers={"X-Vault-Token": token})
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        return r.json()["data"]["data"]


async def store_refresh_token(refresh_token: str, data: dict) -> None:
    bao_token = await _get_token()
    try:
        existing = await _read_refresh_tokens()
    except Exception:
        existing = {}
    existing[refresh_token] = data
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{BAO_ADDR}/v1/secret/data/etrade/mcp_refresh_tokens",
                         headers={"X-Vault-Token": bao_token},
                         json={"data": existing})
        r.raise_for_status()


async def get_refresh_token(refresh_token: str) -> dict:
    tokens = await _read_refresh_tokens()
    return tokens.get(refresh_token, {})


async def delete_refresh_token(refresh_token: str) -> None:
    bao_token = await _get_token()
    try:
        existing = await _read_refresh_tokens()
    except Exception:
        return
    existing.pop(refresh_token, None)
    async with httpx.AsyncClient() as c:
        await c.post(f"{BAO_ADDR}/v1/secret/data/etrade/mcp_refresh_tokens",
                     headers={"X-Vault-Token": bao_token},
                     json={"data": existing})

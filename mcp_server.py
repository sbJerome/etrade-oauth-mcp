"""
E*TRADE OAuth MCP Server — endpoints match the official E*TRADE OpenAPI spec.

One-time setup:
  1. etrade_authorize_start()  → open URL → approve → copy verifier
  2. etrade_authorize_complete(verifier)

Switch environments with etrade_set_sandbox(True/False). Default: sandbox.
"""

import asyncio
import functools
import logging
import os
from typing import Optional

from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_sandbox: bool = True
_client = None
_client_lock = asyncio.Lock()


async def _get_client():
    global _client
    async with _client_lock:
        if _client is None:
            from bao import get_api_keys, get_access_tokens
            from etrade_client import ETradeClient
            ck, cs = await get_api_keys(sandbox=_sandbox)
            at, ats = await get_access_tokens(sandbox=_sandbox)
            if not at:
                raise RuntimeError(
                    "Not authorized. Call etrade_authorize_start() then etrade_authorize_complete(verifier)."
                )
            _client = ETradeClient(ck, cs, at, ats, sandbox=_sandbox)
            logger.info("E*TRADE client ready (sandbox=%s)", _sandbox)
        return _client


async def _run(fn, *args, **kwargs):
    return await asyncio.to_thread(functools.partial(fn, *args, **kwargs))


mcp = FastMCP(
    "E*TRADE OAuth API",
    instructions=(
        "Official E*TRADE API via OAuth 1.0a. "
        "Sandbox (apisb.etrade.com) is active by default. "
        "Call etrade_set_sandbox(False) to switch to live (api.etrade.com). "
        "Credentials are stored in OpenBao — no manual key entry needed."
    ),
    host="0.0.0.0",
    port=8767,
)


# ── Auth & session ────────────────────────────────────────────────────────────

@mcp.tool()
async def etrade_set_sandbox(enabled: bool) -> dict:
    """
    Switch between sandbox and live mode. Resets the active session.
    enabled=True → apisb.etrade.com (sandbox)
    enabled=False → api.etrade.com (live)
    """
    global _sandbox, _client
    async with _client_lock:
        _sandbox = enabled
        _client = None
    return {
        "mode": "sandbox" if enabled else "live",
        "base_url": "https://apisb.etrade.com" if enabled else "https://api.etrade.com",
    }


@mcp.tool()
async def etrade_sandbox_status() -> dict:
    """Check whether sandbox or live mode is active."""
    return {
        "sandbox": _sandbox,
        "mode": "sandbox" if _sandbox else "live",
        "base_url": "https://apisb.etrade.com" if _sandbox else "https://api.etrade.com",
    }


@mcp.tool()
async def etrade_store_api_keys(consumer_key: str, consumer_secret: str) -> dict:
    """
    Store E*TRADE API keys in OpenBao for the current mode (sandbox or live).
    Only needed if keys haven't been stored yet. Then call etrade_authorize_start().
    """
    from bao import store_api_keys
    await store_api_keys(consumer_key, consumer_secret, sandbox=_sandbox)
    return {"status": "stored", "mode": "sandbox" if _sandbox else "live",
            "next": "Call etrade_authorize_start()"}


@mcp.tool()
async def etrade_authorize_start() -> dict:
    """
    Step 1 of OAuth: fetches a request token and returns the authorization URL.
    Open the URL in a browser, log in, approve, and copy the verifier code.
    Then call etrade_authorize_complete(verifier).
    """
    from bao import get_api_keys, store_request_token
    from etrade_client import get_request_token, build_authorize_url
    ck, cs = await get_api_keys(sandbox=_sandbox)
    token, secret = await _run(get_request_token, ck, cs)
    await store_request_token(token, secret, sandbox=_sandbox)
    return {
        "authorization_url": build_authorize_url(ck, token),
        "mode": "sandbox" if _sandbox else "live",
        "next": "Call etrade_authorize_complete(verifier='XXXXX')",
    }


@mcp.tool()
async def etrade_authorize_complete(verifier: str) -> dict:
    """
    Step 2 of OAuth: exchanges the verifier for access tokens and activates the session.
    """
    global _client
    from bao import get_api_keys, get_request_token as _bao_rt, store_access_tokens
    from etrade_client import exchange_access_token, ETradeClient
    ck, cs = await get_api_keys(sandbox=_sandbox)
    rt, rts = await _bao_rt(sandbox=_sandbox)
    if not rt:
        return {"error": "No pending request token — call etrade_authorize_start() first."}
    at, ats = await _run(exchange_access_token, ck, cs, rt, rts, verifier)
    await store_access_tokens(at, ats, sandbox=_sandbox)
    async with _client_lock:
        _client = ETradeClient(ck, cs, at, ats, sandbox=_sandbox)
    return {"status": "authorized", "mode": "sandbox" if _sandbox else "live"}


@mcp.tool()
async def etrade_session_status() -> dict:
    """Check whether OAuth tokens are stored and the client is initialized."""
    from bao import get_access_tokens
    at, _ = await get_access_tokens(sandbox=_sandbox)
    return {"mode": "sandbox" if _sandbox else "live",
            "tokens_stored": bool(at), "client_ready": _client is not None}


@mcp.tool()
async def etrade_renew_access_token() -> dict:
    """Renew the OAuth access token (call after 2+ hours of inactivity)."""
    c = await _get_client()
    return await _run(c.renew_access_token)


@mcp.tool()
async def etrade_revoke_access_token() -> dict:
    """Revoke the current OAuth access token (logout)."""
    global _client
    c = await _get_client()
    result = await _run(c.revoke_access_token)
    async with _client_lock:
        _client = None
    return result


# ── Accounts ──────────────────────────────────────────────────────────────────

@mcp.tool()
async def etrade_list_accounts() -> dict:
    """List all E*TRADE accounts for the current user."""
    c = await _get_client()
    return await _run(c.list_accounts)


@mcp.tool()
async def etrade_get_balance(
    account_id_key: str,
    inst_type: str = "BROKERAGE",
    account_type: Optional[str] = None,
    real_time_nav: bool = True,
) -> dict:
    """
    Get balance details for an account.
    account_id_key: from etrade_list_accounts.
    inst_type: BROKERAGE (required by API).
    account_type: CASH, MARGIN, etc. (optional filter).
    """
    c = await _get_client()
    return await _run(c.get_balance, account_id_key, inst_type, account_type, real_time_nav)


@mcp.tool()
async def etrade_get_balances() -> dict:
    """Get balances for all accounts in one call."""
    c = await _get_client()
    accounts_data = await _run(c.list_accounts)
    result: dict = {"accounts": accounts_data, "balances": []}
    try:
        account_list = accounts_data["AccountListResponse"]["Accounts"]["Account"]
        if isinstance(account_list, dict):
            account_list = [account_list]
        balances = []
        for acct in account_list:
            key = acct.get("accountIdKey", "")
            if key:
                bal = await _run(c.get_balance, key, acct.get("institutionType", "BROKERAGE"))
                balances.append({"accountIdKey": key, **bal})
        result["balances"] = balances
    except (KeyError, TypeError):
        pass
    return result


@mcp.tool()
async def etrade_get_portfolio(
    account_id_key: str,
    count: int = 50,
    sort_by: Optional[str] = None,
    sort_order: Optional[str] = None,
    marker: Optional[str] = None,
) -> dict:
    """
    Get portfolio positions for an account.
    sort_order: ASC or DESC.
    marker: pagination cursor from previous response.
    """
    c = await _get_client()
    return await _run(c.get_portfolio, account_id_key, count, sort_by, sort_order, marker)


@mcp.tool()
async def etrade_list_transactions(
    account_id_key: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    sort_order: Optional[str] = None,
    marker: Optional[str] = None,
    count: Optional[int] = None,
) -> dict:
    """
    List transactions for an account. Up to 2 years of history available.
    start_date / end_date: MMDDYYYY format (e.g. '01152025').
    sort_order: ASC or DESC.
    marker: pagination cursor from previous response.
    """
    c = await _get_client()
    return await _run(c.list_transactions, account_id_key, start_date, end_date,
                      sort_order, marker, count)


@mcp.tool()
async def etrade_get_transaction(
    account_id_key: str,
    transaction_id: str,
    store_id: Optional[str] = None,
) -> dict:
    """Get details for a specific transaction."""
    c = await _get_client()
    return await _run(c.get_transaction, account_id_key, transaction_id, store_id)


# ── Market data ───────────────────────────────────────────────────────────────

@mcp.tool()
async def etrade_get_quotes(
    symbols: str,
    detail_flag: Optional[str] = None,
    require_earnings_date: bool = False,
    override_symbol_count: bool = False,
    skip_mini_options_check: bool = False,
) -> dict:
    """
    Get quotes for one or more symbols (up to 25, or 50 with override_symbol_count=True).
    symbols: comma-separated tickers, e.g. 'AAPL,TSLA,MSFT'.
    detail_flag: ALL, FUNDAMENTAL, INTRADAY, OPTIONS, WEEK_52, MF_DETAIL.
    """
    c = await _get_client()
    return await _run(c.get_quotes, symbols, detail_flag,
                      require_earnings_date, override_symbol_count, skip_mini_options_check)


@mcp.tool()
async def etrade_symbol_lookup(search: str) -> dict:
    """Look up securities by full or partial company name."""
    c = await _get_client()
    return await _run(c.symbol_lookup, search)


@mcp.tool()
async def etrade_get_option_chains(
    symbol: str,
    expiry_year: Optional[int] = None,
    expiry_month: Optional[int] = None,
    expiry_day: Optional[int] = None,
    strike_price_near: Optional[float] = None,
    no_of_strikes: Optional[int] = None,
    include_weekly: bool = False,
    skip_adjusted: bool = True,
    option_category: Optional[str] = None,
    chain_type: Optional[str] = None,
    price_type: Optional[str] = None,
) -> dict:
    """
    Get option chains for a symbol.
    option_category: STANDARD, ALL, MINI.
    chain_type: CALL, PUT, CALLPUT.
    price_type: ATNM, ALL.
    """
    c = await _get_client()
    return await _run(c.get_option_chains, symbol,
                      expiry_year, expiry_month, expiry_day,
                      strike_price_near, no_of_strikes,
                      include_weekly, skip_adjusted,
                      option_category, chain_type, price_type)


@mcp.tool()
async def etrade_get_option_expire_dates(
    symbol: str,
    expiry_type: Optional[str] = None,
) -> dict:
    """Get option expiration dates for a symbol."""
    c = await _get_client()
    return await _run(c.get_option_expire_dates, symbol, expiry_type)


# ── Orders ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def etrade_list_orders(
    account_id_key: str,
    count: int = 25,
    marker: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    symbol: Optional[str] = None,
    security_type: Optional[str] = None,
    transaction_type: Optional[str] = None,
    market_session: Optional[str] = None,
) -> dict:
    """
    List orders for an account (max 100 per page).
    status: OPEN, EXECUTED, CANCELLED, INDIVIDUAL_FILLS, CANCEL_REQUESTED, EXPIRED, REJECTED.
    from_date / to_date: MMDDYYYY format.
    security_type: EQ (includes ETFs), OPTN, MF, MMF.
    transaction_type: BUY, SELL, SELL_SHORT, BUY_TO_COVER, MF_EXCHANGE.
    market_session: REGULAR, EXTENDED.
    marker: pagination cursor from previous response.
    """
    c = await _get_client()
    return await _run(c.list_orders, account_id_key, marker, count, status,
                      from_date, to_date, symbol, security_type,
                      transaction_type, market_session)


@mcp.tool()
async def etrade_preview_order(
    account_id_key: str,
    symbol: str,
    order_action: str,
    quantity: Optional[int] = None,
    price_type: str = "MARKET",
    limit_price: Optional[float] = None,
    stop_price: Optional[float] = None,
    order_term: str = "GOOD_FOR_DAY",
    security_type: str = "EQ",
    market_session: str = "REGULAR",
    client_order_id: Optional[str] = None,
    call_or_put: Optional[str] = None,
    expiry_date: Optional[str] = None,
    strike_price: Optional[float] = None,
    investment_amount: Optional[float] = None,
) -> dict:
    """
    Preview an order. Returns a previewId required by etrade_place_order.

    order_action: BUY, SELL, BUY_TO_COVER, SELL_SHORT, BUY_OPEN, BUY_CLOSE, SELL_OPEN, SELL_CLOSE.
    price_type: MARKET, LIMIT, STOP, STOP_LIMIT.
    order_term: GOOD_FOR_DAY, IMMEDIATE_OR_CANCEL, FILL_OR_KILL, GOOD_UNTIL_CANCEL.
    security_type: EQ (stocks and ETFs), OPTN, MF, BOND. Note: ETFs must use EQ.
    market_session: REGULAR, EXTENDED.
    client_order_id: auto-generated if omitted; save it to use in etrade_place_order.
    --- Options (security_type=OPTN) ---
    call_or_put: CALL or PUT.
    expiry_date: option expiry date as YYYY-MM-DD.
    strike_price: option strike price.
    quantity: number of contracts.
    --- Mutual Funds (security_type=MF) ---
    investment_amount: dollar amount to invest (replaces quantity). Price type auto-set to NET_ASSET_VALUE.
    """
    if security_type == "ETF":
        security_type = "EQ"
    c = await _get_client()
    return await _run(c.preview_order, account_id_key,
                      symbol=symbol, order_action=order_action, quantity=quantity,
                      price_type=price_type, limit_price=limit_price, stop_price=stop_price,
                      order_term=order_term, security_type=security_type,
                      market_session=market_session, client_order_id=client_order_id,
                      call_or_put=call_or_put, expiry_date=expiry_date,
                      strike_price=strike_price, investment_amount=investment_amount)


@mcp.tool()
async def etrade_place_order(
    account_id_key: str,
    symbol: str,
    order_action: str,
    client_order_id: str,
    preview_id: int,
    quantity: Optional[int] = None,
    price_type: str = "MARKET",
    limit_price: Optional[float] = None,
    stop_price: Optional[float] = None,
    order_term: str = "GOOD_FOR_DAY",
    security_type: str = "EQ",
    market_session: str = "REGULAR",
    call_or_put: Optional[str] = None,
    expiry_date: Optional[str] = None,
    strike_price: Optional[float] = None,
    investment_amount: Optional[float] = None,
) -> dict:
    """
    Place a previewed order. Call etrade_preview_order first.

    client_order_id: must match the value used in etrade_preview_order.
    preview_id: the previewId returned by etrade_preview_order.
    All other fields must match the preview exactly.
    security_type: EQ (stocks and ETFs), OPTN, MF, BOND. Note: ETFs must use EQ.
    --- Options (security_type=OPTN) ---
    call_or_put: CALL or PUT.
    expiry_date: option expiry date as YYYY-MM-DD.
    strike_price: option strike price.
    --- Mutual Funds (security_type=MF) ---
    investment_amount: dollar amount (replaces quantity).
    """
    if security_type == "ETF":
        security_type = "EQ"
    c = await _get_client()
    return await _run(c.place_order, account_id_key,
                      symbol=symbol, order_action=order_action, quantity=quantity,
                      client_order_id=client_order_id, preview_id=preview_id,
                      price_type=price_type, limit_price=limit_price, stop_price=stop_price,
                      order_term=order_term, security_type=security_type,
                      market_session=market_session,
                      call_or_put=call_or_put, expiry_date=expiry_date,
                      strike_price=strike_price, investment_amount=investment_amount)


@mcp.tool()
async def etrade_preview_mf_order(
    account_id_key: str,
    symbol: str,
    order_action: str,
    investment_amount: Optional[float] = None,
    quantity: Optional[float] = None,
    quantity_type: str = "DOLLAR",
    client_order_id: Optional[str] = None,
) -> dict:
    """
    Preview a mutual fund order. Returns a previewId required by etrade_place_mf_order.

    order_action: BUY, SELL, MF_EXCHANGE.
    investment_amount: dollar amount to invest/redeem (use with quantity_type=DOLLAR).
    quantity: share count (use with quantity_type=QUANTITY) or omit for dollar-based orders.
    quantity_type: DOLLAR (default), QUANTITY, or ALL_I_OWN (full redemption).
    price_type and order_term are always NET_ASSET_VALUE / GOOD_FOR_DAY for MF orders.
    """
    c = await _get_client()
    return await _run(c.preview_mf_order, account_id_key,
                      symbol=symbol, order_action=order_action,
                      investment_amount=investment_amount, quantity=quantity,
                      quantity_type=quantity_type, client_order_id=client_order_id)


@mcp.tool()
async def etrade_place_mf_order(
    account_id_key: str,
    symbol: str,
    order_action: str,
    client_order_id: str,
    preview_id: int,
    investment_amount: Optional[float] = None,
    quantity: Optional[float] = None,
    quantity_type: str = "DOLLAR",
) -> dict:
    """
    Place a previewed mutual fund order. Call etrade_preview_mf_order first.

    client_order_id and preview_id must match the values from etrade_preview_mf_order.
    All other fields must match the preview exactly.
    """
    c = await _get_client()
    return await _run(c.place_mf_order, account_id_key,
                      symbol=symbol, order_action=order_action,
                      investment_amount=investment_amount, quantity=quantity,
                      quantity_type=quantity_type, client_order_id=client_order_id,
                      preview_id=preview_id)


@mcp.tool()
async def etrade_skip_preview(
    account_id_key: str,
    symbol: str,
    order_action: str,
    quantity: Optional[int] = None,
    price_type: str = "MARKET",
    limit_price: Optional[float] = None,
    stop_price: Optional[float] = None,
    order_term: str = "GOOD_FOR_DAY",
    security_type: str = "EQ",
    market_session: str = "REGULAR",
    call_or_put: Optional[str] = None,
    expiry_date: Optional[str] = None,
    strike_price: Optional[float] = None,
    investment_amount: Optional[float] = None,
    quantity_type: str = "DOLLAR",
) -> dict:
    """
    Preview and immediately place an order in one step.

    Accepts the same parameters as etrade_preview_order / etrade_preview_mf_order.
    Routes to the MF flow automatically when security_type=MF.
    Returns the place order response plus the preview that was used.
    """
    import xml.etree.ElementTree as ET

    if security_type == "ETF":
        security_type = "EQ"

    c = await _get_client()

    # Step 1 — preview
    if security_type == "MF":
        preview_result = await _run(c.preview_mf_order, account_id_key,
                                    symbol=symbol, order_action=order_action,
                                    investment_amount=investment_amount, quantity=quantity,
                                    quantity_type=quantity_type)
    else:
        preview_result = await _run(c.preview_order, account_id_key,
                                    symbol=symbol, order_action=order_action, quantity=quantity,
                                    price_type=price_type, limit_price=limit_price,
                                    stop_price=stop_price, order_term=order_term,
                                    security_type=security_type, market_session=market_session,
                                    call_or_put=call_or_put, expiry_date=expiry_date,
                                    strike_price=strike_price)

    # Extract previewId and clientOrderId from XML response
    raw_xml = preview_result.get("raw", "")
    if not raw_xml or preview_result.get("status_code", 200) >= 400:
        return {"error": "Preview failed", "preview_response": preview_result}

    try:
        root = ET.fromstring(raw_xml)
        preview_id = int(root.findtext(".//previewId"))
        client_order_id = root.findtext(".//clientOrderId")
    except Exception as e:
        return {"error": f"Could not parse previewId from response: {e}",
                "preview_response": preview_result}

    # Step 2 — place
    if security_type == "MF":
        place_result = await _run(c.place_mf_order, account_id_key,
                                  symbol=symbol, order_action=order_action,
                                  investment_amount=investment_amount, quantity=quantity,
                                  quantity_type=quantity_type,
                                  client_order_id=client_order_id, preview_id=preview_id)
    else:
        place_result = await _run(c.place_order, account_id_key,
                                  symbol=symbol, order_action=order_action, quantity=quantity,
                                  price_type=price_type, limit_price=limit_price,
                                  stop_price=stop_price, order_term=order_term,
                                  security_type=security_type, market_session=market_session,
                                  call_or_put=call_or_put, expiry_date=expiry_date,
                                  strike_price=strike_price,
                                  client_order_id=client_order_id, preview_id=preview_id)

    return {"placed": place_result, "preview_id": preview_id, "client_order_id": client_order_id}


@mcp.tool()
async def etrade_cancel_order(account_id_key: str, order_id: int) -> dict:
    """Cancel an open order by order ID."""
    c = await _get_client()
    return await _run(c.cancel_order, account_id_key, order_id)


@mcp.tool()
async def etrade_change_order_preview(
    account_id_key: str,
    order_id: int,
    symbol: str,
    order_action: str,
    quantity: Optional[int] = None,
    price_type: str = "MARKET",
    limit_price: Optional[float] = None,
    stop_price: Optional[float] = None,
    order_term: str = "GOOD_FOR_DAY",
    security_type: str = "EQ",
    market_session: str = "REGULAR",
    client_order_id: Optional[str] = None,
    call_or_put: Optional[str] = None,
    expiry_date: Optional[str] = None,
    strike_price: Optional[float] = None,
    investment_amount: Optional[float] = None,
) -> dict:
    """Preview a change to an existing open order. Returns a previewId for etrade_change_order_place."""
    if security_type == "ETF":
        security_type = "EQ"
    c = await _get_client()
    return await _run(c.change_order_preview, account_id_key, order_id,
                      symbol=symbol, order_action=order_action, quantity=quantity,
                      price_type=price_type, limit_price=limit_price, stop_price=stop_price,
                      order_term=order_term, security_type=security_type,
                      market_session=market_session, client_order_id=client_order_id,
                      call_or_put=call_or_put, expiry_date=expiry_date,
                      strike_price=strike_price, investment_amount=investment_amount)


@mcp.tool()
async def etrade_change_order_place(
    account_id_key: str,
    order_id: int,
    symbol: str,
    order_action: str,
    client_order_id: str,
    preview_id: int,
    quantity: Optional[int] = None,
    price_type: str = "MARKET",
    limit_price: Optional[float] = None,
    stop_price: Optional[float] = None,
    order_term: str = "GOOD_FOR_DAY",
    security_type: str = "EQ",
    market_session: str = "REGULAR",
    call_or_put: Optional[str] = None,
    expiry_date: Optional[str] = None,
    strike_price: Optional[float] = None,
    investment_amount: Optional[float] = None,
) -> dict:
    """Apply a previewed change to an existing open order."""
    if security_type == "ETF":
        security_type = "EQ"
    c = await _get_client()
    return await _run(c.change_order_place, account_id_key, order_id,
                      symbol=symbol, order_action=order_action, quantity=quantity,
                      client_order_id=client_order_id, preview_id=preview_id,
                      price_type=price_type, limit_price=limit_price, stop_price=stop_price,
                      order_term=order_term, security_type=security_type,
                      market_session=market_session,
                      call_or_put=call_or_put, expiry_date=expiry_date,
                      strike_price=strike_price, investment_amount=investment_amount)


# ── MCP auth management ───────────────────────────────────────────────────────

@mcp.tool()
async def etrade_set_mcp_pin(pin: str) -> dict:
    """
    Set the PIN used on the OAuth authorization page (quick login option).
    """
    from bao import set_mcp_pin
    await set_mcp_pin(pin)
    return {"status": "stored", "note": "PIN active for next OAuth authorization"}


@mcp.tool()
async def etrade_create_oauth_client(client_name: str) -> dict:
    """
    Create a pre-registered OAuth 2.1 client with client_id + client_secret.
    Use this for ChatGPT / OpenAI or any client that supports client_credentials grant.
    Returns client_id and client_secret — save the secret, it cannot be retrieved later.

    Token endpoint: POST /oauth/token
      grant_type=client_credentials
      client_id=<returned client_id>
      client_secret=<returned client_secret>
    """
    import secrets as _sec
    from auth import _hash_secret
    from bao import register_oauth_client
    client_id     = "mcp-" + _sec.token_urlsafe(12)
    client_secret = _sec.token_urlsafe(32)
    await register_oauth_client(client_id, {
        "client_id": client_id,
        "client_name": client_name,
        "redirect_uris": [],
        "client_secret_hash": _hash_secret(client_secret),
        "grant_types": ["client_credentials"],
        "created_at": int(__import__("time").time()),
    })
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "token_url": f"{__import__('os').environ.get('MCP_ISSUER', 'http://localhost:8767')}/oauth/token",
        "grant_type": "client_credentials",
        "warning": "Save the client_secret now — it is not stored in plaintext and cannot be retrieved.",
    }


# ── Alerts ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def etrade_list_alerts(
    count: int = 25,
    category: Optional[str] = None,
    status: Optional[str] = None,
    direction: Optional[str] = None,
    search: Optional[str] = None,
) -> dict:
    """
    List alerts (max 300 per page).
    category: STOCK, ACCOUNT.
    status: READ, UNREAD, DELETED.
    direction: ASC, DESC.
    search: filter by keyword.
    """
    c = await _get_client()
    return await _run(c.list_alerts, count, category, status, direction, search)


@mcp.tool()
async def etrade_get_alert(alert_id: int) -> dict:
    """Get details for a specific alert."""
    c = await _get_client()
    return await _run(c.get_alert, alert_id)


@mcp.tool()
async def etrade_delete_alerts(alert_ids: list[int]) -> dict:
    """Delete one or more alerts by ID."""
    c = await _get_client()
    return await _run(c.delete_alerts, alert_ids)


# ── OAuth 2.0 + well-known routes ────────────────────────────────────────────

from auth import handle_metadata, handle_register, handle_authorize, handle_token


@mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
async def route_metadata(request):
    return await handle_metadata(request)


@mcp.custom_route("/oauth/register", methods=["POST"])
async def route_register(request):
    return await handle_register(request)


@mcp.custom_route("/oauth/authorize", methods=["GET", "POST"])
async def route_authorize(request):
    return await handle_authorize(request)


@mcp.custom_route("/oauth/token", methods=["POST"])
async def route_token(request):
    return await handle_token(request)


@mcp.custom_route("/.well-known/openid-configuration", methods=["GET"])
async def route_oidc(request):
    return await handle_metadata(request)


@mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
async def route_protected_resource(request):
    from starlette.responses import JSONResponse
    return JSONResponse({
        "resource": os.environ.get("MCP_ISSUER", "http://localhost:8767"),
        "authorization_servers": [os.environ.get("MCP_ISSUER", "http://localhost:8767")],
    })


@mcp.custom_route("/health", methods=["GET"])
async def route_health(request):
    from starlette.responses import JSONResponse
    return JSONResponse({"status": "ok"})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import uvicorn
    from auth import BearerAuthMiddleware

    parser = argparse.ArgumentParser(description="E*TRADE OAuth MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--live", action="store_true", default=False)
    args = parser.parse_args()

    if args.live:
        _sandbox = False
        logger.info("Starting in LIVE mode (api.etrade.com)")
    else:
        logger.info("Starting in SANDBOX mode (apisb.etrade.com)")

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        sse_app  = mcp.sse_app()
        http_app = mcp.streamable_http_app()

        class PathRouter:
            """Routes /mcp to streamable-http, everything else to SSE.
            Properly runs both app lifespans concurrently so the
            streamable-http session manager's TaskGroup initializes."""

            async def __call__(self, scope, receive, send):
                if scope["type"] == "lifespan":
                    await self._dual_lifespan(scope, receive, send)
                elif scope.get("path", "").startswith("/mcp"):
                    await http_app(scope, receive, send)
                else:
                    await sse_app(scope, receive, send)

            async def _dual_lifespan(self, scope, receive, send):
                import anyio
                from anyio.streams.memory import MemoryObjectSendStream, MemoryObjectReceiveStream

                sse_tx,  sse_rx  = anyio.create_memory_object_stream(10)
                http_tx, http_rx = anyio.create_memory_object_stream(10)

                sse_up   = anyio.Event()
                http_up  = anyio.Event()
                sse_down = anyio.Event()
                http_down = anyio.Event()

                async def sse_send(msg):
                    if msg["type"] == "lifespan.startup.complete":   sse_up.set()
                    if msg["type"] == "lifespan.shutdown.complete":  sse_down.set()

                async def http_send(msg):
                    if msg["type"] == "lifespan.startup.complete":   http_up.set()
                    if msg["type"] == "lifespan.shutdown.complete":  http_down.set()

                async with anyio.create_task_group() as tg:
                    tg.start_soon(sse_app,  scope, sse_rx.receive,  sse_send)
                    tg.start_soon(http_app, scope, http_rx.receive, http_send)

                    await receive()                                        # lifespan.startup
                    await sse_tx.send({"type": "lifespan.startup"})
                    await http_tx.send({"type": "lifespan.startup"})
                    await sse_up.wait()
                    await http_up.wait()
                    await send({"type": "lifespan.startup.complete"})

                    await receive()                                        # lifespan.shutdown
                    await sse_tx.send({"type": "lifespan.shutdown"})
                    await http_tx.send({"type": "lifespan.shutdown"})
                    await sse_down.wait()
                    await http_down.wait()
                    await send({"type": "lifespan.shutdown.complete"})
                    tg.cancel_scope.cancel()

        router = PathRouter()
        router_with_auth = BearerAuthMiddleware(app=router)
        logger.info("Serving SSE at /sse and streamable-http at /mcp")
        uvicorn.run(router_with_auth, host="0.0.0.0", port=args.port, log_level="info")

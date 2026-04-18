"""
E*TRADE OAuth 1.0a client using rauth.
Endpoints match the official OpenAPI spec exactly.
All methods are synchronous — call via asyncio.to_thread() from async handlers.
"""

import random
import logging
from typing import Optional

import requests
from rauth import OAuth1Service, OAuth1Session

logger = logging.getLogger(__name__)

PROD_BASE    = "https://api.etrade.com"
SANDBOX_BASE = "https://apisb.etrade.com"  # sandbox — use PROD_BASE for live
AUTH_BASE  = "https://api.etrade.com"   # OAuth dance always targets prod
AUTHORIZE_URL = "https://us.etrade.com/e/t/etws/authorize?key={}&token={}"


# ── OAuth helpers (module-level, called before a client exists) ───────────────

def _build_service(consumer_key: str, consumer_secret: str) -> OAuth1Service:
    return OAuth1Service(
        name="etrade",
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
        request_token_url=f"{AUTH_BASE}/oauth/request_token",
        access_token_url=f"{AUTH_BASE}/oauth/access_token",
        authorize_url=AUTHORIZE_URL,
        base_url=AUTH_BASE,
    )


def get_request_token(consumer_key: str, consumer_secret: str) -> tuple[str, str]:
    svc = _build_service(consumer_key, consumer_secret)
    token, secret = svc.get_request_token(params={"oauth_callback": "oob", "format": "json"})
    return token, secret


def build_authorize_url(consumer_key: str, request_token: str) -> str:
    return AUTHORIZE_URL.format(consumer_key, request_token)


def exchange_access_token(
    consumer_key: str, consumer_secret: str,
    request_token: str, request_token_secret: str,
    verifier: str,
) -> tuple[str, str]:
    svc = _build_service(consumer_key, consumer_secret)
    session = svc.get_auth_session(
        request_token, request_token_secret,
        params={"oauth_verifier": verifier},
    )
    return session.access_token, session.access_token_secret


# ── Client ────────────────────────────────────────────────────────────────────

class ETradeClient:
    """Synchronous E*TRADE client backed by rauth OAuth1Session."""

    def __init__(
        self,
        consumer_key: str,
        consumer_secret: str,
        access_token: str,
        access_token_secret: str,
        sandbox: bool = False,
    ):
        self.consumer_key = consumer_key
        self.base_url = SANDBOX_BASE if sandbox else PROD_BASE
        self.sandbox = sandbox
        self._sess = OAuth1Session(
            consumer_key, consumer_secret,
            access_token=access_token,
            access_token_secret=access_token_secret,
        )

    def _get(self, path: str, params: dict = None, **kw) -> requests.Response:
        url = f"{self.base_url}{path}"
        r = self._sess.get(url, header_auth=True, params={k: v for k, v in (params or {}).items() if v is not None}, **kw)
        logger.debug("GET %s → %d", url, r.status_code)
        return r

    def _post(self, path: str, **kw) -> requests.Response:
        url = f"{self.base_url}{path}"
        r = self._sess.post(url, header_auth=True, **kw)
        logger.debug("POST %s → %d", url, r.status_code)
        return r

    def _put(self, path: str, **kw) -> requests.Response:
        url = f"{self.base_url}{path}"
        r = self._sess.put(url, header_auth=True, **kw)
        logger.debug("PUT %s → %d", url, r.status_code)
        return r

    def _delete(self, path: str, **kw) -> requests.Response:
        url = f"{self.base_url}{path}"
        r = self._sess.delete(url, header_auth=True, **kw)
        logger.debug("DELETE %s → %d", url, r.status_code)
        return r

    def _parse(self, r: requests.Response) -> dict:
        try:
            return r.json()
        except Exception:
            return {"raw": r.text, "status_code": r.status_code}

    def _xml_headers(self) -> dict:
        return {"Content-Type": "application/xml", "consumerKey": self.consumer_key}

    # ── Token management ──────────────────────────────────────────────────────

    def renew_access_token(self) -> dict:
        """GET /oauth/renew_access_token"""
        return self._parse(self._get("/oauth/renew_access_token"))

    def revoke_access_token(self) -> dict:
        """GET /oauth/revoke_access_token"""
        return self._parse(self._get("/oauth/revoke_access_token"))

    # ── Accounts ──────────────────────────────────────────────────────────────

    def list_accounts(self) -> dict:
        """GET /v1/accounts/list"""
        return self._parse(self._get("/v1/accounts/list"))

    def get_balance(self, account_id_key: str,
                    inst_type: str = "BROKERAGE",
                    account_type: Optional[str] = None,
                    real_time_nav: bool = True) -> dict:
        """GET /v1/accounts/{accountIdKey}/balance"""
        return self._parse(self._get(
            f"/v1/accounts/{account_id_key}/balance",
            params={"instType": inst_type, "accountType": account_type,
                    "realTimeNAV": str(real_time_nav).lower()},
        ))

    def get_portfolio(self, account_id_key: str,
                      count: int = 50,
                      sort_by: Optional[str] = None,
                      sort_order: Optional[str] = None,
                      marker: Optional[str] = None) -> dict:
        """GET /v1/accounts/{accountIdKey}/portfolio"""
        return self._parse(self._get(
            f"/v1/accounts/{account_id_key}/portfolio",
            params={"count": count, "sortBy": sort_by,
                    "sortOrder": sort_order, "marker": marker},
        ))

    def list_transactions(self, account_id_key: str,
                          start_date: Optional[str] = None,
                          end_date: Optional[str] = None,
                          sort_order: Optional[str] = None,
                          marker: Optional[str] = None,
                          count: Optional[int] = None) -> dict:
        """GET /v1/accounts/{accountIdKey}/transactions  (dates: MMDDYYYY)"""
        return self._parse(self._get(
            f"/v1/accounts/{account_id_key}/transactions",
            params={"startDate": start_date, "endDate": end_date,
                    "sortOrder": sort_order, "marker": marker, "count": count},
        ))

    def get_transaction(self, account_id_key: str, transaction_id: str,
                        store_id: Optional[str] = None) -> dict:
        """GET /v1/accounts/{accountIdKey}/transactions/{transactionId}"""
        return self._parse(self._get(
            f"/v1/accounts/{account_id_key}/transactions/{transaction_id}",
            params={"storeId": store_id},
        ))

    # ── Market data ───────────────────────────────────────────────────────────

    def get_quotes(self, symbols: str,
                   detail_flag: Optional[str] = None,
                   require_earnings_date: bool = False,
                   override_symbol_count: bool = False,
                   skip_mini_options_check: bool = False) -> dict:
        """GET /v1/market/quote/{symbols}"""
        return self._parse(self._get(
            f"/v1/market/quote/{symbols}",
            params={
                "detailFlag": detail_flag,
                "requireEarningsDate": str(require_earnings_date).lower(),
                "overrideSymbolCount": str(override_symbol_count).lower(),
                "skipMiniOptionsCheck": str(skip_mini_options_check).lower(),
            },
        ))

    def symbol_lookup(self, search: str) -> dict:
        """GET /v1/market/lookup/{search}"""
        return self._parse(self._get(f"/v1/market/lookup/{search}"))

    def get_option_chains(self,
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
                          price_type: Optional[str] = None) -> dict:
        """GET /v1/market/optionchains"""
        return self._parse(self._get(
            "/v1/market/optionchains",
            params={
                "symbol": symbol,
                "expiryYear": expiry_year,
                "expiryMonth": expiry_month,
                "expiryDay": expiry_day,
                "strikePriceNear": strike_price_near,
                "noOfStrikes": no_of_strikes,
                "includeWeekly": str(include_weekly).lower(),
                "skipAdjusted": str(skip_adjusted).lower(),
                "optionCategory": option_category,
                "chainType": chain_type,
                "priceType": price_type,
            },
        ))

    def get_option_expire_dates(self, symbol: str,
                                expiry_type: Optional[str] = None) -> dict:
        """GET /v1/market/optionexpiredate"""
        return self._parse(self._get(
            "/v1/market/optionexpiredate",
            params={"symbol": symbol, "expiryType": expiry_type},
        ))

    # ── Orders ────────────────────────────────────────────────────────────────

    def list_orders(self, account_id_key: str,
                    marker: Optional[str] = None,
                    count: int = 25,
                    status: Optional[str] = None,
                    from_date: Optional[str] = None,
                    to_date: Optional[str] = None,
                    symbol: Optional[str] = None,
                    security_type: Optional[str] = None,
                    transaction_type: Optional[str] = None,
                    market_session: Optional[str] = None) -> dict:
        """GET /v1/accounts/{accountIdKey}/orders"""
        return self._parse(self._get(
            f"/v1/accounts/{account_id_key}/orders",
            params={
                "marker": marker, "count": count, "status": status,
                "fromDate": from_date, "toDate": to_date, "symbol": symbol,
                "securityType": security_type, "transactionType": transaction_type,
                "marketSession": market_session,
            },
        ))

    def _order_xml(self, tag: str, fields: dict) -> str:
        client_order_id = fields.get("client_order_id") or str(random.randint(1000000000, 9999999999))
        security_type   = fields.get("security_type", "EQ")
        price_type      = fields.get("price_type", "MARKET")
        order_term      = fields.get("order_term", "GOOD_FOR_DAY")
        limit_price     = fields.get("limit_price") or ""
        stop_price      = fields.get("stop_price") or ""
        market_session  = fields.get("market_session", "REGULAR")
        symbol          = fields["symbol"]
        order_action    = fields["order_action"]

        preview_block = ""
        if tag == "PlaceOrderRequest":
            preview_id = fields.get("preview_id")
            if preview_id:
                preview_block = f"<PreviewIds><previewId>{preview_id}</previewId></PreviewIds>"

        # Options instrument block
        if security_type == "OPTN":
            # Auto-parse OSI symbol: CHPT--260515C00007000 or CHPT  260515C00007000
            underlying = symbol
            call_or_put  = fields.get("call_or_put") or ""
            strike_price = fields.get("strike_price") or ""
            expiry_year = expiry_month = expiry_day = ""
            expiry_date = fields.get("expiry_date") or ""

            import re
            osi = re.match(r'^([A-Z]+)\s*-*(\d{6})([CP])(\d{8})$', symbol.replace(" ", ""))
            if osi:
                underlying   = osi.group(1)
                yymmdd       = osi.group(2)
                expiry_year  = "20" + yymmdd[0:2]
                expiry_month = yymmdd[2:4]
                expiry_day   = yymmdd[4:6]
                call_or_put  = "CALL" if osi.group(3) == "C" else "PUT"
                strike_price = str(int(osi.group(4)) / 1000)
            elif expiry_date:
                parts = expiry_date.split("-")
                if len(parts) == 3:
                    expiry_year, expiry_month, expiry_day = parts

            call_or_put  = (call_or_put or "CALL").upper()
            quantity = int(fields.get("quantity") or 1)
            product_block = (
                f"<Product>"
                f"<securityType>OPTN</securityType>"
                f"<symbol>{underlying}</symbol>"
                f"<callPut>{call_or_put}</callPut>"
                f"<expiryYear>{expiry_year}</expiryYear>"
                f"<expiryMonth>{expiry_month}</expiryMonth>"
                f"<expiryDay>{expiry_day}</expiryDay>"
                f"<strikePrice>{strike_price}</strikePrice>"
                f"</Product>"
            )
            quantity_block = f"<quantityType>QUANTITY</quantityType><quantity>{quantity}</quantity>"

        # Equity / default
        else:
            quantity = int(fields["quantity"])
            product_block = (
                f"<Product>"
                f"<securityType>{security_type}</securityType>"
                f"<symbol>{symbol}</symbol>"
                f"</Product>"
            )
            quantity_block = f"<quantityType>QUANTITY</quantityType><quantity>{quantity}</quantity>"

        return (
            f"<{tag}>"
            f"<orderType>{security_type}</orderType>"
            f"<clientOrderId>{client_order_id}</clientOrderId>"
            f"{preview_block}"
            f"<Order>"
            f"<allOrNone>false</allOrNone>"
            f"<priceType>{price_type}</priceType>"
            f"<orderTerm>{order_term}</orderTerm>"
            f"<marketSession>{market_session}</marketSession>"
            f"<stopPrice>{stop_price}</stopPrice>"
            f"<limitPrice>{limit_price}</limitPrice>"
            f"<Instrument>"
            f"{product_block}"
            f"<orderAction>{order_action}</orderAction>"
            f"{quantity_block}"
            f"</Instrument>"
            f"</Order>"
            f"</{tag}>"
        )

    def preview_order(self, account_id_key: str, **fields) -> dict:
        """POST /v1/accounts/{accountIdKey}/orders/preview"""
        return self._parse(self._post(
            f"/v1/accounts/{account_id_key}/orders/preview",
            data=self._order_xml("PreviewOrderRequest", fields),
            headers=self._xml_headers(),
        ))

    def place_order(self, account_id_key: str, **fields) -> dict:
        """POST /v1/accounts/{accountIdKey}/orders/place"""
        return self._parse(self._post(
            f"/v1/accounts/{account_id_key}/orders/place",
            data=self._order_xml("PlaceOrderRequest", fields),
            headers=self._xml_headers(),
        ))

    def _mf_order_json(self, request_key: str, fields: dict) -> dict:
        client_order_id   = fields.get("client_order_id") or str(random.randint(1000000000, 9999999999))
        symbol            = fields["symbol"]
        mf_transaction    = (fields.get("order_action") or "BUY").upper()
        quantity_type     = fields.get("quantity_type", "DOLLAR").upper()
        investment_amount = fields.get("investment_amount") or fields.get("quantity") or 0
        reinvest_option   = fields.get("reinvest_option", "REINVEST").upper()

        payload: dict = {
            request_key: {
                "orderType": "MF",
                "clientOrderId": client_order_id,
                "Order": {
                    "allOrNone": "false",
                    "priceType": "MARKET",
                    "orderTerm": "GOOD_FOR_DAY",
                    "marketSession": "REGULAR",
                    "Instrument": {
                        "Product": {"securityType": "MF", "symbol": symbol},
                        "mfTransaction": mf_transaction,
                        "mfQuantity": str(investment_amount),
                        "quantityType": quantity_type,
                        "reInvestOption": reinvest_option,
                    },
                },
            }
        }

        if request_key == "PlaceOrderRequest":
            preview_id = fields.get("preview_id")
            client_oid = fields.get("client_order_id", client_order_id)
            if preview_id:
                payload[request_key]["PreviewIds"] = {"previewId": preview_id}
                payload[request_key]["clientOrderId"] = client_oid

        return payload

    def preview_mf_order(self, account_id_key: str, **fields) -> dict:
        """POST /v1/accounts/{accountIdKey}/orders/preview.json — mutual fund"""
        payload = self._mf_order_json("PreviewOrderRequest", fields)
        logger.info("MF preview JSON: %s", payload)
        r = self._post(f"/v1/accounts/{account_id_key}/orders/preview.json", json=payload)
        logger.info("MF preview response [%d]: %s", r.status_code, r.text)
        return self._parse(r)

    def place_mf_order(self, account_id_key: str, **fields) -> dict:
        """POST /v1/accounts/{accountIdKey}/orders/place.json — mutual fund"""
        payload = self._mf_order_json("PlaceOrderRequest", fields)
        logger.info("MF place JSON: %s", payload)
        r = self._post(f"/v1/accounts/{account_id_key}/orders/place.json", json=payload)
        logger.info("MF place response [%d]: %s", r.status_code, r.text)
        return self._parse(r)

    def cancel_order(self, account_id_key: str, order_id: int) -> dict:
        """PUT /v1/accounts/{accountIdKey}/orders/cancel"""
        xml = f"<CancelOrderRequest><orderId>{order_id}</orderId></CancelOrderRequest>"
        return self._parse(self._put(
            f"/v1/accounts/{account_id_key}/orders/cancel",
            data=xml, headers=self._xml_headers(),
        ))

    def change_order_preview(self, account_id_key: str, order_id: int, **fields) -> dict:
        """PUT /v1/accounts/{accountIdKey}/orders/{orderId}/change/preview"""
        return self._parse(self._put(
            f"/v1/accounts/{account_id_key}/orders/{order_id}/change/preview",
            data=self._order_xml("PreviewOrderRequest", fields),
            headers=self._xml_headers(),
        ))

    def change_order_place(self, account_id_key: str, order_id: int, **fields) -> dict:
        """PUT /v1/accounts/{accountIdKey}/orders/{orderId}/change/place"""
        return self._parse(self._put(
            f"/v1/accounts/{account_id_key}/orders/{order_id}/change/place",
            data=self._order_xml("PlaceOrderRequest", fields),
            headers=self._xml_headers(),
        ))

    # ── Alerts ────────────────────────────────────────────────────────────────

    def list_alerts(self, count: int = 25,
                    category: Optional[str] = None,
                    status: Optional[str] = None,
                    direction: Optional[str] = None,
                    search: Optional[str] = None) -> dict:
        """GET /v1/user/alerts"""
        return self._parse(self._get(
            "/v1/user/alerts",
            params={"count": count, "category": category,
                    "status": status, "direction": direction, "search": search},
        ))

    def get_alert(self, alert_id: int) -> dict:
        """GET /v1/user/alerts/{id}"""
        return self._parse(self._get(f"/v1/user/alerts/{alert_id}"))

    def delete_alerts(self, alert_ids: list[int]) -> dict:
        """DELETE /v1/user/alerts/{id,id,...}"""
        ids = ",".join(str(i) for i in alert_ids)
        return self._parse(self._delete(f"/v1/user/alerts/{ids}"))

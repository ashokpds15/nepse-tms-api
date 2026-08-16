"""
REST client for NEPSE TMS broker portals.

The TMS web portal is an Angular app over a plain JSON API at ``/tmsapi``.
Talking to that API directly is faster and far more reliable than driving the
DOM: there is no autocomplete to bind the wrong scrip, no toggle that can stay
on the wrong side, and no form field that clears itself while being filled.

AUTHENTICATION
--------------
Each of these cost real debugging time. Do not "simplify" them away.

* **The password is base64, not a hash.** The portal sends ``btoa(password)``.
  All six fields must be present on ``/authenticate``, including empty ``jwt``
  and ``otp``, or the answer is a bare 400 "Invalid Request".

* **Host-Session-Id is base64-encoded twice.**

      inner  = base64(str(sessionId)) + "-" + suid
      header = base64(inner)

  The inner string is what the portal keeps in ``localStorage`` under ``suid``.
  Sending it raw returns ``500 {"level":"OAUTH","message":""}`` on *every*
  endpoint — which reads like a server fault but is really a parse failure, and
  is the single hardest thing to guess about this API.

* **Access tokens expire within minutes.** A 401 with ``level: OAUTH`` is
  routine and means "refresh, then retry once". :meth:`TmsClient.request`
  handles it, mirroring the portal's own interceptor.

* **Auth is cookie based** (``_rid``, ``_aid``, ``XSRF-TOKEN``). There is no
  bearer token: the portal's ``id_token`` is literally the string "undefined".
"""

from __future__ import annotations

import base64
import json
import logging
import math
import uuid
from pathlib import Path
from typing import Any, Iterable, Sequence

import httpx

from nepse_tms.errors import (
    NotAuthenticated,
    OrderRejected,
    SecurityNotFound,
    StalePrice,
    TmsApiError,
)
from nepse_tms.models import (
    Account,
    Holding,
    Order,
    OrderResult,
    PriceBand,
    Security,
    Side,
)

logger = logging.getLogger("nepse_tms")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

#: Fallback band percentage, matching the portal's own default when a security
#: has no per-security record.
DEFAULT_BAND_PERCENT = 2.0


def _host_session(session_id: Any, suid: Any) -> str:
    """Build the Host-Session-Id header. See the module docstring."""
    inner = base64.b64encode(str(session_id).encode()).decode() + "-" + str(suid)
    return base64.b64encode(inner.encode()).decode()


class TmsClient:
    """
    An authenticated conversation with one TMS broker portal.

        >>> client = TmsClient("https://tms35.nepsetms.com.np")
        >>> client.login("USERNAME", "PASSWORD")     # solves the captcha
        >>> client.holdings()
        [Holding(symbol='CIT', total_quantity=399.0, ...), ...]

    Not thread-safe: give each thread its own client, or guard it with a lock.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 40.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base = base_url.rstrip("/")
        self.http = httpx.Client(
            timeout=timeout, follow_redirects=True, transport=transport
        )
        self.client_id: int | None = None
        self.user_id: int | None = None
        self.member_code: str | None = None
        #: The client profile, echoed back verbatim inside every order.
        self.client_block: dict[str, Any] | None = None
        self._host_session_id: str | None = None
        self._securities: list[Security] | None = None

    # ------------------------------------------------------------- internals

    @property
    def _xsrf(self) -> str:
        """
        Most recently set XSRF cookie.

        The portal sets this on several paths, so ``cookies.get()`` would raise
        ``CookieConflict``; the last one set is the live one.
        """
        values = [c.value for c in self.http.cookies.jar if c.name == "XSRF-TOKEN"]
        return values[-1] if values else ""

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US",
            "Content-Type": "application/json",
            "Referer": f"{self.base}/tms",
            "X-XSRF-TOKEN": self._xsrf,
        }
        if self.member_code:
            headers["MemberCode"] = str(self.member_code)
        if self.user_id:
            headers["Request-Owner"] = str(self.user_id)
        if self._host_session_id:
            headers["Host-Session-Id"] = self._host_session_id
        if extra:
            headers.update(extra)
        return headers

    @staticmethod
    def _token_expired(response: httpx.Response) -> bool:
        if response.status_code not in (401, 500):
            return False
        try:
            return str(response.json().get("level", "")).upper() == "OAUTH"
        except Exception:
            return False

    def refresh(self) -> bool:
        """Swap the refresh cookie for a fresh access token."""
        response = self.http.post(
            f"{self.base}/tmsapi/authApi/authenticate/refresh", headers=self._headers()
        )
        if response.status_code != 200:
            # A refresh straight after login hits 403 TOKEN_NOT_EXPIRED, which
            # means the current token is still good — not a failure.
            if "TOKEN_NOT_EXPIRED" in response.text:
                return True
            logger.warning(
                "token refresh failed: %s %s", response.status_code, response.text[:200]
            )
            return False
        data = response.json().get("data") or {}
        if "sessionId" in data and "suid" in data:
            self._host_session_id = _host_session(data["sessionId"], data["suid"])
        return True

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """
        Send a request, refreshing the token once if it has expired.

        Retrying exactly once is deliberate: an unbounded retry loop around an
        order endpoint could submit the same order repeatedly.
        """
        url = f"{self.base}{path}"
        response = self.http.request(method, url, headers=self._headers(), **kwargs)
        if self._token_expired(response):
            logger.debug("access token expired on %s, refreshing", path)
            if not self.refresh():
                raise NotAuthenticated(
                    "session expired and could not be refreshed; log in again"
                )
            response = self.http.request(
                method, url, headers=self._headers(), **kwargs
            )
        return response

    def get_json(self, path: str) -> Any:
        response = self.request("GET", path)
        if response.status_code != 200:
            raise TmsApiError(
                f"GET {path} -> {response.status_code}: {response.text[:300]}"
            )
        return response.json()

    # ----------------------------------------------------------------- login

    def captcha(self) -> tuple[str, bytes]:
        """Return ``(captcha_id, png_bytes)`` for solving."""
        response = self.http.get(
            f"{self.base}/tmsapi/authApi/captcha/id", headers=self._headers()
        )
        try:
            captcha_id = response.json()["id"]
        except Exception:
            # A wrong base_url serves the SPA's HTML shell with a 200, so the
            # bare JSON error would blame the parser instead of the URL.
            hint = ""
            if "html" in response.headers.get("content-type", ""):
                hint = (
                    f" — {self.base!r} returned an HTML page rather than JSON. "
                    "base_url should be just the scheme and host, e.g. "
                    "'https://tms35.nepsetms.com.np'."
                )
            raise TmsApiError(
                f"could not read a captcha id ({response.status_code}){hint}"
            ) from None
        png = self.http.get(
            f"{self.base}/tmsapi/authApi/captcha/image/{captcha_id}",
            headers=self._headers(),
        ).content
        return captcha_id, png

    def authenticate(
        self, username: str, password: str, captcha_id: str, captcha_text: str
    ) -> dict[str, Any]:
        """
        Submit one login attempt with an already-solved captcha.

        Most callers want :meth:`login`, which solves the captcha and retries.
        """
        response = self.http.post(
            f"{self.base}/tmsapi/authApi/authenticate",
            headers=self._headers(),
            json={
                "userName": username,
                # base64, NOT a hash — mirrors the portal's btoa(password).
                "password": base64.b64encode(password.encode()).decode(),
                "jwt": "",
                "otp": "",
                "captchaIdentifier": captcha_id,
                "userCaptcha": captcha_text,
            },
        )
        try:
            body = response.json()
        except Exception:
            raise NotAuthenticated(
                f"login returned unparseable response: {response.text[:200]}"
            ) from None

        if response.status_code != 200:
            raise NotAuthenticated(
                f"login failed ({body.get('status')}): {body.get('message')}",
                code=str(body.get("status")),
            )

        data = body["data"]
        member = data["clientDealerMember"]
        self.client_block = member["client"]
        self.client_id = member["client"]["id"]
        self.user_id = data["user"]["id"]
        self.member_code = member["member"]["memberCode"]
        # The response carries no suid, so mint one exactly as the portal does:
        # localStorage.suid = btoa(sessionId) + "-" + uuid4()
        self._host_session_id = _host_session(data["sessionId"], uuid.uuid4())
        self.refresh()
        return data

    def login(
        self,
        username: str,
        password: str,
        *,
        attempts: int = 6,
        solver: Any | None = None,
        min_margin: float = 12,
    ) -> Account:
        """
        Log in, solving the captcha locally.

        Needs the optional captcha extra::

            pip install "nepse-tms-api[captcha]"

        Every attempt fetches a *fresh* captcha, because they are single use —
        resubmitting the same one after a rejection fails identically.

        `min_margin` is stricter than the solver's own default on purpose. A
        captcha fetch is free, but a wrong submission is a rejected login
        against an account whose failed attempts the portal counts, so this
        prefers fetching another over guessing.
        """
        if solver is None:
            try:
                from tms_captcha import Solver
            except ImportError as exc:
                raise NotAuthenticated(
                    "captcha solving needs the optional dependency: "
                    'pip install "nepse-tms-api[captcha]" — or pass a solver, '
                    "or call authenticate() with a captcha you solved yourself."
                ) from exc
            solver = Solver(min_margin=min_margin)

        unreadable = rejected = 0
        last = "no attempt made"
        for _ in range(attempts):
            captcha_id, png = self.captcha()
            result = solver.solve(png)
            if not result.ok:
                unreadable += 1
                last = f"captcha unreadable ({result.status.value})"
                continue
            try:
                self.authenticate(username, password, captcha_id, result.value)
                return self.account()
            except NotAuthenticated as exc:
                # Only a wrong captcha is worth retrying; bad credentials or a
                # disabled account must surface immediately.
                if not exc.retryable:
                    raise
                rejected += 1
                last = str(exc)

        raise NotAuthenticated(
            f"login failed after {attempts} attempts "
            f"({unreadable} unreadable, {rejected} rejected). Last: {last}"
        )

    @property
    def authenticated(self) -> bool:
        """Cheap probe that also refreshes an expired token."""
        if not self.client_id:
            return False
        try:
            self.collateral()
            return True
        except Exception:
            return False

    # ------------------------------------------------------------ read paths

    def account(self) -> Account:
        """Identity plus current collateral."""
        if not self.client_id:
            raise NotAuthenticated("not logged in")
        limits: dict[str, Any] = {}
        try:
            limits = self.collateral()
        except TmsApiError:
            pass
        return Account(
            client_id=self.client_id,
            user_id=self.user_id or 0,
            member_code=self.member_code or "",
            display_name=(self.client_block or {}).get("displayName"),
            collateral=limits.get("collateralAmount"),
            utilized_collateral=limits.get("utilizedcollateralAmount"),
            raw=self.client_block or {},
        )

    def collateral(self) -> dict[str, Any]:
        """Trading limit: total, utilized, and multiplication factor."""
        return self.get_json(f"/tmsapi/dashboard/client/collateral/{self.client_id}")

    def dashboard(self) -> dict[str, Any]:
        """Balance summary and depository totals."""
        return self.get_json(f"/tmsapi/dashboard/client/{self.client_id}")

    def holdings(self, *, held_only: bool = True) -> list[Holding]:
        """
        Depository holdings.

        `held_only` drops the zero-quantity rows the portal returns for scrips
        that have been fully sold.
        """
        rows = self.get_json(f"/tmsapi/dp-holding/{self.client_id}")
        holdings = [Holding.from_api(row) for row in rows]
        if held_only:
            holdings = [h for h in holdings if h.total_quantity > 0]
        return sorted(holdings, key=lambda h: -h.total_quantity)

    def free_quantity(self, symbol: str) -> float:
        """Shares of `symbol` currently available to sell."""
        symbol = symbol.strip().upper()
        for holding in self.holdings():
            if holding.symbol.upper() == symbol:
                return holding.free_quantity
        return 0.0

    def order_book(self, symbol: str | None = None) -> list[Order]:
        """Live order book, most recent first."""
        rows = self.get_json(f"/tmsapi/orderApi/orderbook-v2/client/{self.client_id}")
        orders = [Order.from_api(row) for row in rows]
        if symbol:
            want = symbol.strip().upper()
            orders = [o for o in orders if o.symbol.upper() == want]
        return orders

    def securities(self, *, refresh: bool = False) -> list[Security]:
        """
        The tradable securities master, cached per client.

        Roughly 500 entries and stable within a session, so it is fetched once
        unless `refresh` is set.
        """
        if self._securities is None or refresh:
            rows = self.get_json("/tmsapi/orderApi/stock/securities")
            self._securities = [Security.from_api(row) for row in rows]
        return self._securities

    def security(self, symbol: str) -> Security:
        """
        Resolve a scrip by *exact* symbol.

        Exact matching, never prefix or fuzzy: a lookup that tolerates near
        misses is how an order for one company ends up placed on another.
        """
        want = symbol.strip().upper()
        for candidate in self.securities():
            if candidate.symbol.upper() == want:
                return candidate
        raise SecurityNotFound(f"{symbol!r} is not in the tradable securities list")

    def ltp(self, security: Security | str) -> float:
        """Last traded price."""
        sec = self.security(security) if isinstance(security, str) else security
        payload = self.get_json(
            f"/tmsapi/orderApi/stock/validation/ltp/"
            f"{sec.id}/{sec.exchange_security_id}"
        )
        return float(payload["data"])

    def price_band(self, security: Security | str) -> PriceBand:
        """
        The price range order entry will accept right now.

        LTP +/- the security's band percentage, clamped to the day's DPR range.
        Read this immediately before placing: the band re-centres as the price
        moves, so a value from a minute ago may already be wrong.
        """
        sec = self.security(security) if isinstance(security, str) else security
        ltp = self.ltp(sec)

        # Outside trading hours the LTP endpoint returns the security's
        # networthBasePrice instead of a real traded price — 100.0 for a scrip
        # that trades near 1700. The giveaway is that it sits outside the day's
        # own DPR range, which a genuine last-traded price never can.
        if sec.dpr_low and sec.dpr_high and not (sec.dpr_low <= ltp <= sec.dpr_high):
            raise StalePrice(
                f"{sec.symbol}: last traded price {ltp} is outside the day's "
                f"range {sec.dpr_low}-{sec.dpr_high}, so it is a placeholder "
                "rather than a real price (the market is most likely closed). "
                "No usable price band can be derived.",
                ltp=ltp,
            )

        try:
            percent = float(
                self.get_json(
                    f"/tmsapi/orderApi/stock/validation/stp/{sec.isin}/LTPCF"
                )["value"]
            )
        except Exception:
            percent = DEFAULT_BAND_PERCENT
        return PriceBand(
            ltp=ltp,
            low=max(ltp * (100 - percent) / 100, sec.dpr_low),
            high=min(ltp * (100 + percent) / 100, sec.dpr_high),
            percent=percent,
            tick_size=sec.tick_size,
        )

    # ------------------------------------------------------------ order entry

    def place_order(
        self,
        side: Side | str,
        symbol: str,
        quantity: int,
        price: float,
        *,
        dry_run: bool = False,
        allow_crossing: bool = True,
        check_band: bool = True,
    ) -> OrderResult:
        """
        Place a limit order.

        Everything that decides what gets traded is checked against data read
        back from the portal in this same call: the scrip comes from the
        securities master, the price from the live band, and a sell is sized
        against free depository quantity.

        Args:
            side: ``BUY``/``SELL`` or a :class:`Side`.
            symbol: Exact scrip symbol, e.g. ``"CIT"``.
            quantity: Number of shares (kitta).
            price: Limit price.
            dry_run: Validate and build the payload without submitting.
            allow_crossing: When False, refuse an order that could execute
                immediately (a buy at/above LTP, a sell at/below it). Use for
                orders that are meant to rest.
            check_band: When False, skip the band check and let the portal
                decide. Only useful if the band endpoint is unavailable.

        Raises:
            OrderRejected: the request failed validation; nothing was sent.
        """
        side = Side.parse(side)
        if quantity <= 0:
            raise OrderRejected("quantity must be positive", ["quantity <= 0"])

        sec = self.security(symbol)
        band = self.price_band(sec) if check_band else None

        problems: list[str] = []
        if band is not None:
            if not band.on_tick(price):
                problems.append(f"price {price} is not on the {sec.tick_size} tick")
            if not band.contains(price):
                problems.append(
                    f"price {price} is outside the accepted band {band}; "
                    "the portal would reject it"
                )
        if sec.board_lot and quantity % sec.board_lot:
            problems.append(f"quantity must be a multiple of {sec.board_lot}")
        if sec.max_quantity and quantity > sec.max_quantity:
            problems.append(f"quantity exceeds the maximum {sec.max_quantity}")

        if side is Side.SELL:
            free = self.free_quantity(sec.symbol)
            if quantity > free:
                problems.append(
                    f"only {free:.0f} free {sec.symbol} available to sell"
                )

        if band is not None and not allow_crossing:
            crosses = (
                price >= band.ltp if side is Side.BUY else price <= band.ltp
            )
            if crosses:
                problems.append(
                    f"a {side} at {price} crosses the market (LTP {band.ltp}) "
                    "and could execute immediately"
                )

        if problems:
            raise OrderRejected(
                f"{side} {quantity} {sec.symbol} @ {price} rejected: "
                + "; ".join(problems),
                problems,
            )

        payload = self.build_order(sec, side, quantity, price)

        if dry_run:
            return OrderResult(
                submitted=False, side=side, symbol=sec.symbol, quantity=quantity,
                price=price, band=band, dry_run=True,
                message="validated; not submitted", payload=payload,
            )

        response = self.request("POST", "/tmsapi/orderApi/order/", json=payload)
        try:
            body = response.json()
            ok = response.status_code == 200 and str(body.get("status")) == "200"
            message = body.get("message")
        except Exception:
            ok, message = False, response.text[:300]

        return OrderResult(
            submitted=ok, side=side, symbol=sec.symbol, quantity=quantity,
            price=price, band=band, message=message, payload=payload,
        )

    def build_order(
        self, security: Security, side: Side, quantity: int, price: float
    ) -> dict[str, Any]:
        """
        Build the order DTO.

        The shape is taken from a real portal submission. The client block is
        echoed back exactly as the login returned it — the server round-trips
        it and rejects anything it does not recognise.
        """
        if not self.client_block:
            raise NotAuthenticated("no client profile on this session; log in first")
        return {
            "orderBook": {
                "orderBookExtensions": [
                    {
                        "orderTypes": {"id": 1, "orderTypeCode": "LMT"},
                        "disclosedQuantity": 0,
                        "orderValidity": {"id": 1, "orderValidityCode": "DAY"},
                        "triggerPrice": 0,
                        "orderPrice": price,
                        "orderQuantity": quantity,
                        "remainingOrderQuantity": quantity,
                        "marketType": {"id": 2, "marketType": "Continuous"},
                    }
                ],
                "exchange": {"id": 1},
                "dnaConnection": {},
                "dealer": {},
                "member": {},
                "productType": {"id": 1, "productCode": "CNC"},
                "instrumentType": {"id": 1, "code": "EQ"},
                "client": self.client_block,
                "security": {
                    "id": security.id,
                    "exchangeSecurityId": security.exchange_security_id,
                    "marketProtectionPercentage": security.raw.get(
                        "marketProtectionPercentage", 0
                    ),
                    "divisor": security.raw.get("divisor", 100),
                    "boardLotQuantity": security.board_lot,
                    "tickSize": security.tick_size,
                },
                "accountType": 1,
                "cpMemberId": 0,
                "buyOrSell": int(side),
            },
            "orderPlacedBy": 2,       # 2 = placed by the client themselves
            "exchangeOrderId": None,  # None = new order; set = modification
        }

    def cancel_order(self, exchange_order_id: str) -> bool:
        """Cancel a resting order by its exchange order id."""
        response = self.request(
            "POST",
            "/tmsapi/orderApi/order/cancel/",
            json={
                "orderBook": None,
                "orderPlacedBy": 2,
                "exchangeOrderId": exchange_order_id,
            },
        )
        return response.status_code == 200

    # ----------------------------------------------------------- persistence

    def save(self, path: str | Path) -> None:
        """
        Write the session to disk so another process can resume it.

        The file contains live session cookies — treat it like a credential and
        keep it out of version control.
        """
        Path(path).write_text(
            json.dumps(
                {
                    "base_url": self.base,
                    "cookies": {c.name: c.value for c in self.http.cookies.jar},
                    "client_id": self.client_id,
                    "user_id": self.user_id,
                    "member_code": self.member_code,
                    "host_session_id": self._host_session_id,
                    "client_block": self.client_block,
                },
                indent=1,
            )
        )

    @classmethod
    def restore(
        cls, path: str | Path, base_url: str | None = None
    ) -> "TmsClient | None":
        """
        Rebuild a client from :meth:`save`, or None if there is nothing usable.

        The restored session may still be expired; check :attr:`authenticated`.
        """
        path = Path(path)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

        client = cls(base_url or data.get("base_url", ""))
        host = httpx.URL(client.base).host
        domain = f".{host}" if host else ""
        for name, value in (data.get("cookies") or {}).items():
            client.http.cookies.set(name, value, domain=domain, path="/")
        client.client_id = data.get("client_id")
        client.user_id = data.get("user_id")
        client.member_code = data.get("member_code")
        client._host_session_id = data.get("host_session_id")
        client.client_block = data.get("client_block")
        return client

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> "TmsClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

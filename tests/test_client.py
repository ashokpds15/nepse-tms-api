"""
Offline tests against a simulated TMS portal.

No credentials and no network: httpx.MockTransport stands in for the portal, so
the parts that are genuinely tricky — the double-base64 session header, the
refresh-once-then-retry loop, and the order validation that stands between a
typo and a real trade — are covered on every run.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from nepse_tms import (
    NotAuthenticated,
    Order,
    OrderRejected,
    PriceBand,
    SecurityNotFound,
    Side,
    TmsClient,
)

BASE = "https://tms35.nepsetms.com.np"

CLIENT_BLOCK = {
    "id": 2157190,
    "clientMemberCode": "20210306395",
    "notsUniqueClientCode": "202103212103545",
    "displayName": "TEST CLIENT",
    "activeStatus": "A",
}

SECURITY = {
    "id": 210,
    "symbol": "CIT",
    "exchangeSecurityId": 210,
    "isin": "NPE119A00005",
    "tickSize": 0.1,
    "boardLotQuantity": 1,
    "maxAllowedQuantity": 100000,
    "dprRangeLow": 1447.55,
    "dprRangeHigh": 1958.45,
    "securityName": "Citizen Investment Trust",
    "marketProtectionPercentage": 0,
    "divisor": 100,
}

HOLDINGS = [
    {"scripCode": "CIT", "securityName": "Citizen Investment Trust",
     "totalQuantity": 399.0, "freeQuantity": 399.0, "boid": "1301370003090306"},
    {"scripCode": "SOLD", "securityName": "Fully Sold Ltd",
     "totalQuantity": 0.0, "freeQuantity": 0.0, "boid": "1301370003090306"},
]

LTP = 1700.0


class FakePortal:
    """A minimal stand-in for the parts of TMS this client touches."""

    def __init__(self, *, expire_first: bool = False, captcha_failures: int = 0):
        self.orders: list[dict] = []
        self.requests: list[httpx.Request] = []
        self.expire_first = expire_first
        self.captcha_failures = captcha_failures
        self.refreshes = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path

        if path == "/tmsapi/authApi/captcha/id":
            return httpx.Response(200, json={"id": "captcha-1"})

        if path.startswith("/tmsapi/authApi/captcha/image/"):
            return httpx.Response(200, content=b"\x89PNG fake")

        if path == "/tmsapi/authApi/authenticate":
            body = json.loads(request.content)
            if self.captcha_failures > 0:
                self.captcha_failures -= 1
                return httpx.Response(
                    401, json={"status": "108", "message": "Wrong Captcha"}
                )
            if body["userName"] != "USER":
                return httpx.Response(
                    401, json={"status": "103", "message": "Credentials Not Found"}
                )
            return httpx.Response(
                200,
                json={
                    "status": "202",
                    "message": "userDetails",
                    "data": {
                        "sessionId": 12,
                        "user": {"id": 50660},
                        "clientDealerMember": {
                            "client": CLIENT_BLOCK,
                            "member": {"memberCode": "35"},
                        },
                    },
                },
                headers={"set-cookie": "XSRF-TOKEN=token-1; Path=/"},
            )

        if path == "/tmsapi/authApi/authenticate/refresh":
            self.refreshes += 1
            return httpx.Response(
                200,
                json={"status": "200", "data": {"sessionId": 24, "suid": "abc-def"}},
            )

        # Everything past here needs the session header.
        if not request.headers.get("Host-Session-Id"):
            return httpx.Response(500, json={"status": "500", "level": "OAUTH",
                                            "message": ""})

        if self.expire_first:
            self.expire_first = False
            return httpx.Response(
                401,
                json={"status": "401", "level": "OAUTH",
                      "message": "ACCESS_TOKEN_EXPIRED"},
            )

        if path.startswith("/tmsapi/dashboard/client/collateral/"):
            return httpx.Response(200, json={
                "collateralAmount": 200000.0,
                "utilizedcollateralAmount": 0.0,
                "collateralMultiplicationFactor": 1.0,
            })

        if path.startswith("/tmsapi/dp-holding/"):
            return httpx.Response(200, json=HOLDINGS)

        if path == "/tmsapi/orderApi/stock/securities":
            return httpx.Response(200, json=[SECURITY])

        if path.startswith("/tmsapi/orderApi/stock/validation/ltp/"):
            return httpx.Response(200, json={"status": "200", "data": LTP})

        if path.startswith("/tmsapi/orderApi/stock/validation/stp/"):
            return httpx.Response(200, json={"code": "LTPCF", "value": "3"})

        if path.startswith("/tmsapi/orderApi/orderbook-v2/client/"):
            return httpx.Response(200, json=[
                {"exchangeOrderId": "2026081305025114", "symbol": "CIT",
                 "buyOrSell": 1, "orderPrice": 1659.2, "orderQuantity": 10,
                 "totalTradedQuantity": 0, "remainingOrderQuantity": 10,
                 "activeStatus": "OPEN", "orderTime": "2026-08-13 14:45:46"},
            ])

        if path == "/tmsapi/orderApi/order/":
            self.orders.append(json.loads(request.content))
            return httpx.Response(200, json={"status": "200",
                                             "message": "ORDER PLACEMENT SUCCESS"})

        if path == "/tmsapi/orderApi/order/cancel/":
            return httpx.Response(200, json={"status": "200"})

        return httpx.Response(404, json={"status": "404"})


class FakeSolver:
    """Stands in for tms_captcha.Solver."""

    class _Result:
        def __init__(self, value: str, ok: bool = True):
            self.value, self.ok = value, ok
            self.status = type("S", (), {"value": "success" if ok else "low_confidence"})()

    def __init__(self, value: str = "abc123", ok: bool = True):
        self._value, self._ok = value, ok
        self.calls = 0

    def solve(self, image):
        self.calls += 1
        return self._Result(self._value, self._ok)


@pytest.fixture
def portal() -> FakePortal:
    return FakePortal()


@pytest.fixture
def client(portal: FakePortal) -> TmsClient:
    c = TmsClient(BASE, transport=portal.transport())
    c.login("USER", "PASS", solver=FakeSolver())
    return c


# --------------------------------------------------------------------- auth


def test_login_solves_captcha_and_populates_identity(portal: FakePortal):
    client = TmsClient(BASE, transport=portal.transport())
    account = client.login("USER", "PASS", solver=FakeSolver())

    assert client.client_id == 2157190
    assert client.user_id == 50660
    assert client.member_code == "35"
    assert account.collateral == 200000.0
    assert account.available_collateral == 200000.0


def test_password_is_sent_base64_not_plaintext(portal: FakePortal):
    client = TmsClient(BASE, transport=portal.transport())
    client.login("USER", "hunter2", solver=FakeSolver())

    auth = next(r for r in portal.requests
                if r.url.path == "/tmsapi/authApi/authenticate")
    body = json.loads(auth.content)
    assert body["password"] == base64.b64encode(b"hunter2").decode()
    assert body["password"] != "hunter2"
    # All six fields must be present or the portal answers a bare 400.
    assert set(body) == {"userName", "password", "jwt", "otp",
                         "captchaIdentifier", "userCaptcha"}


def test_host_session_id_is_double_base64_encoded(client: TmsClient, portal: FakePortal):
    """
    The header is base64(base64(sessionId) + "-" + suid).

    Sending the inner string raw returns 500 OAUTH on every endpoint, so this
    encoding is the difference between a working client and a dead one.
    """
    client.collateral()
    request = portal.requests[-1]
    header = request.headers["Host-Session-Id"]

    inner = base64.b64decode(header).decode()
    session_part, _, suid = inner.partition("-")
    assert base64.b64decode(session_part).decode() == "24"   # from refresh
    assert suid == "abc-def"


def test_login_retries_only_on_wrong_captcha():
    portal = FakePortal(captcha_failures=2)
    client = TmsClient(BASE, transport=portal.transport())
    solver = FakeSolver()

    client.login("USER", "PASS", solver=solver)

    assert solver.calls == 3          # two rejected, third accepted
    assert client.client_id == 2157190


def test_login_gives_up_on_bad_credentials_without_retrying():
    portal = FakePortal()
    client = TmsClient(BASE, transport=portal.transport())
    solver = FakeSolver()

    with pytest.raises(NotAuthenticated) as excinfo:
        client.login("WRONG", "PASS", solver=solver)

    assert excinfo.value.code == "103"
    assert not excinfo.value.retryable
    assert solver.calls == 1, "must not burn attempts on a credential failure"


def test_login_reports_unreadable_captchas():
    portal = FakePortal()
    client = TmsClient(BASE, transport=portal.transport())

    with pytest.raises(NotAuthenticated, match="unreadable"):
        client.login("USER", "PASS", attempts=3, solver=FakeSolver(ok=False))


def test_expired_token_is_refreshed_and_the_call_retried():
    portal = FakePortal()
    client = TmsClient(BASE, transport=portal.transport())
    client.login("USER", "PASS", solver=FakeSolver())

    before = portal.refreshes
    portal.expire_first = True
    assert client.collateral()["collateralAmount"] == 200000.0
    assert portal.refreshes == before + 1


# ------------------------------------------------------------------- reads


def test_holdings_hides_zero_rows_by_default(client: TmsClient):
    symbols = [h.symbol for h in client.holdings()]
    assert symbols == ["CIT"]
    assert "SOLD" in [h.symbol for h in client.holdings(held_only=False)]


def test_free_quantity(client: TmsClient):
    assert client.free_quantity("cit") == 399.0
    assert client.free_quantity("NOTHELD") == 0.0


def test_order_book_parses_rows(client: TmsClient):
    orders = client.order_book()
    assert len(orders) == 1
    order = orders[0]
    assert isinstance(order, Order)
    assert order.side is Side.BUY
    assert order.is_open and not order.is_filled
    assert order.exchange_order_id == "2026081305025114"


def test_security_lookup_is_exact(client: TmsClient):
    assert client.security("CIT").id == 210
    assert client.security("cit").id == 210
    with pytest.raises(SecurityNotFound):
        client.security("CI")          # a prefix must not match
    with pytest.raises(SecurityNotFound):
        client.security("NOTREAL")


def test_securities_are_cached(client: TmsClient, portal: FakePortal):
    client.securities()
    calls = sum(r.url.path == "/tmsapi/orderApi/stock/securities"
                for r in portal.requests)
    client.securities()
    assert sum(r.url.path == "/tmsapi/orderApi/stock/securities"
               for r in portal.requests) == calls


def test_price_band(client: TmsClient):
    band = client.price_band("CIT")
    assert band.ltp == LTP
    assert band.low == pytest.approx(1649.0)
    assert band.high == pytest.approx(1751.0)
    assert band.contains(1700.0) and not band.contains(1800.0)
    assert band.on_tick(1659.2) and not band.on_tick(1659.23)


def test_price_band_clamp_stays_inside():
    band = PriceBand(ltp=1700.0, low=1649.0, high=1751.0, percent=3, tick_size=0.1)
    assert band.contains(band.clamp(9999))
    assert band.contains(band.clamp(1))
    assert band.clamp(1700.04) == pytest.approx(1700.0)


# ------------------------------------------------------------ order entry


def test_place_order_submits_expected_payload(client: TmsClient, portal: FakePortal):
    result = client.place_order("BUY", "CIT", 10, 1659.2)

    assert result.submitted and bool(result) is True
    assert result.message == "ORDER PLACEMENT SUCCESS"

    sent = portal.orders[-1]
    book = sent["orderBook"]
    assert book["buyOrSell"] == 1
    assert book["security"]["id"] == 210
    assert book["client"] == CLIENT_BLOCK
    assert book["orderBookExtensions"][0]["orderPrice"] == 1659.2
    assert book["orderBookExtensions"][0]["orderQuantity"] == 10
    assert sent["exchangeOrderId"] is None, "must be a new order, not a modify"


def test_sell_uses_side_code_two(client: TmsClient, portal: FakePortal):
    client.place_order("SELL", "CIT", 10, 1750.0)
    assert portal.orders[-1]["orderBook"]["buyOrSell"] == 2


def test_dry_run_sends_nothing(client: TmsClient, portal: FakePortal):
    result = client.place_order("BUY", "CIT", 10, 1659.2, dry_run=True)
    assert result.dry_run and not result.submitted
    assert result.payload["orderBook"]["buyOrSell"] == 1
    assert portal.orders == []


def test_rejects_price_outside_band(client: TmsClient, portal: FakePortal):
    with pytest.raises(OrderRejected, match="outside the accepted band"):
        client.place_order("BUY", "CIT", 10, 999999.0)
    assert portal.orders == []


def test_rejects_off_tick_price(client: TmsClient, portal: FakePortal):
    with pytest.raises(OrderRejected, match="tick"):
        client.place_order("BUY", "CIT", 10, 1700.03)
    assert portal.orders == []


def test_rejects_selling_more_than_held(client: TmsClient, portal: FakePortal):
    with pytest.raises(OrderRejected, match="only 399"):
        client.place_order("SELL", "CIT", 500, 1750.0)
    assert portal.orders == []


def test_rejects_non_positive_quantity(client: TmsClient):
    with pytest.raises(OrderRejected):
        client.place_order("BUY", "CIT", 0, 1700.0)


def test_allow_crossing_false_blocks_marketable_orders(client: TmsClient):
    with pytest.raises(OrderRejected, match="crosses the market"):
        client.place_order("BUY", "CIT", 10, 1740.0, allow_crossing=False)
    with pytest.raises(OrderRejected, match="crosses the market"):
        client.place_order("SELL", "CIT", 10, 1660.0, allow_crossing=False)


def test_allow_crossing_false_permits_resting_orders(client: TmsClient):
    assert client.place_order("BUY", "CIT", 10, 1650.0, allow_crossing=False)
    assert client.place_order("SELL", "CIT", 10, 1750.0, allow_crossing=False)


def test_rejection_lists_every_problem_at_once(client: TmsClient):
    with pytest.raises(OrderRejected) as excinfo:
        client.place_order("SELL", "CIT", 500, 999999.0)
    assert len(excinfo.value.problems) >= 2


def test_cancel_order(client: TmsClient):
    assert client.cancel_order("2026081305025114") is True


# ------------------------------------------------------------------- misc


def test_side_parsing():
    assert Side.parse("buy") is Side.BUY
    assert Side.parse("SELL") is Side.SELL
    assert Side.parse(1) is Side.BUY
    assert Side.parse(Side.SELL) is Side.SELL
    with pytest.raises(ValueError):
        Side.parse("hold")


def test_save_and_restore_round_trip(client: TmsClient, tmp_path):
    path = tmp_path / "session.json"
    client.save(path)

    restored = TmsClient.restore(path)
    assert restored is not None
    assert restored.client_id == client.client_id
    assert restored.client_block == CLIENT_BLOCK
    assert restored._host_session_id == client._host_session_id


def test_restore_missing_or_corrupt_returns_none(tmp_path):
    assert TmsClient.restore(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert TmsClient.restore(bad) is None


def test_build_order_requires_login():
    client = TmsClient(BASE, transport=FakePortal().transport())
    from nepse_tms.models import Security

    with pytest.raises(NotAuthenticated):
        client.build_order(Security.from_api(SECURITY), Side.BUY, 1, 1.0)


def test_context_manager_closes(portal: FakePortal):
    with TmsClient(BASE, transport=portal.transport()) as client:
        client.login("USER", "PASS", solver=FakeSolver())
    assert client.http.is_closed


def test_missing_captcha_extra_gives_actionable_error(portal: FakePortal, monkeypatch):
    """
    Without the optional captcha dependency, login must explain how to fix it
    rather than dying on a bare ImportError.
    """
    import builtins

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "tms_captcha":
            raise ImportError("no module named tms_captcha")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    client = TmsClient(BASE, transport=portal.transport())

    with pytest.raises(NotAuthenticated, match="nepse-tms-api\\[captcha\\]"):
        client.login("USER", "PASS")


def test_wrong_base_url_explains_itself():
    """
    A base_url pointing at the SPA serves HTML with a 200. The error must name
    the real problem instead of surfacing a JSON parse failure.
    """
    def serve_html(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>...</html>",
                              headers={"content-type": "text/html"})

    from nepse_tms import TmsApiError

    client = TmsClient(BASE, transport=httpx.MockTransport(serve_html))
    with pytest.raises(TmsApiError, match="HTML page rather than JSON"):
        client.captcha()


# ------------------------------------------------- stale prices (market shut)


class ClosedMarketPortal(FakePortal):
    """
    Outside trading hours the LTP endpoint returns networthBasePrice — a
    placeholder far below the day's range, not a tradable price.
    """

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/tmsapi/orderApi/stock/validation/ltp/"):
            self.requests.append(request)
            return httpx.Response(200, json={"status": "200", "data": 100.0})
        return super().handle(request)


def test_placeholder_ltp_is_rejected_not_turned_into_a_band():
    """
    A placeholder LTP would produce an inverted band (low above high). Emitting
    that would send orders at prices the portal always rejects, so the client
    must refuse to derive a band at all.
    """
    from nepse_tms import StalePrice

    portal = ClosedMarketPortal()
    client = TmsClient(BASE, transport=portal.transport())
    client.login("USER", "PASS", solver=FakeSolver())

    with pytest.raises(StalePrice) as excinfo:
        client.price_band("CIT")
    assert excinfo.value.ltp == 100.0
    assert "market is most likely closed" in str(excinfo.value)


def test_orders_refuse_to_price_against_a_stale_market():
    from nepse_tms import StalePrice

    portal = ClosedMarketPortal()
    client = TmsClient(BASE, transport=portal.transport())
    client.login("USER", "PASS", solver=FakeSolver())

    with pytest.raises(StalePrice):
        client.place_order("BUY", "CIT", 10, 1659.2)
    assert portal.orders == [], "nothing may be sent without a verified band"


def test_check_band_false_allows_bypassing_a_dead_price_feed():
    portal = ClosedMarketPortal()
    client = TmsClient(BASE, transport=portal.transport())
    client.login("USER", "PASS", solver=FakeSolver())

    result = client.place_order("BUY", "CIT", 10, 1659.2, check_band=False)
    assert result.submitted and result.band is None


def test_invalid_band_is_inert():
    band = PriceBand(ltp=100.0, low=1456.56, high=103.0, percent=3, tick_size=0.1)
    assert not band.valid
    assert not band.contains(103.1)
    with pytest.raises(ValueError, match="impossible band"):
        band.clamp(9999)

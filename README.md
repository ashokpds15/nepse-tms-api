# nepse-tms-api

[![PyPI](https://img.shields.io/pypi/v/nepse-tms-api.svg)](https://pypi.org/project/nepse-tms-api/)
[![Python](https://img.shields.io/pypi/pyversions/nepse-tms-api.svg)](https://pypi.org/project/nepse-tms-api/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Python client for **NEPSE TMS** broker portals — the trading system Nepali brokers run at `tms{NN}.nepsetms.com.np`.

It talks to the portal's REST API directly. **No browser, no Selenium, no scraping.** Logging in takes about 200 ms, including solving the captcha locally.

```python
from nepse_tms import TmsClient

with TmsClient("https://tms35.nepsetms.com.np") as client:
    client.login("YOUR_CLIENT_CODE", "YOUR_PASSWORD")   # captcha solved offline

    for holding in client.holdings():
        print(holding.symbol, holding.free_quantity)

    band = client.price_band("CIT")
    print(f"CIT is {band.ltp}, orders accepted in {band}")

    client.place_order("BUY", "CIT", 10, band.clamp(band.low))
```

> **Unofficial.** Not affiliated with or endorsed by NEPSE or any broker. It works by speaking the same API the official web portal speaks. See [Responsible use](#responsible-use).

---

## Contents

- [Why not browser automation](#why-not-browser-automation)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Guide](#guide)
  - [Logging in](#logging-in)
  - [Reusing a session](#reusing-a-session)
  - [Reading your account](#reading-your-account)
  - [Price bands](#price-bands)
  - [Placing orders](#placing-orders)
  - [Safety rails](#safety-rails)
- [API reference](#api-reference)
- [How authentication works](#how-authentication-works)
- [Troubleshooting](#troubleshooting)
- [Responsible use](#responsible-use)

## Why not browser automation

Every other TMS automation tool drives the DOM with Selenium. That approach fails in ways that cost money:

| Browser automation | This library |
|---|---|
| Autocomplete can bind a **different scrip** than you typed | Symbol is a dictionary key from the securities master |
| A BUY/SELL toggle can silently stay on the wrong side | Side is an integer field in the request |
| Form fields clear themselves mid-fill | No form exists |
| Price rejections appear as a toast you must scrape | The accepted band is fetched and checked before sending |
| ~10 s per login, needs Chrome installed | ~0.2 s, pure HTTP |

These are not hypotheticals — they are the failure modes this library was written in response to.

## Installation

```bash
pip install "nepse-tms-api[captcha]"
```

The `captcha` extra pulls in [`nepse-tms-captcha`](https://github.com/DevAbhinav2073/nepse-tms-captcha), which solves the login captcha offline. Without it you can still log in by solving the captcha yourself — see [Logging in](#logging-in).

Requires Python 3.9+. The only hard dependency is `httpx`.

## Quick start

```python
import os
from nepse_tms import TmsClient

client = TmsClient("https://tms35.nepsetms.com.np")
account = client.login(os.environ["TMS_USERNAME"], os.environ["TMS_PASSWORD"])

print(account.display_name, account.client_id)
print("available collateral:", account.available_collateral)
```

Find your broker's URL by the broker number — broker 35 is `https://tms35.nepsetms.com.np`.

Keep credentials in the environment or a secrets manager, never in source.

## Guide

### Logging in

`login()` fetches a captcha, solves it locally, and retries with a **fresh** captcha if the portal rejects it — captchas are single use, so resubmitting the same one always fails.

```python
account = client.login(username, password, attempts=6)
```

It distinguishes retryable from fatal failures. A wrong captcha is retried; bad credentials, a disabled account, or a session-limit error raise immediately rather than burning attempts:

```python
from nepse_tms import NotAuthenticated

try:
    client.login(username, password)
except NotAuthenticated as exc:
    print(exc.code)        # '103' = bad credentials, '108' = wrong captcha
    print(exc.retryable)   # False for anything that isn't a captcha problem
```

**Solving the captcha yourself** — if you'd rather not install the extra, or you want a human in the loop:

```python
captcha_id, png = client.captcha()
open("captcha.png", "wb").write(png)
client.authenticate(username, password, captcha_id, input("captcha: "))
```

### Reusing a session

Logging in on every run is wasteful and rude to the portal. Save the session and restore it:

```python
from pathlib import Path

SESSION = Path("~/.config/nepse-tms/session.json").expanduser()

client = TmsClient.restore(SESSION) or TmsClient(BASE_URL)
if not client.authenticated:
    client.login(username, password)
    client.save(SESSION)
```

`authenticated` is a cheap probe that also refreshes an expired access token, so this pattern re-logs in only when the session is genuinely dead.

> The session file contains live cookies. Treat it like a password: keep it out of version control and restrict its permissions.

### Reading your account

```python
client.holdings()          # [Holding(symbol='CIT', total_quantity=399.0, ...)]
client.holdings(held_only=False)   # includes fully-sold, zero-quantity rows
client.free_quantity("CIT")        # 399.0 — what you can actually sell
client.order_book()                # [Order(...)]
client.order_book("CIT")           # filtered to one scrip
client.collateral()                # trading limit
client.dashboard()                 # balance summary + depository totals
```

Orders carry convenience properties:

```python
for order in client.order_book():
    if order.is_open:
        print(order.exchange_order_id, order.side, order.remaining_quantity)
```

Every model keeps the untouched response in `.raw`, so a field this library does not model yet is still reachable:

```python
client.holdings()[0].raw["cdsLastModifiedDate"]
```

### Price bands

NEPSE rejects limit prices outside a percentage band around the last traded price, clamped to the day's DPR range. **The band re-centres as the price moves**, so read it immediately before placing rather than caching it:

```python
band = client.price_band("CIT")

band.ltp            # 1700.0
band.low, band.high # 1649.0, 1751.0
band.percent        # 3.0
str(band)           # '1649.00-1751.00 (LTP 1700.0, +/-3.0%)'

band.contains(1700.0)   # True
band.on_tick(1659.23)   # False — must be a multiple of the tick size
band.clamp(9999)        # 1751.0 — nearest acceptable price, snapped to tick
```

`clamp()` is the easy way to build a valid price: it bounds to the band *and* snaps to the tick, and never returns a value that would be rejected.

**Outside trading hours there is no usable band.** The LTP endpoint falls back to the security's `networthBasePrice` — 100.0 for a scrip that trades near 1700 — which would produce an impossible band whose lower bound sits above its upper bound. Rather than hand that back, the client raises:

```python
from nepse_tms import StalePrice

try:
    band = client.price_band("CIT")
except StalePrice as exc:
    print(exc.ltp)   # 100.0 — a placeholder, not a real price
    # No usable band: the market is almost certainly closed.
```

`place_order` propagates this, so orders cannot be priced against a dead feed. If you need to submit anyway and let the portal arbitrate, pass `check_band=False`.

### Placing orders

```python
result = client.place_order("BUY", "CIT", 10, 1659.2)

if result:
    print("submitted:", result.message)
else:
    print("not submitted:", result.message)
```

`OrderResult` is falsy when nothing reached the exchange, and `submitted` is derived from the portal's own answer rather than assumed — a rejected order can never read as a live one.

**Always dry-run first when you're building something:**

```python
result = client.place_order("SELL", "CIT", 10, 1750.0, dry_run=True)
print(result.payload)     # exactly what would be sent; nothing was
```

Cancel a resting order by its exchange id:

```python
client.cancel_order("2026081305025114")
```

### Safety rails

`place_order` validates against data read back from the portal in the same call, and raises `OrderRejected` **before sending anything**:

- price outside the live accepted band
- price off the tick size
- quantity not a multiple of the board lot, or above the per-order maximum
- selling more than your free depository quantity
- unknown symbol (exact match only — a prefix will not silently resolve)

```python
from nepse_tms import OrderRejected

try:
    client.place_order("SELL", "CIT", 500, 999999.0)
except OrderRejected as exc:
    for problem in exc.problems:
        print("-", problem)
    # - price 999999.0 is outside the accepted band 1649.00-1751.00 ...
    # - only 399 free CIT available to sell
```

Every problem is reported at once, so you don't fix one and discover the next.

**Non-crossing orders.** For orders that must rest rather than execute — testing a flow, or placing a limit you don't want filled at market — `allow_crossing=False` refuses anything marketable:

```python
client.place_order("BUY",  "CIT", 10, 1650.0, allow_crossing=False)  # rests below
client.place_order("SELL", "CIT", 10, 1750.0, allow_crossing=False)  # rests above
client.place_order("BUY",  "CIT", 10, 1740.0, allow_crossing=False)  # OrderRejected
```

## API reference

### `TmsClient(base_url, *, timeout=40.0, transport=None)`

| Method | Returns | Notes |
|---|---|---|
| `login(username, password, *, attempts=6, solver=None, min_margin=12)` | `Account` | Solves the captcha; retries only on captcha failure |
| `authenticate(username, password, captcha_id, captcha_text)` | `dict` | One attempt with a captcha you solved |
| `captcha()` | `(str, bytes)` | Captcha id and PNG |
| `authenticated` | `bool` | Probe; refreshes an expired token |
| `account()` | `Account` | Identity plus collateral |
| `holdings(*, held_only=True)` | `list[Holding]` | Sorted by size |
| `free_quantity(symbol)` | `float` | Sellable quantity |
| `order_book(symbol=None)` | `list[Order]` | |
| `securities(*, refresh=False)` | `list[Security]` | Cached per client |
| `security(symbol)` | `Security` | Exact match; raises `SecurityNotFound` |
| `ltp(security)` | `float` | Last traded price |
| `price_band(security)` | `PriceBand` | Read fresh before each order; raises `StalePrice` when closed |
| `place_order(side, symbol, quantity, price, *, dry_run=False, allow_crossing=True, check_band=True)` | `OrderResult` | Raises `OrderRejected` on validation failure |
| `build_order(security, side, quantity, price)` | `dict` | The raw DTO, for inspection |
| `cancel_order(exchange_order_id)` | `bool` | |
| `save(path)` / `TmsClient.restore(path)` | | Session persistence |

### Models

`Account`, `Holding`, `Order`, `Security`, `PriceBand`, `OrderResult`, and `Side` (an `IntEnum`: `BUY = 1`, `SELL = 2`, matching the portal's encoding).

### Exceptions

All inherit from `TmsError`: `TmsApiError`, `NotAuthenticated` (with `.code` and `.retryable`), `OrderRejected` (with `.problems`), `SecurityNotFound`, `StalePrice` (with `.ltp`).

## How authentication works

Documented because it is genuinely non-obvious, and because anyone reimplementing this will otherwise lose a day to it:

1. **The password is base64, not a hash.** The portal sends `btoa(password)`. All six fields must be present on `/authenticate` — including empty `jwt` and `otp` — or the reply is a bare `400 Invalid Request`.

2. **`Host-Session-Id` is base64-encoded twice:**

   ```
   inner  = base64(str(sessionId)) + "-" + suid
   header = base64(inner)
   ```

   The inner string is what the portal stores in `localStorage` under `suid`. Sending it raw returns `500 {"level":"OAUTH","message":""}` on *every* endpoint — which looks like a server fault but is really a parse failure. This one detail is the difference between a working client and one that appears entirely broken.

3. **Access tokens expire within minutes.** A `401` with `level: OAUTH` is routine, and means "refresh, then retry once". The client does this automatically. Retrying exactly once is deliberate: an unbounded retry loop around an order endpoint could submit the same order repeatedly.

4. **Auth is cookie-based** (`_rid`, `_aid`, `XSRF-TOKEN`). There is no bearer token — the portal's own `id_token` is literally the string `"undefined"`.

## Troubleshooting

**Everything returns `500` with `level: OAUTH`** — the `Host-Session-Id` encoding is wrong, or the session is dead. Log in again.

**`NotAuthenticated` with code `403`** — the portal's concurrent-session limit. Log out of the web portal, or wait for the old session to lapse.

**Order rejected for price despite looking right** — the band moved. Re-read `price_band()` immediately before placing; a value from a minute ago can already be stale.

**`SecurityNotFound` for a symbol you can see on the portal** — the securities master only lists *tradable* scrips. Check `client.securities()`, and note matching is exact and case-insensitive but never partial.

**`StalePrice` raised for every symbol** — the market is closed. The portal serves a placeholder price outside trading hours; wait for the session to open (NEPSE trades 11:00–15:00 NPT, Sunday to Thursday).

**Orders accepted but not visible in the order book** — the book lags a moment behind acceptance. Re-read after a short pause.

## Responsible use

This is an unofficial client for **your own broker account**. It does not bypass authentication: you supply your own credentials, and it performs the same actions the web portal performs on your behalf.

Please be a good citizen of infrastructure a lot of people depend on:

- Reuse sessions instead of logging in repeatedly.
- Don't poll aggressively; the portal is not a market-data feed.
- Check your broker's and NEPSE's terms — automated access may be restricted, and that is between you and them.

**Orders placed through this library are real orders for real money.** Test with `dry_run=True`, and consider `allow_crossing=False` while you are still building. The author accepts no liability for financial loss; see the [licence](LICENSE).

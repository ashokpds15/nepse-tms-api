# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-08-16

Initial release.

### Added

- `TmsClient`: login, holdings, order book, securities master, last traded
  price, price bands, order placement and cancellation, over the portal's REST
  API — no browser required.
- Unattended login: fetches and solves the captcha locally via the optional
  `nepse-tms-captcha` extra, retrying with a fresh captcha and distinguishing a
  wrong captcha (retryable) from bad credentials (not).
- Automatic access-token refresh, retrying a request exactly once so an order
  endpoint can never be resubmitted in a loop.
- Typed models — `Account`, `Holding`, `Order`, `Security`, `PriceBand`,
  `OrderResult`, `Side` — each keeping the untouched response in `.raw`.
- Order validation that runs before anything is sent: live price band, tick
  size, board lot, per-order maximum, free depository quantity, and exact
  symbol resolution. `OrderRejected` reports every problem at once.
- `allow_crossing=False` to refuse orders that could execute immediately.
- Session persistence via `save()` / `restore()`.

### Fixed

- **Stale prices outside trading hours.** The LTP endpoint returns the
  security's `networthBasePrice` when the market is closed — 100.0 for a scrip
  trading near 1700 — which produced an inverted price band (lower bound above
  upper bound) and a `clamp()` result the portal would always reject. The
  client now detects a price outside the day's DPR range and raises
  `StalePrice` instead of deriving a band from it.

[0.1.0]: https://github.com/DevAbhinav2073/nepse-tms-api/releases/tag/v0.1.0

"""
Typed views over the portal's JSON.

Every model keeps the untouched response in `raw`, so a field this package does
not model yet is still reachable without patching the library.

Nothing here invents data. A value the portal did not send stays None rather
than being defaulted to zero, because a silent 0 in a trading context reads as
a real quantity or price.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class Side(IntEnum):
    """
    Order side, using the portal's own encoding.

    Verified against the portal bundle two independent ways:
    ``OrderTypes = [{display:"BUY", modelValue:1}, {display:"-"},
    {display:"SELL", modelValue:2}]``, and the market-maker path pairing
    ``buyOrSell = 1`` with the buy price.
    """

    BUY = 1
    SELL = 2

    @classmethod
    def parse(cls, value: "Side | str | int") -> "Side":
        if isinstance(value, cls):
            return value
        if isinstance(value, int):
            return cls(value)
        text = str(value).strip().upper()
        if text in ("BUY", "B", "1"):
            return cls.BUY
        if text in ("SELL", "S", "2"):
            return cls.SELL
        raise ValueError(f"side must be BUY or SELL, got {value!r}")

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class Security:
    """A tradable scrip from the securities master."""

    symbol: str
    id: int
    exchange_security_id: int
    isin: str
    tick_size: float
    board_lot: int
    max_quantity: int
    dpr_low: float
    dpr_high: float
    name: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Security":
        return cls(
            symbol=data["symbol"],
            id=data["id"],
            exchange_security_id=data["exchangeSecurityId"],
            isin=data.get("isin", ""),
            tick_size=data.get("tickSize", 0.1),
            board_lot=data.get("boardLotQuantity", 1),
            max_quantity=data.get("maxAllowedQuantity", 0),
            dpr_low=data.get("dprRangeLow", 0.0),
            dpr_high=data.get("dprRangeHigh", 0.0),
            name=data.get("securityName"),
            raw=data,
        )


@dataclass(frozen=True)
class PriceBand:
    """
    The price range order entry will accept right now.

    NEPSE rejects limit prices outside a percentage band around the last traded
    price, clamped to the day's DPR range. The band re-centres as the price
    moves, so it must be read fresh rather than cached.
    """

    ltp: float
    low: float
    high: float
    percent: float
    tick_size: float

    @property
    def valid(self) -> bool:
        """
        False when the bounds are contradictory.

        Happens when the portal hands back a placeholder price instead of a
        real one — see :class:`~nepse_tms.errors.StalePrice`.
        """
        return self.low <= self.high

    def contains(self, price: float) -> bool:
        return self.valid and self.low <= price <= self.high

    def on_tick(self, price: float) -> bool:
        return abs(round(price / self.tick_size) - price / self.tick_size) < 1e-9

    def clamp(self, price: float) -> float:
        """
        Nearest acceptable price: inside the band and on the tick.

        Raises ValueError on an invalid band rather than returning a number
        that would be rejected — a silently wrong price here becomes a real
        order at a real price.
        """
        if not self.valid:
            raise ValueError(
                f"cannot clamp to an impossible band {self.low:.2f}-{self.high:.2f}; "
                "the last traded price is not usable right now"
            )
        bounded = min(max(price, self.low), self.high)
        snapped = round(bounded / self.tick_size) * self.tick_size
        # Snapping can push a boundary value back outside the band.
        if snapped < self.low:
            snapped += self.tick_size
        elif snapped > self.high:
            snapped -= self.tick_size
        return round(snapped, 4)

    def __str__(self) -> str:
        return f"{self.low:.2f}-{self.high:.2f} (LTP {self.ltp}, +/-{self.percent}%)"


@dataclass(frozen=True)
class Holding:
    """One line of the depository holding statement."""

    symbol: str
    total_quantity: float
    free_quantity: float
    security_name: str | None = None
    isin: str | None = None
    boid: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Holding":
        return cls(
            symbol=data.get("scripCode", ""),
            total_quantity=data.get("totalQuantity") or 0.0,
            free_quantity=data.get("freeQuantity") or 0.0,
            security_name=data.get("securityName"),
            isin=data.get("isin"),
            boid=data.get("boid"),
            raw=data,
        )


@dataclass(frozen=True)
class Order:
    """One row of the order book."""

    exchange_order_id: str | None
    symbol: str
    side: Side
    quantity: float
    price: float
    traded_quantity: float
    remaining_quantity: float
    status: str | None
    order_time: str | None = None
    id: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Order":
        return cls(
            exchange_order_id=data.get("exchangeOrderId"),
            symbol=data.get("symbol", ""),
            side=Side(data["buyOrSell"]) if data.get("buyOrSell") else Side.BUY,
            quantity=data.get("orderQuantity") or 0.0,
            price=data.get("orderPrice") or 0.0,
            traded_quantity=data.get("totalTradedQuantity") or 0.0,
            remaining_quantity=data.get("remainingOrderQuantity") or 0.0,
            status=data.get("activeStatus"),
            order_time=data.get("orderTime"),
            id=data.get("id"),
            raw=data,
        )

    @property
    def is_open(self) -> bool:
        return (self.status or "").upper() == "OPEN"

    @property
    def is_filled(self) -> bool:
        return self.traded_quantity > 0 and self.remaining_quantity == 0


@dataclass(frozen=True)
class OrderResult:
    """
    What happened to a placement request.

    `submitted` is derived from the portal's own answer, never assumed, so a
    rejected order can't be read as a live one.
    """

    submitted: bool
    side: Side
    symbol: str
    quantity: int
    price: float
    band: PriceBand | None = None
    dry_run: bool = False
    message: str | None = None
    problems: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict, repr=False)

    def __bool__(self) -> bool:
        return self.submitted


@dataclass(frozen=True)
class Account:
    """Identity and trading limits for the logged-in client."""

    client_id: int
    user_id: int
    member_code: str
    display_name: str | None = None
    collateral: float | None = None
    utilized_collateral: float | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def available_collateral(self) -> float | None:
        if self.collateral is None or self.utilized_collateral is None:
            return None
        return self.collateral - self.utilized_collateral

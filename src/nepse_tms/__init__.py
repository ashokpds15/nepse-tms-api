"""
Python client for NEPSE TMS broker portals.

    >>> from nepse_tms import TmsClient
    >>> with TmsClient("https://tms35.nepsetms.com.np") as client:
    ...     client.login("USERNAME", "PASSWORD")
    ...     client.holdings()

Talks to the portal's REST API directly — no browser, no Selenium.
"""

from nepse_tms.client import DEFAULT_BAND_PERCENT, USER_AGENT, TmsClient
from nepse_tms.errors import (
    NotAuthenticated,
    OrderRejected,
    SecurityNotFound,
    StalePrice,
    TmsApiError,
    TmsError,
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

__all__ = [
    "DEFAULT_BAND_PERCENT",
    "USER_AGENT",
    "Account",
    "Holding",
    "NotAuthenticated",
    "Order",
    "OrderRejected",
    "OrderResult",
    "PriceBand",
    "Security",
    "SecurityNotFound",
    "Side",
    "StalePrice",
    "TmsApiError",
    "TmsClient",
    "TmsError",
]

__version__ = "0.1.0"

"""Exceptions raised by the client."""

from __future__ import annotations


class TmsError(RuntimeError):
    """Base class for every error this package raises."""


class TmsApiError(TmsError):
    """The portal answered with something that must not be interpreted."""


class NotAuthenticated(TmsApiError):
    """
    The session is missing, expired, or was refused.

    `code` carries the portal's own status code when the failure came from
    ``/authenticate``, so callers can tell a wrong captcha (retryable) from bad
    credentials (not).
    """

    #: Wrong captcha — retry with a fresh one.
    WRONG_CAPTCHA = "108"
    #: Bad username or password.
    BAD_CREDENTIALS = "103"
    #: Account disabled by the broker.
    DISABLED = "106"
    #: Account inactive.
    INACTIVE = "107"
    #: Too many concurrent sessions.
    SESSION_LIMIT = "403"

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code

    @property
    def retryable(self) -> bool:
        """True only for a wrong captcha; everything else needs a human."""
        return self.code == self.WRONG_CAPTCHA


class OrderRejected(TmsApiError):
    """
    An order was refused before or during submission.

    `problems` lists every reason found, so a caller sees all of them at once
    rather than fixing one and discovering the next.
    """

    def __init__(self, message: str, problems: list[str] | None = None) -> None:
        super().__init__(message)
        self.problems = problems or []


class SecurityNotFound(TmsApiError):
    """No tradable security matches the requested symbol."""


class StalePrice(TmsApiError):
    """
    The portal returned a price that cannot be a real last-traded price.

    Outside trading hours the LTP endpoint falls back to the security's
    ``networthBasePrice`` — for example 100.0 for a scrip trading near 1700.
    Deriving a price band from that produces an impossible range (a lower bound
    above the upper bound), so this is raised instead of handing back a band
    that would get every order rejected.

    Treat it as "no usable price right now", not as an error to work around.
    """

    def __init__(self, message: str, ltp: float | None = None) -> None:
        super().__init__(message)
        self.ltp = ltp

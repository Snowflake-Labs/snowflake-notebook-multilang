"""Download utilities with retry logic."""

from __future__ import annotations

import functools
import logging
import time
from typing import Callable, TypeVar

logger = logging.getLogger("sfnb_multilang.shared.download")

F = TypeVar("F", bound=Callable)


def retry(
    max_attempts: int = 3,
    delay: float = 5.0,
    backoff: float = 1.0,
    exceptions: tuple = (Exception,),
) -> Callable[[F], F]:
    """Decorator that retries a function on failure.

    Args:
        max_attempts: Maximum number of attempts.
        delay: Initial delay between retries (seconds).
        backoff: Multiplier applied to delay after each retry.
        exceptions: Tuple of exception types to catch.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay
            last_exception = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exception = exc
                    if attempt < max_attempts:
                        logger.warning(
                            "%s failed (attempt %d/%d), retrying in %.0fs: %s",
                            func.__name__,
                            attempt,
                            max_attempts,
                            current_delay,
                            exc,
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logger.error(
                            "%s failed after %d attempts: %s",
                            func.__name__,
                            max_attempts,
                            exc,
                        )
            raise last_exception  # type: ignore[misc]

        return wrapper  # type: ignore[return-value]

    return decorator

"""
PolyAugur Retry Utilities
Decorators for resilient API calls with exponential backoff.

Author: Diego Ringleb | Phase 10 | 2026-02-28
"""

import time
import logging
import functools
from typing import Tuple, Type

logger = logging.getLogger(__name__)


def retry(
    max_retries: int = 3,
    backoff_base: float = 1.0,
    backoff_max: float = 30.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: str = "Retrying...",
):
    """
    Decorator that retries a function on failure with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts
        backoff_base: Base delay in seconds (doubles each retry)
        backoff_max: Maximum delay between retries
        exceptions: Tuple of exception types to catch
        on_retry: Log message prefix on retry
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        delay = min(backoff_base * (2 ** attempt), backoff_max)
                        logger.warning(
                            f"⚠️ {on_retry} {func.__name__} attempt {attempt+1}/{max_retries} "
                            f"failed: {e}. Retrying in {delay:.1f}s..."
                        )
                        time.sleep(delay)
                    else:
                        logger.error(
                            f"❌ {func.__name__} failed after {max_retries} retries: {e}"
                        )
            raise last_exception
        return wrapper
    return decorator

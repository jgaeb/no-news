import functools
import logging
import traceback
from datetime import datetime

################################################################################
# Exception handling decorator


def handle_exceptions(coroutine_func):
    """A decorator to handle exceptions in coroutine functions."""

    @functools.wraps(coroutine_func)
    async def wrapper(*args, **kwargs):
        try:
            # Execute the coroutine function
            return await coroutine_func(*args, **kwargs)
        except Exception as e:
            # Get the entire traceback as a string
            traceback_str = traceback.format_exc()
            # Log the exception with the traceback
            logging.error(
                "An error occurred in %s: %s\nTraceback: %s",
                coroutine_func.__name__,
                e,
                traceback_str,
            )

    return wrapper


################################################################################
# Date conversion functions


def convert_date(s):
    """Convert a string to a date."""
    return datetime.strptime(s.decode("ascii"), "%Y-%m-%d").date()


def adapt_date(date):
    """Adapt a date to a string."""
    return date.isoformat()

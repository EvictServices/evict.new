import logging
import functools
from typing import Any, Callable

class Logger:
    def __init__(self, name: str = "bot"):
        self._log = logging.getLogger(name)

    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log an info message."""
        self._log.info(message, *args, **kwargs)

    def warn(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log a warning message."""
        self._log.warning(message, *args, **kwargs)

    def debug(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log a debug message."""
        self._log.debug(message, *args, **kwargs)

    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log an error message."""
        self._log.error(message, *args, **kwargs)

    def exception(self, message: str, *args: Any, exc_info: bool = True, **kwargs: Any) -> None:
        """Log an exception with traceback."""
        self._log.exception(message, *args, exc_info=exc_info, **kwargs)

def log_call(level: str = "info") -> Callable:
    """Decorator to log function calls."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            log = Logger(func.__module__)
            log_func = getattr(log, level)
            log_func(f"Calling {func.__name__}")
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                log.exception(f"Error in {func.__name__}: {str(e)}")
                raise
        return wrapper
    return decorator

log = Logger() 
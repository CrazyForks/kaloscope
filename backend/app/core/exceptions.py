from enum import StrEnum, auto
from pathlib import Path

from sanic import NotFound, Request, SanicException, file
from sanic.log import Colors, logger
from sanic_ext.exceptions import ValidationError

# get the root path of the project
ROOT_PATH = Path(__file__).resolve().parents[3]


class ErrorCode(StrEnum):
    """The error codes for Kaloscope application."""

    INTERNAL_SERVER_ERROR = auto()
    BAD_REQUEST = auto()
    UNAUTHORIZED = auto()
    NOT_FOUND = auto()
    USER_NOT_FOUND = auto()
    FLOW_NOT_FOUND = auto()
    FLOW_NOT_PUBLISHED = auto()
    LOGIN_FAILED = auto()
    LOGIN_EXPIRED = auto()
    NAME_ALREADY_EXISTS = auto()
    USERNAME_ALREADY_EXISTS = auto()
    DUPLICATE_DIRECTORY = auto()
    INCORRECT_PASSWORD = auto()
    INVALID_YAML_CONFIG = auto()
    GET_INFO_HASH_FAILED = auto()
    INFO_HASH_COLLISION = auto()
    HTTP_REQUEST_FAILED = auto()
    FLOW_ALREADY_RUNNING = auto()
    IMPORT_GRAPHS_FAILED = auto()


class KaloscopeException(SanicException):
    """The base exception class for Kaloscope application."""

    status_code = 520
    quiet = False


class BadRequestException(KaloscopeException):
    """The exception class for bad requests."""

    status_code = 400
    message = ErrorCode.BAD_REQUEST


class UnauthorizedException(KaloscopeException):
    """The exception class for unauthorized access."""

    status_code = 401
    message = ErrorCode.UNAUTHORIZED
    quiet = True


class NotFoundException(KaloscopeException):
    """The exception class for not found resources."""

    status_code = 404
    message = ErrorCode.NOT_FOUND


async def error_handler(request: Request, exception: Exception):
    """Wrap all exceptions as `KaloscopeException` with specific error codes.

    See https://sanic.dev/en/guide/best-practices/exceptions.html for more details.

    Args:
        request: The request object.
        exception: The exception object.

    Raises:
        KaloscopeException: The wrapped exception.

    Returns:
        The 404.html page for all 404 errors.
    """
    if isinstance(exception, NotFound):
        # return the frontend 404.html for all 404 errors
        return await file(ROOT_PATH / "frontend/build/404.html")

    if request.route:
        # set the error format to JSON for all routes
        request.route.extra.error_format = "json"

    # wrap the exception as KaloscopeException
    if not isinstance(exception, KaloscopeException):
        if isinstance(exception, ValidationError):
            msg = exception.extra.get("exception") if exception.extra else None
            logger.error(f"Validation error: {Colors.RED}%s{Colors.END}", msg)
            exception = BadRequestException()
        elif isinstance(exception, SanicException):
            exception = KaloscopeException(exception.message, exception.status_code)
        else:
            exception = KaloscopeException(ErrorCode.INTERNAL_SERVER_ERROR)

    raise exception

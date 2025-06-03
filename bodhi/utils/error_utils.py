from typing import Optional, Dict, Any
from datetime import datetime
from enum import IntEnum


class BodhiErrors(IntEnum):
    BadRequest = 400
    Unauthorized = 401
    InsufficientCredit = 402
    InactiveCustomer = 403
    ClientClosed = 499
    InternalServerError = 500
    GatewayDown = 502
    GatewayTimeout = 504


def make_error_response(
    message: str,
    code: int,
    err_type: Optional[str] = None,
    timestamp: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Create a standardized error response dictionary.
    """
    resp = {
        "error": err_type if err_type else "bodhi_error",
        "message": message,
        "code": code,
        "timestamp": timestamp
        or datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
    }
    if extra:
        resp.update(extra)
    return resp

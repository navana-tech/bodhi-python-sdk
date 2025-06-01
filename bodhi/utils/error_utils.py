from typing import Optional, Dict, Any
from datetime import datetime


def make_error_response(
    err_type: str,
    message: str,
    code: int,
    timestamp: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Create a standardized error response dictionary.
    """
    resp = {
        "error": err_type,
        "message": message,
        "code": code,
        "timestamp": timestamp or datetime.utcnow().isoformat() + "Z",
    }
    if extra:
        resp.update(extra)
    return resp

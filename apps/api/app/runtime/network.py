import ipaddress

DEFAULT_COORDINATOR_HOST = "127.0.0.1"
DEFAULT_COORDINATOR_PORT = 8100
DEFAULT_OCR_SERVICE_HOST = "127.0.0.1"
DEFAULT_OCR_SERVICE_PORT = 8101


def require_loopback_host(host: str) -> str:
    """Accept only literal loopback addresses; hostnames are intentionally rejected."""

    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("runtime services require a literal loopback address") from exc
    if not address.is_loopback:
        raise ValueError("runtime services may bind only to loopback")
    return host

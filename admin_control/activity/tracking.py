"""Activity logging helpers for the custom admin."""
from ipaddress import ip_address
from typing import Dict


class AdminActivityLogger:
    """Collects inbound admin requests."""

    def log_request(
        self,
        *,
        user_id: str,
        path: str,
        method: str,
        ip: str,
        device: str,
        duration_ms: float,
        status_code: int,
        response_size: int,
    ) -> Dict:
        record = {
            "user_id": user_id,
            "path": path,
            "method": method,
            "ip": ip_address(ip).compressed,
            "device": device,
            "duration_ms": duration_ms,
            "status_code": status_code,
            "response_size": response_size,
        }
        return record

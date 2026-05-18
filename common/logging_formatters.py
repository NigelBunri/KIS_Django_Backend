import json
import logging
import traceback


def _get_request_id() -> str:
    try:
        from common.middleware import get_request_id
        return get_request_id()
    except Exception:
        return "-"


class JsonRequestFormatter(logging.Formatter):
    """
    Emits one JSON object per log line.
    Fields: timestamp, level, logger, message, request_id, [exc_info].
    Compatible with Datadog, CloudWatch, and most log aggregators.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": _get_request_id(),
        }
        if record.exc_info:
            payload["exc"] = traceback.format_exception(*record.exc_info)
        return json.dumps(payload, default=str)

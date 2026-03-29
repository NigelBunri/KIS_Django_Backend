"""Analytics helpers for the admin control platform."""

from .insights import AnalyticsInsightService


class AnalyticsAggregator(AnalyticsInsightService):
    """Backwards-compatible wrapper that exposes a summarize API."""

    def summarize(self):
        return self.collect()

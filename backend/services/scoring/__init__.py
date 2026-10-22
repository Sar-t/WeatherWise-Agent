"""Activity scoring: weather + air quality -> suitability, best times, hints."""

from backend.services.scoring.engine import assess_activity

__all__ = ["assess_activity"]

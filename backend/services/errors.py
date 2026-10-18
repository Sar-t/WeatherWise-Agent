"""Domain errors raised by WeatherWise services."""


class WeatherWiseError(Exception):
    """Base class for all WeatherWise errors."""


class InvalidInputError(WeatherWiseError, ValueError):
    """A caller passed malformed input, e.g. an empty query or out-of-range coordinates."""


class LocationNotFoundError(WeatherWiseError):
    """The requested place could not be geocoded."""


class OutOfRangeError(WeatherWiseError):
    """The requested time window is outside what the data source covers."""


class UnrecognizedDateError(WeatherWiseError):
    """A date or time expression could not be understood."""


class UpstreamAPIError(WeatherWiseError):
    """An external API failed or returned an unusable response."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code  # HTTP status, when the API answered at all

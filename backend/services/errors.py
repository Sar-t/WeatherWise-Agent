"""Domain errors raised by WeatherWise services."""


class WeatherWiseError(Exception):
    """Base class for all WeatherWise errors."""


class LocationNotFoundError(WeatherWiseError):
    """The requested place could not be geocoded."""


class OutOfRangeError(WeatherWiseError):
    """The requested time window is outside what the data source covers."""


class UnrecognizedDateError(WeatherWiseError):
    """A date or time expression could not be understood."""


class UpstreamAPIError(WeatherWiseError):
    """An external API failed or returned an unusable response."""

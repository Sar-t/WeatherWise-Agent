"""Our own small icon set, mapped from OpenWeather condition codes, so a frontend isn't tied to
OpenWeather's icons. Codes: https://openweathermap.org/weather-conditions"""

ICON_KEYS = ("clear-day", "clear-night", "partly-cloudy", "partly-cloudy-night", "cloudy", "rain", "heavy-rain",
             "thunderstorm", "drizzle", "fog", "haze", "snow", "wind")
HEAVY_RAIN = {502, 503, 504, 522, 531}
FOG = {701, 741}
HAZE = {711, 721, 731, 751, 761, 762}


def icon_key(condition_id: int | None, icon: str | None = None) -> str:
    """'rain', 'clear-night', ... from an OpenWeather code; day/night from its icon suffix ("01n")."""
    night = bool(icon) and icon.endswith("n")
    if condition_id is None:
        return "cloudy"
    group = condition_id // 100
    if group == 2 or condition_id == 781:  # 781: tornado
        return "thunderstorm"
    if group == 3:
        return "drizzle"
    if group == 5:
        return "heavy-rain" if condition_id in HEAVY_RAIN else "rain"
    if group == 6:
        return "snow"
    if condition_id in FOG:
        return "fog"
    if condition_id in HAZE:
        return "haze"
    if condition_id == 771:  # squalls
        return "wind"
    if condition_id == 800:
        return "clear-night" if night else "clear-day"
    if condition_id in (801, 802):
        return "partly-cloudy-night" if night else "partly-cloudy"
    return "cloudy"  # 803, 804 and anything unknown


def compass(degrees: float | None) -> str | None:
    """8-point compass direction the wind comes from: 0 -> 'N', 225 -> 'SW'."""
    if degrees is None:
        return None
    return ("N", "NE", "E", "SE", "S", "SW", "W", "NW")[int((degrees % 360) / 45 + 0.5) % 8]

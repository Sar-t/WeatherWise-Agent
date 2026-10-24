import pytest

from backend.services.icons import ICON_KEYS, compass, icon_key


@pytest.mark.parametrize("code, icon, expected", [
    (200, "11d", "thunderstorm"), (232, "11n", "thunderstorm"), (781, "50d", "thunderstorm"),  # tornado
    (300, "09d", "drizzle"), (321, "09n", "drizzle"),
    (500, "10d", "rain"), (501, "10n", "rain"), (511, "13d", "rain"), (520, "09d", "rain"),
    (502, "10d", "heavy-rain"), (503, "10d", "heavy-rain"), (504, "10d", "heavy-rain"), (522, "09d", "heavy-rain"),
    (600, "13d", "snow"), (622, "13n", "snow"),
    (701, "50d", "fog"), (741, "50n", "fog"),
    (711, "50d", "haze"), (721, "50d", "haze"), (731, "50d", "haze"), (751, "50d", "haze"), (761, "50d", "haze"),
    (771, "50d", "wind"),
    (800, "01d", "clear-day"), (800, "01n", "clear-night"),
    (801, "02d", "partly-cloudy"), (802, "03d", "partly-cloudy"),
    (801, "02n", "partly-cloudy-night"), (802, "03n", "partly-cloudy-night"),
    (803, "04d", "cloudy"), (804, "04n", "cloudy"),
    (None, None, "cloudy"), (999, None, "cloudy"),
])
def test_icon_key(code, icon, expected: str) -> None:
    assert icon_key(code, icon) == expected
    assert expected in ICON_KEYS


def test_day_is_the_default_without_an_icon_suffix() -> None:
    assert icon_key(800, None) == "clear-day" and icon_key(801, "d") == "partly-cloudy"


@pytest.mark.parametrize("degrees, expected", [(0, "N"), (22, "N"), (23, "NE"), (90, "E"), (225, "SW"),
                                               (337, "NW"), (338, "N"), (360, "N"), (None, None)])
def test_compass(degrees, expected) -> None:
    assert compass(degrees) == expected

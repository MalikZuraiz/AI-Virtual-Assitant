"""Small, dependency-light web integrations.

Two things here were rebuilt after they turned out to be broken in practice:

**Wikipedia.** The ``wikipedia`` PyPI package (unmaintained since 2014) sends
no ``User-Agent``. Wikimedia now blocks that outright and returns an HTML
error page, so the library's ``r.json()`` raises a JSONDecodeError on *every*
lookup - which is why every query answered "I couldn't find anything". This
now calls Wikipedia's REST API directly with a proper User-Agent, which is
what their policy requires, and falls back to the search API when a title
doesn't match exactly.

**Weather.** ``wttr.in`` was reading several degrees high (38°C when the
actual observation was 34°C) because it reports a model-derived value for a
wide area. Replaced with **Open-Meteo** - also free and keyless, but it
serves actual observation-anchored values for a specific lat/lon, which is
what a phone or Google shows.
"""
from __future__ import annotations

import logging
import webbrowser
from urllib.parse import quote, quote_plus

import requests

logger = logging.getLogger("assistant.web")

#: Wikimedia's policy requires a descriptive User-Agent with contact info.
#: Requests without one are refused, which is what broke the old library.
USER_AGENT = "NovaDesktopAssistant/1.0 (personal use; https://github.com/)"
WIKI_REST = "https://en.wikipedia.org/api/rest_v1/page/summary/"
WIKI_API = "https://en.wikipedia.org/w/api.php"


def _wiki_get(url: str, params: dict | None = None, timeout: int = 8):
    return requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=timeout)


def _wiki_search_title(query: str) -> str | None:
    """Best matching article title, so near-misses still resolve."""
    try:
        response = _wiki_get(
            WIKI_API,
            {
                "action": "query", "list": "search", "srsearch": query,
                "srlimit": 1, "format": "json",
            },
        )
        response.raise_for_status()
        results = response.json().get("query", {}).get("search", [])
    except (requests.RequestException, ValueError):
        return None
    return results[0]["title"] if results else None


def wikipedia_summary(query: str, sentences: int = 3) -> str:
    query = (query or "").strip()
    if not query:
        return "What would you like me to look up?"

    for title in (query, None):
        if title is None:
            title = _wiki_search_title(query)
            if not title:
                break
        try:
            response = _wiki_get(WIKI_REST + quote(title.replace(" ", "_"), safe=""))
            if response.status_code == 404:
                continue
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError):
            logger.warning("Wikipedia lookup failed for %r", title, exc_info=True)
            continue

        if data.get("type") == "disambiguation":
            return (
                f"'{title}' could mean several things on Wikipedia. "
                "Try being more specific."
            )
        extract = (data.get("extract") or "").strip()
        if not extract:
            continue
        parts = [s.strip() for s in extract.replace("\n", " ").split(". ") if s.strip()]
        text = ". ".join(parts[:sentences]).rstrip(".") + "."
        url = (data.get("content_urls", {}).get("desktop", {}) or {}).get("page", "")
        return f"{data.get('title', title)}: {text}" + (f"\n{url}" if url else "")

    return f"I couldn't find a Wikipedia article for '{query}'."


def google_search(query: str) -> str:
    webbrowser.open(f"https://www.google.com/search?q={quote_plus(query)}")
    return f"Searching Google for '{query}'."


def youtube_search(query: str) -> str:
    webbrowser.open(f"https://www.youtube.com/results?search_query={quote_plus(query)}")
    return f"Searching YouTube for '{query}'."


def tell_joke(category: str = "neutral") -> str:
    try:
        import pyjokes

        return pyjokes.get_joke(language="en", category=category)
    except Exception:
        logger.warning("pyjokes failed", exc_info=True)
        return "I couldn't think of a joke right now."


#: WMO weather codes -> words. Open-Meteo returns a numeric code, not text.
WEATHER_CODES = {
    0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Freezing fog", 51: "Light drizzle", 53: "Drizzle",
    55: "Heavy drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain",
    67: "Freezing rain", 71: "Light snow", 73: "Snow", 75: "Heavy snow",
    77: "Snow grains", 80: "Light showers", 81: "Showers", 82: "Violent showers",
    85: "Snow showers", 86: "Heavy snow showers", 95: "Thunderstorm",
    96: "Thunderstorm with hail", 99: "Thunderstorm with hail",
}


def geocode(place: str) -> tuple[float, float, str] | None:
    """Resolve a place name to ``(lat, lon, label)`` via Open-Meteo's geocoder."""
    try:
        response = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": place, "count": 1, "language": "en", "format": "json"},
            timeout=8,
        )
        response.raise_for_status()
        results = response.json().get("results") or []
    except (requests.RequestException, ValueError):
        logger.warning("Geocoding failed for %r", place, exc_info=True)
        return None
    if not results:
        return None
    top = results[0]
    label = ", ".join(x for x in (top.get("name"), top.get("country")) if x)
    return float(top["latitude"]), float(top["longitude"]), label


def weather(city: str) -> str:
    city = (city or "").strip() or "Lahore"
    located = geocode(city)
    if located is None:
        return f"I couldn't find a place called '{city}'."
    latitude, longitude, label = located
    try:
        response = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min",
                "timezone": "auto",
                "forecast_days": 1,
            },
            timeout=8,
        )
        response.raise_for_status()
        data = response.json()
        current = data["current"]
        daily = data.get("daily", {})
    except (requests.RequestException, ValueError, KeyError):
        logger.warning("Weather lookup failed for %r", city, exc_info=True)
        return f"I couldn't fetch the weather for '{city}' right now."

    description = WEATHER_CODES.get(int(current.get("weather_code", -1)), "")
    lines = [
        f"{label}: {description}, {current['temperature_2m']:.0f}°C "
        f"(feels like {current['apparent_temperature']:.0f}°C)"
    ]
    highs, lows = daily.get("temperature_2m_max"), daily.get("temperature_2m_min")
    if highs and lows:
        lines.append(f"Today: {lows[0]:.0f}° to {highs[0]:.0f}°")
    lines.append(
        f"Humidity {current['relative_humidity_2m']:.0f}%, "
        f"wind {current['wind_speed_10m']:.0f} km/h"
    )
    return "\n".join(lines)


def my_ip_and_location() -> str:
    try:
        ip = requests.get("https://api64.ipify.org?format=json", timeout=8).json()["ip"]
        info = requests.get(f"https://ipapi.co/{ip}/json/", timeout=8).json()
        return (
            f"IP: {ip}\n"
            f"Location: {info.get('city')}, {info.get('region')}, {info.get('country_name')}\n"
            f"Timezone: {info.get('timezone')}"
        )
    except Exception:
        logger.warning("IP/location lookup failed", exc_info=True)
        return "I couldn't determine your IP/location right now (check your internet connection)."

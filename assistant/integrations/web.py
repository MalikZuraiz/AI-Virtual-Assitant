"""Small, dependency-light web integrations.

Notes on what was dropped from the legacy code and why:
- ``googletrans`` (unofficial Google Translate scraper) and ``pywikihow``
  are unmaintained and break unpredictably; they are not ported.
- Weather used to scrape Google's HTML search results with BeautifulSoup
  (fragile, breaks whenever Google changes markup). Replaced with the
  keyless ``wttr.in`` JSON endpoint.
"""
from __future__ import annotations

import logging
import webbrowser
from urllib.parse import quote_plus

import requests

logger = logging.getLogger("assistant.web")


def wikipedia_summary(query: str, sentences: int = 3) -> str:
    try:
        import wikipedia

        return wikipedia.summary(query, sentences=sentences, auto_suggest=True)
    except Exception:
        logger.warning("Wikipedia lookup failed for %r", query, exc_info=True)
        return f"I couldn't find a Wikipedia summary for '{query}'."


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


def weather(city: str) -> str:
    city = city.strip() or "Islamabad"
    try:
        response = requests.get(f"https://wttr.in/{quote_plus(city)}?format=j1", timeout=8)
        response.raise_for_status()
        current = response.json()["current_condition"][0]
        return (
            f"Weather in {city}: {current['weatherDesc'][0]['value']}, "
            f"{current['temp_C']}°C (feels like {current['FeelsLikeC']}°C), "
            f"humidity {current['humidity']}%."
        )
    except Exception:
        logger.warning("Weather lookup failed for %r", city, exc_info=True)
        return f"I couldn't fetch the weather for '{city}' right now."


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

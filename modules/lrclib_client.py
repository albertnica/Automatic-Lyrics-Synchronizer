"""
modules/lrclib_client.py
Native client for LRCLIB REST API (https://lrclib.net).

Why:
Replaces external desktop binaries (e.g. LRCGET) with direct HTTP requests
to retrieve canonical synced and plain-text lyrics with zero system dependencies.
"""

import logging
from typing import Optional, Dict, Any, List
import requests

logger = logging.getLogger(__name__)

LRCLIB_BASE_URL = "https://lrclib.net/api"
DEFAULT_USER_AGENT = "Automatic-Lyrics-Synchronizer/1.0.0 (https://github.com/alfa/Automatic-Lyrics-Synchronizer)"
TIMEOUT_SECONDS = 10


def get_lyrics(
    track_name: str,
    artist_name: str,
    album_name: Optional[str] = None,
    duration: Optional[float] = None,
    timeout: int = TIMEOUT_SECONDS,
) -> Optional[Dict[str, Any]]:
    """
    Fetch exact lyrics record from LRCLIB using track metadata.

    Why:
    LRCLIB provides an exact-match endpoint (`/api/get`) that resolves directly
    to verified community lyrics when artist, title, and duration align closely.

    Args:
        track_name: Song title.
        artist_name: Primary artist or band name.
        album_name: Optional album name to refine matching.
        duration: Optional song duration in seconds (integer or float).
        timeout: HTTP request timeout in seconds.

    Returns:
        Dictionary containing 'syncedLyrics', 'plainLyrics', 'instrumental',
        and metadata fields, or None if no match is found or an error occurs.
    """
    if not track_name or not artist_name:
        logger.debug("LRCLIB get_lyrics skipped: missing track_name or artist_name")
        return None

    params: Dict[str, Any] = {
        "track_name": track_name.strip(),
        "artist_name": artist_name.strip(),
    }
    if album_name and album_name.strip():
        params["album_name"] = album_name.strip()
    if duration and duration > 0:
        params["duration"] = int(round(duration))

    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/json",
    }

    url = f"{LRCLIB_BASE_URL}/get"
    try:
        response = requests.get(url, params=params, headers=headers, timeout=timeout)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict):
                logger.info(
                    "LRCLIB hit: found lyrics for '%s - %s' (synced: %s, instrumental: %s)",
                    artist_name,
                    track_name,
                    bool(data.get("syncedLyrics")),
                    bool(data.get("instrumental")),
                )
                return data
        elif response.status_code == 404:
            logger.debug("LRCLIB: no exact match found for '%s - %s'", artist_name, track_name)
        else:
            logger.warning(
                "LRCLIB returned unexpected status code %s for '%s - %s'",
                response.status_code,
                artist_name,
                track_name,
            )
    except requests.RequestException as e:
        logger.warning("LRCLIB request failed for '%s - %s': %s", artist_name, track_name, e)

    return None


def search_lyrics(
    query: Optional[str] = None,
    track_name: Optional[str] = None,
    artist_name: Optional[str] = None,
    limit: int = 5,
    timeout: int = TIMEOUT_SECONDS,
) -> List[Dict[str, Any]]:
    """
    Search LRCLIB for candidate lyrics records.

    Why:
    When exact metadata lookup fails (e.g. slight duration mismatch or title variations),
    the search endpoint (`/api/search`) surfaces nearby candidates that can be ranked.

    Args:
        query: Generic search query string.
        track_name: Specific track title filter.
        artist_name: Specific artist filter.
        limit: Maximum number of candidate results to return.
        timeout: HTTP request timeout in seconds.

    Returns:
        List of matching dictionary records.
    """
    params: Dict[str, Any] = {}
    if query and query.strip():
        params["q"] = query.strip()
    if track_name and track_name.strip():
        params["track_name"] = track_name.strip()
    if artist_name and artist_name.strip():
        params["artist_name"] = artist_name.strip()

    if not params:
        return []

    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/json",
    }

    url = f"{LRCLIB_BASE_URL}/search"
    try:
        response = requests.get(url, params=params, headers=headers, timeout=timeout)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                return data[:limit]
    except requests.RequestException as e:
        logger.warning("LRCLIB search request failed: %s", e)

    return []


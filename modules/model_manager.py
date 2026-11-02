"""
modules/model_manager.py
Hugging Face Hub Model Discovery, Search Autocomplete, and Local Cache Manager.

Why:
Allows users to discover, search, and pre-cache speech recognition (ASR) models
directly from the Web Studio UI without requiring manual CLI downloads or blindly guessing model IDs.
"""

import os
import re
import logging
from typing import List, Dict, Any, Optional
import requests

logger = logging.getLogger(__name__)

HF_MODELS_API_URL = "https://huggingface.co/api/models"
DEFAULT_TIMEOUT = 8

# Curated list of high-accuracy and efficient ASR models
RECOMMENDED_MODELS: List[Dict[str, Any]] = [
    {
        "id": "openai/whisper-large-v3-turbo",
        "name": "Whisper Large v3 Turbo",
        "description": "Fastest large model with near state-of-the-art accuracy.",
        "recommended": True,
        "default": True,
    },
    {
        "id": "openai/whisper-large-v3",
        "name": "Whisper Large v3",
        "description": "Gold-standard accuracy across 99+ languages.",
        "recommended": True,
        "default": False,
    },
    {
        "id": "openai/whisper-medium",
        "name": "Whisper Medium",
        "description": "Balanced speed and precision for mid-range GPUs and CPUs.",
        "recommended": True,
        "default": True,
    },
    {
        "id": "openai/whisper-small",
        "name": "Whisper Small",
        "description": "Lightweight model optimized for rapid local transcription.",
        "recommended": True,
        "default": True,
    },
    {
        "id": "distil-whisper/distil-large-v3",
        "name": "Distil-Whisper Large v3",
        "description": "Distilled model running up to 6x faster with minimal quality trade-off.",
        "recommended": True,
        "default": False,
    },
]


def get_hf_cache_dir() -> str:
    """
    Resolve the local Hugging Face Hub cache directory.

    Why:
    Hugging Face stores downloaded snapshots under `HF_HUB_CACHE` or `HF_HOME/hub`,
    defaulting to `~/.cache/huggingface/hub`. Knowing this path allows offline
    detection of downloaded models without invoking heavy PyTorch loaders.
    """
    hub_cache = os.environ.get("HF_HUB_CACHE")
    if hub_cache and os.path.isdir(hub_cache):
        return hub_cache

    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        candidate = os.path.join(hf_home, "hub")
        return candidate

    return os.path.expanduser(os.path.join("~", ".cache", "huggingface", "hub"))


def list_cached_models() -> List[str]:
    """
    Scan the local Hugging Face cache for downloaded ASR/transformers models.

    Returns:
        List of repo IDs formatted as 'author/model-name'.
    """
    cache_dir = get_hf_cache_dir()
    if not os.path.isdir(cache_dir):
        return []

    cached: List[str] = []
    try:
        entries = os.listdir(cache_dir)
        for entry in entries:
            # Hugging Face caches directories with prefix 'models--'
            if entry.startswith("models--"):
                parts = entry.split("--")[1:]
                if len(parts) >= 2:
                    repo_id = f"{parts[0]}/{'--'.join(parts[1:])}"
                    # Verify snapshots directory exists and contains files
                    snapshots_dir = os.path.join(cache_dir, entry, "snapshots")
                    if os.path.isdir(snapshots_dir) and os.listdir(snapshots_dir):
                        cached.append(repo_id)
    except Exception as e:
        logger.debug("Error scanning Hugging Face cache dir: %s", e)

    return sorted(list(set(cached)))


def is_model_cached(model_id: str) -> bool:
    """
    Check if a specific Hugging Face model repository exists in the local cache.
    """
    cached = list_cached_models()
    return model_id in cached


def search_hub_models(query: str, limit: int = 15) -> List[Dict[str, Any]]:
    """
    Search Hugging Face Model Hub for automatic-speech-recognition models.

    Why:
    Enables live autocomplete and recommendations in the Web Studio UI.

    Args:
        query: User input query string (e.g. 'openai', 'whisper', 'spanish').
        limit: Max number of candidates returned.

    Returns:
        List of model summaries with cache presence flags.
    """
    query = (query or "").strip()
    cached_set = set(list_cached_models())

    # Build local recommended candidates matching query
    results: List[Dict[str, Any]] = []
    seen_ids = set()

    for rec in RECOMMENDED_MODELS:
        m_id = rec["id"]
        if not query or query.lower() in m_id.lower() or query.lower() in rec["name"].lower():
            seen_ids.add(m_id)
            results.append({
                "id": m_id,
                "name": rec["name"],
                "description": rec.get("description", ""),
                "downloads": 1000000,
                "likes": 500,
                "pipeline_tag": "automatic-speech-recognition",
                "is_cached": m_id in cached_set,
                "is_recommended": True,
            })

    if not query:
        return results[:limit]

    # Query Hugging Face Model Hub API
    params = {
        "search": query,
        "pipeline_tag": "automatic-speech-recognition",
        "sort": "downloads",
        "direction": "-1",
        "limit": limit,
    }
    headers = {"Accept": "application/json"}
    hf_token = os.environ.get("HF_TOKEN")
    if hf_token:
        headers["Authorization"] = f"Bearer {hf_token}"

    try:
        response = requests.get(HF_MODELS_API_URL, params=params, headers=headers, timeout=DEFAULT_TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                for item in data:
                    item_id = item.get("id")
                    if not item_id or item_id in seen_ids:
                        continue
                    seen_ids.add(item_id)
                    results.append({
                        "id": item_id,
                        "name": item_id.split("/")[-1],
                        "description": f"Downloads: {item.get('downloads', 0):,} | Likes: {item.get('likes', 0)}",
                        "downloads": item.get("downloads", 0),
                        "likes": item.get("likes", 0),
                        "pipeline_tag": item.get("pipeline_tag", "automatic-speech-recognition"),
                        "is_cached": item_id in cached_set,
                        "is_recommended": False,
                    })
    except requests.RequestException as e:
        logger.warning("Hugging Face Hub API search failed: %s", e)

    return results[:limit]


def get_model_status_catalog() -> Dict[str, Any]:
    """
    Return comprehensive model catalog including recommended models,
    cached models, and system status.
    """
    cached = list_cached_models()
    cached_set = set(cached)

    curated = []
    for rec in RECOMMENDED_MODELS:
        curated.append({
            **rec,
            "is_cached": rec["id"] in cached_set,
        })

    return {
        "recommended": curated,
        "cached_models": cached,
        "cache_dir": get_hf_cache_dir(),
    }


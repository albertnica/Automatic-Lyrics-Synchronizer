"""
server/routers/songs.py
Endpoints for inspecting audio tracks and streaming audio for Wavesurfer.js.

Why:
Allows the Web Studio to list available tracks, display track metadata,
and stream audio files directly to the browser waveform engine from any custom directory.
"""

import os
import sys
import asyncio
import subprocess
import logging
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from config import SONGS_FOLDER, LYRICS_FOLDER, LYRICS_DB_FOLDER
from modules import utils as mutils
from server.schemas import SongItem

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/songs", tags=["Songs"])


def _pick_directory_sync(initial_dir: Optional[str] = None) -> Optional[str]:
    """
    Open native Windows folder browser dialog via dedicated child GUI process.
    """
    start_dir = initial_dir if (initial_dir and os.path.isdir(initial_dir)) else os.getcwd()
    server_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    picker_script = os.path.join(server_dir, "folder_picker.py")

    try:
        res = subprocess.run(
            [sys.executable, picker_script, start_dir],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if res.returncode != 0:
            logger.warning(f"Picker script failed with code {res.returncode}: {res.stderr}")
        out = res.stdout.strip()
        if out and os.path.isdir(out):
            return os.path.normpath(out)
    except Exception as e:
        logger.warning(f"Folder picker error: {e}")

    return None


@router.post("/browse-folder")
@router.get("/browse-folder")
async def browse_folder(initial_dir: Optional[str] = Query(None, description="Initial directory to browse from")):
    """
    Trigger native OS folder browser dialog and return the selected directory path.
    """
    chosen = await asyncio.to_thread(_pick_directory_sync, initial_dir)
    if chosen:
        valid_exts = {".flac", ".mp3", ".wav", ".ogg", ".m4a"}
        track_count = sum(1 for f in os.listdir(chosen) if os.path.splitext(f)[1].lower() in valid_exts)
        return {
            "success": True,
            "path": chosen,
            "track_count": track_count,
            "canceled": False,
        }
    return {
        "success": True,
        "path": None,
        "track_count": 0,
        "canceled": True,
    }


def _resolve_folder(custom_folder: Optional[str]) -> str:
    """
    Resolve and validate a custom folder path, falling back to SONGS_FOLDER.
    """
    if custom_folder and custom_folder.strip():
        resolved = os.path.abspath(custom_folder.strip())
        if os.path.isdir(resolved):
            return resolved
    return os.path.abspath(SONGS_FOLDER)


@router.get("", response_model=List[SongItem])
async def list_songs(folder: Optional[str] = Query(None, description="Custom folder path to scan")) -> List[SongItem]:
    """
    List audio files inside the specified or default folder with metadata and LRC presence.
    """
    target_folder = _resolve_folder(folder)
    if not os.path.isdir(target_folder):
        return []

    items: List[SongItem] = []
    valid_exts = {".flac", ".mp3", ".wav", ".ogg", ".m4a"}

    for filename in sorted(os.listdir(target_folder)):
        ext = os.path.splitext(filename)[1].lower()
        if ext in valid_exts:
            full_path = os.path.join(target_folder, filename)
            base = os.path.splitext(filename)[0]

            # Check if synced LRC exists in custom folder, lyrics/ or .lyrics_db/
            has_lrc = False
            folder_lrc = os.path.join(target_folder, f"{base}.lrc")
            lrc_candidate = os.path.join(LYRICS_FOLDER, f"{base}.lrc")
            if os.path.isfile(folder_lrc):
                has_lrc = True
            elif os.path.isfile(lrc_candidate):
                has_lrc = True
            elif os.path.isdir(LYRICS_DB_FOLDER):
                for db_file in os.listdir(LYRICS_DB_FOLDER):
                    if db_file.lower().endswith(".lrc") and base.lower() in db_file.lower():
                        has_lrc = True
                        break

            # Metadata extraction
            info = mutils.extract_audio_info(full_path)

            items.append(
                SongItem(
                    filename=filename,
                    path=full_path,
                    title=info.get("title") or base,
                    artist=info.get("artist"),
                    album=info.get("album"),
                    duration=info.get("duration"),
                    has_lrc=has_lrc,
                )
            )

    return items


@router.get("/validate-folder")
async def validate_folder(path: str = Query(..., description="Folder path to validate")) -> Dict[str, Any]:
    """
    Validate whether a folder exists and count audio tracks inside it.
    """
    resolved = os.path.abspath(path.strip())
    exists = os.path.isdir(resolved)
    track_count = 0
    if exists:
        valid_exts = {".flac", ".mp3", ".wav", ".ogg", ".m4a"}
        try:
            track_count = sum(1 for f in os.listdir(resolved) if os.path.splitext(f)[1].lower() in valid_exts)
        except Exception:
            pass

    return {
        "path": resolved,
        "exists": exists,
        "track_count": track_count,
    }


@router.get("/stream/{filename}")
async def stream_audio(filename: str, folder: Optional[str] = Query(None, description="Source folder of the audio file")):
    """
    Stream an audio file directly to the browser for Wavesurfer.js waveform visualizer.
    """
    target_folder = _resolve_folder(folder)
    file_path = os.path.join(target_folder, filename)

    # Fallback to SONGS_FOLDER if not found in custom folder
    if not os.path.isfile(file_path):
        fallback_path = os.path.join(os.path.abspath(SONGS_FOLDER), filename)
        if os.path.isfile(fallback_path):
            file_path = fallback_path
        else:
            raise HTTPException(status_code=404, detail="Audio file not found")

    ext = os.path.splitext(filename)[1].lower()
    media_types = {
        ".flac": "audio/flac",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".ogg": "audio/ogg",
        ".m4a": "audio/mp4",
    }
    media_type = media_types.get(ext, "application/octet-stream")

    return FileResponse(file_path, media_type=media_type, filename=filename)

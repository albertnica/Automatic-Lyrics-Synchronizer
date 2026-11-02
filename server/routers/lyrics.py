"""
server/routers/lyrics.py
Endpoints for reading, parsing, and committing synchronized LRC lyrics files.

Why:
Supplies the dual-track LRC micro-editor with parsed timestamp cues and provides
atomic persistence back to both `lyrics/` and canonical `.lyrics_db/`.
"""

import os
import re
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query

from config import LYRICS_FOLDER, LYRICS_DB_FOLDER
from modules import db_manager
from server.schemas import LrcDocument, LyricLine, LrcSaveRequest

router = APIRouter(prefix="/api/lyrics", tags=["Lyrics"])

LRC_LINE_REGEX = re.compile(r"^\s*\[(\d+):(\d+(?:\.\d+)?)\](.*)$")


def parse_lrc_lines(raw_text: str) -> List[LyricLine]:
    """
    Parse raw LRC text into a structured list of LyricLine objects with numeric seconds.
    """
    lines: List[LyricLine] = []
    for line in raw_text.splitlines():
        line_clean = line.strip()
        match = LRC_LINE_REGEX.match(line_clean)
        if match:
            minutes = int(match.group(1))
            seconds = float(match.group(2))
            text = match.group(3).strip()
            total_seconds = round(minutes * 60 + seconds, 3)
            tag = f"[{minutes:02d}:{seconds:05.2f}]"
            lines.append(
                LyricLine(
                    time=total_seconds,
                    timestamp_tag=tag,
                    text=text,
                )
            )
    return lines


@router.get("/{track_base}", response_model=LrcDocument)
async def get_lyrics(track_base: str, folder: Optional[str] = Query(None, description="Optional folder to locate LRC")) -> LrcDocument:
    """
    Load an LRC file by song base name from custom folder, lyrics/ or .lyrics_db/.
    """
    safe_base = os.path.basename(track_base)
    lrc_path = os.path.join(LYRICS_FOLDER, f"{safe_base}.lrc")
    folder_lrc = os.path.join(folder, f"{safe_base}.lrc") if folder and os.path.isdir(folder) else None

    raw_content = ""
    exists = False

    if folder_lrc and os.path.isfile(folder_lrc):
        exists = True
        with open(folder_lrc, "r", encoding="utf-8", errors="replace") as f:
            raw_content = f.read()
    elif os.path.isfile(lrc_path):
        exists = True
        with open(lrc_path, "r", encoding="utf-8", errors="replace") as f:
            raw_content = f.read()
    elif os.path.isdir(LYRICS_DB_FOLDER):
        for fname in os.listdir(LYRICS_DB_FOLDER):
            if fname.lower().endswith(".lrc") and safe_base.lower() in fname.lower():
                candidate = os.path.join(LYRICS_DB_FOLDER, fname)
                exists = True
                with open(candidate, "r", encoding="utf-8", errors="replace") as f:
                    raw_content = f.read()
                break

    parsed_lines = parse_lrc_lines(raw_content) if raw_content else []

    return LrcDocument(
        track_base=safe_base,
        raw_content=raw_content,
        lines=parsed_lines,
        exists=exists,
    )


@router.put("/{track_base}", response_model=LrcDocument)
async def save_lyrics(
    track_base: str,
    payload: LrcSaveRequest,
    folder: Optional[str] = Query(None, description="Optional folder to save LRC alongside song")
) -> LrcDocument:
    """
    Commit edited LRC content to `lyrics/`, source folder (if present), and archive to canonical `.lyrics_db/`.
    """
    safe_base = os.path.basename(track_base)
    os.makedirs(LYRICS_FOLDER, exist_ok=True)
    lrc_path = os.path.join(LYRICS_FOLDER, f"{safe_base}.lrc")

    try:
        with open(lrc_path, "w", encoding="utf-8") as f:
            f.write(payload.raw_content)

        # Also write directly to custom source folder if provided
        if folder and os.path.isdir(folder):
            custom_lrc = os.path.join(folder, f"{safe_base}.lrc")
            with open(custom_lrc, "w", encoding="utf-8") as f:
                f.write(payload.raw_content)

        # Parse artist and title if available to copy to .lyrics_db
        if " - " in safe_base:
            parts = safe_base.split(" - ", 1)
            artist, title = parts[0].strip(), parts[1].strip()
            db_manager.copy_to_lyrics_db(lrc_path, title, artist)
        else:
            db_manager.copy_to_lyrics_db(lrc_path, safe_base, "Unknown Artist")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write LRC: {e}")

    parsed_lines = parse_lrc_lines(payload.raw_content)

    return LrcDocument(
        track_base=safe_base,
        raw_content=payload.raw_content,
        lines=parsed_lines,
        exists=True,
    )


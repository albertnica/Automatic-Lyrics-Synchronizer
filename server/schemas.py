"""
server/schemas.py
Pydantic V2 schemas for API requests, responses, and real-time state.

Why:
Enforces strict contract validation, response integrity, and self-documenting
OpenAPI documentation for the Web Studio frontend.
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class SongItem(BaseModel):
    """
    Representation of an audio file discovered in the songs folder.
    """
    filename: str = Field(..., description="Audio file base name including extension")
    path: str = Field(..., description="Absolute or relative file path")
    title: Optional[str] = Field(None, description="Extracted track title")
    artist: Optional[str] = Field(None, description="Extracted artist name")
    album: Optional[str] = Field(None, description="Extracted album name")
    duration: Optional[float] = Field(None, description="Track duration in seconds")
    has_lrc: bool = Field(False, description="True if a synchronized LRC exists in lyrics/ or .lyrics_db/")


class ModelInfo(BaseModel):
    """
    Summary of an ASR model candidate from Hugging Face or local cache.
    """
    id: str = Field(..., description="Repository model identifier, e.g. openai/whisper-large-v3-turbo")
    name: str = Field(..., description="Human-readable model name")
    description: str = Field("", description="Model size, speed, and capability summary")
    is_cached: bool = Field(False, description="Whether the model weights are already downloaded locally")
    is_recommended: bool = Field(False, description="Whether this is a curated default model")
    downloads: Optional[int] = Field(0, description="Hugging Face download count")
    likes: Optional[int] = Field(0, description="Hugging Face like count")


class ModelCatalogResponse(BaseModel):
    """
    Initial catalog response providing curated choices and local cache status.
    """
    recommended: List[ModelInfo]
    cached_models: List[str]
    cache_dir: str


class SyncRequest(BaseModel):
    """
    Payload to trigger a synchronization job.
    """
    mode: str = Field("folder", description="'folder' to process directory or 'file' for single track")
    folder_path: Optional[str] = Field(None, description="Custom directory containing audio tracks")
    file_path: Optional[str] = Field(None, description="Specific audio path when mode is 'file'")
    models: Optional[List[str]] = Field(None, description="List of ASR models in priority order")
    force_asr: bool = Field(False, description="Whether to bypass LRCLIB/DB and force multi-model ASR transcription")


class SyncTaskResponse(BaseModel):
    """
    Response acknowledging task creation.
    """
    task_id: str
    status: str
    message: str


class LyricLine(BaseModel):
    """
    Parsed line cue with second timestamp and text.
    """
    time: float = Field(..., description="Timestamp in seconds")
    timestamp_tag: str = Field(..., description="Original LRC format tag e.g. [01:23.45]")
    text: str = Field(..., description="Lyric text content")


class LrcDocument(BaseModel):
    """
    Full representation of an LRC lyric file for editor manipulation.
    """
    track_base: str
    raw_content: str
    lines: List[LyricLine]
    exists: bool


class LrcSaveRequest(BaseModel):
    """
    Payload for committing modified LRC content.
    """
    raw_content: str = Field(..., description="Full raw LRC formatted string")


class ShutdownResponse(BaseModel):
    """
    Response confirming initiation of studio server shutdown.
    """
    message: str = Field(..., description="Status message of the shutdown command")



"""
server/routers/sync.py
Endpoints for running synchronization tasks and streaming progress via SSE.

Why:
Allows the Web Studio to trigger synchronization non-blockingly and receive
live SSE updates without polling loops or UI freezing.
"""

import asyncio
import json
import uuid
import threading
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from modules.pipeline import LyricsSyncService
from server.schemas import SyncRequest, SyncTaskResponse

router = APIRouter(prefix="/api/sync", tags=["Synchronization"])

# Active and historical task storage
tasks: Dict[str, Dict[str, Any]] = {}


def _run_sync_task(
    task_id: str,
    audio_paths: Optional[list],
    models: Optional[list],
    folder_path: Optional[str] = None,
    force_asr: bool = False
) -> None:
    """
    Background worker executing LyricsSyncService in a dedicated thread.
    """
    task_data = tasks.get(task_id)
    if not task_data:
        return

    event_queue: asyncio.Queue = task_data["queue"]
    loop: asyncio.AbstractEventLoop = task_data["loop"]

    def progress_callback(event: Dict[str, Any]) -> None:
        # Push event to asyncio queue safely across threads
        asyncio.run_coroutine_threadsafe(event_queue.put(event), loop)

    service = LyricsSyncService(
        models=models,
        progress_callback=progress_callback,
        source_folder=folder_path,
        force_asr=force_asr,
    )
    try:
        task_data["status"] = "running"
        summary = service.process_tracks(audio_paths=audio_paths)
        task_data["status"] = "completed"
        task_data["summary"] = summary
        asyncio.run_coroutine_threadsafe(
            event_queue.put({"stage": "DONE", "message": "All processing complete.", "percent": 100}),
            loop,
        )
    except Exception as e:
        task_data["status"] = "failed"
        task_data["error"] = str(e)
        asyncio.run_coroutine_threadsafe(
            event_queue.put({"stage": "ERROR", "message": f"Pipeline failure: {e}", "percent": 100}),
            loop,
        )
    finally:
        # Sentinel to signal stream completion
        asyncio.run_coroutine_threadsafe(event_queue.put(None), loop)


@router.post("/run", response_model=SyncTaskResponse)
async def start_sync_task(payload: SyncRequest) -> SyncTaskResponse:
    """
    Start an asynchronous synchronization run on the default folder or a single audio track.
    """
    task_id = str(uuid.uuid4())[:8]
    loop = asyncio.get_running_loop()
    event_queue = asyncio.Queue()

    audio_paths = None
    if payload.mode == "file" and payload.file_path:
        audio_paths = [payload.file_path]

    tasks[task_id] = {
        "status": "pending",
        "queue": event_queue,
        "loop": loop,
        "summary": None,
        "error": None,
    }

    worker = threading.Thread(
        target=_run_sync_task,
        args=(task_id, audio_paths, payload.models, payload.folder_path, payload.force_asr),
        daemon=True,
    )
    worker.start()

    return SyncTaskResponse(
        task_id=task_id,
        status="started",
        message=f"Sync task initialized for mode='{payload.mode}'. Connect to SSE stream to observe progress.",
    )


@router.get("/stream/{task_id}")
async def stream_task_events(task_id: str):
    """
    Server-Sent Events (SSE) stream broadcasting live progress and log messages for a task.
    """
    task_data = tasks.get(task_id)
    if not task_data:
        raise HTTPException(status_code=404, detail="Task ID not found")

    event_queue: asyncio.Queue = task_data["queue"]

    async def event_generator():
        yield f"data: {json.dumps({'stage': 'CONNECTED', 'message': f'Connected to task {task_id}', 'percent': 0})}\n\n"
        while True:
            event = await event_queue.get()
            if event is None:
                # Task finished
                yield f"data: {json.dumps({'stage': 'CLOSED', 'message': 'Stream closed.', 'percent': 100})}\n\n"
                break
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/status/{task_id}")
async def get_task_status(task_id: str):
    """
    Query the state and final summary of a sync task.
    """
    task_data = tasks.get(task_id)
    if not task_data:
        raise HTTPException(status_code=404, detail="Task ID not found")

    return {
        "task_id": task_id,
        "status": task_data["status"],
        "summary": task_data["summary"],
        "error": task_data["error"],
    }


"""
server/main.py
Primary FastAPI Application for Automatic Lyrics Synchronizer Web Studio.

Why:
Coordinates REST endpoints, SSE streams, centralized error handling,
and static delivery of the lightweight two-page web studio.
"""

import os
import sys
import threading

# Reconfigure stdout/stderr to UTF-8 on Windows to prevent UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from server.schemas import ShutdownResponse
from server.routers import songs, models, sync, lyrics

app = FastAPI(
    title="Automatic Lyrics Synchronizer Studio",
    description="Decoupled API and Studio for ASR model discovery, audio synchronization, and real-time LRC editing.",
    version="2.0.0",
)

# CORS configuration for development and local parity
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Centralized global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Ensure all uncaught errors return a consistent JSON response.
    """
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "error_type": type(exc).__name__,
            "message": str(exc),
            "path": request.url.path,
        },
    )

# Include API Routers
app.include_router(songs.router)
app.include_router(models.router)
app.include_router(sync.router)
app.include_router(lyrics.router)


@app.post("/api/shutdown", response_model=ShutdownResponse, tags=["System"])
async def shutdown_studio_server() -> ShutdownResponse:
    """
    Terminate the background Web Studio server process cleanly from the UI.

    Why:
    Allows user to safely shut down the Python runtime when work is finished
    without hunting for background process IDs or command shells.
    """
    def _delayed_exit() -> None:
        os._exit(0)

    threading.Timer(0.35, _delayed_exit).start()
    return ShutdownResponse(message="Web Studio server stopped.")


# Resolve Web Directory
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

if os.path.isdir(WEB_DIR):
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    @app.get("/sync", include_in_schema=False)
    async def serve_sync_page():
        """Serve Page 1: Transcription and Model Setup."""
        index_file = os.path.join(WEB_DIR, "index.html")
        return FileResponse(index_file)

    @app.get("/editor", include_in_schema=False)
    async def serve_editor_page():
        """Serve Page 2: LRC Studio and Micro-Editor."""
        editor_file = os.path.join(WEB_DIR, "editor.html")
        return FileResponse(editor_file)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server.main:app", host="127.0.0.1", port=8585, reload=True)


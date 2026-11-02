"""
modules/logs_manager.py
Simple run-level logging utilities.

Responsibilities:
- Create timestamped run folders.
- Append per-song anchor entries to the main run log.
- Save small JSON objects for diagnostics.
"""

import os
import sys
from datetime import datetime
import json
from typing import Any, Dict, Optional
from config import LOGS_FOLDER, LOG_NAME, DEBUG

# Reconfigure stdout/stderr to UTF-8 on Windows to prevent UnicodeEncodeError with Asian/Cyrillic chars
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

# Simple logging helpers with encoding fault-tolerance
def dbg(msg: str):
    """
    Log a debug message when DEBUG is True.
    """
    if DEBUG:
        try:
            print("[DEBUG]", msg)
        except Exception:
            try:
                print("[DEBUG]", str(msg).encode("ascii", "replace").decode("ascii"))
            except Exception:
                pass

def info(msg: str):
    """
    Log an info-level message to stdout.
    """
    try:
        print("[INFO]", msg)
    except Exception:
        try:
            print("[INFO]", str(msg).encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass

def warn(msg: str):
    """
    Log a warning-level message to stdout.
    """
    try:
        print("[WARN]", msg)
    except Exception:
        try:
            print("[WARN]", str(msg).encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass


def make_run_folder() -> str:
    """
    Create and return a timestamped run folder inside LOGS_FOLDER.
    """
    os.makedirs(LOGS_FOLDER, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_folder = os.path.join(LOGS_FOLDER, ts)
    os.makedirs(run_folder, exist_ok=True)
    dbg(f"created run folder: {run_folder}")
    return run_folder

def log_path(run_folder: str) -> str:
    """
    Return the path to the main log file inside a run folder.
    """
    return os.path.join(run_folder, LOG_NAME)

def init_anchors_log(run_folder: Optional[str] = None) -> str:
    """
    Initialize the main run log file and return the run folder path.
    """
    try:
        if run_folder is None:
            run_folder = make_run_folder()
        os.makedirs(run_folder, exist_ok=True)
        path = log_path(run_folder)
        ts = datetime.now().isoformat(sep=" ", timespec="seconds")
        header = f"=== Run: {ts} ===\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(header)
        info(f"Main run log initialized at: {path}")
        return run_folder
    except Exception as e:
        dbg(f"init_anchors_log error: {e}")
        # Return a usable run folder even on partial failure.
        if run_folder is None:
            fallback = make_run_folder()
            return fallback
        return run_folder

def append_anchor_entry(run_folder: str, basename: str, status: str, extra: str = ""):
    """
    Append a tab-separated anchor entry (timestamp, basename, status, extra) to the main run log.
    """
    try:
        path = log_path(run_folder)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        ts = datetime.now().isoformat(sep=" ", timespec="seconds")
        safe_basename = str(basename)
        safe_status = str(status)
        safe_extra = str(extra) if extra else ""
        line = f"{ts}\t{safe_basename}\t{safe_status}"
        if safe_extra:
            line += f"\t{safe_extra}"
        line += "\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
        dbg(f"Appended main log entry: {safe_basename} | {safe_status} | {safe_extra}")
    except Exception as e:
        dbg(f"append_anchor_entry error: {e}")

def save_json(obj: Dict[str, Any], path: str):
    """
    Save a small JSON object to disk at the given path (creates parent folders).
    """
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        dbg(f"Saved JSON to: {path}")
    except Exception as e:
        dbg(f"save_json error: {e}")

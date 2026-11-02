"""
modules/pipeline.py
Decoupled Lyrics Synchronization Service.

Why:
Encapsulates end-to-end synchronization logic into an observable, reusable class
that can be invoked seamlessly by both the legacy CLI and the FastAPI backend,
supporting single-file and batch execution with real-time progress callbacks.
"""

import os
import sys
import shutil
import json
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable

# Reconfigure stdout/stderr to UTF-8 on Windows
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

from modules import utils as mutils
from modules import (
    logs_manager,
    db_manager,
    ingest,
    lyrics_retrieving,
    whisper_transcription,
    lyrics_checker,
    lrc_writer,
    decision_logic,
    lrclib_client,
)
from modules.logs_manager import init_anchors_log, append_anchor_entry, make_run_folder
from config import (
    LYRICS_FOLDER,
    TRANSCRIBE_MODELS,
    FALLBACK_TO_TRANSCRIPTION_ON_MISSING_LYRICS,
    SONGS_FOLDER,
)

logger = logging.getLogger(__name__)


class LyricsSyncService:
    """
    Orchestrates the multi-tier lyric retrieval, ASR transcription,
    anchor alignment, and canonical database archiving.
    """

    def __init__(
        self,
        models: Optional[List[str]] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        fallback_transcription: bool = FALLBACK_TO_TRANSCRIPTION_ON_MISSING_LYRICS,
        source_folder: Optional[str] = None,
        force_asr: bool = False,
    ) -> None:
        """
        Initialize the synchronization service.

        Args:
            models: Ordered list of Whisper models to test. Defaults to TRANSCRIBE_MODELS.
            progress_callback: Optional callback receiving structured event dicts.
            fallback_transcription: Whether to proceed with raw transcription if lyrics fail.
            source_folder: Optional source directory containing audio tracks. Defaults to SONGS_FOLDER.
            force_asr: Whether to bypass LRCLIB/DB and force multi-model ASR transcription.
        """
        self.models = models if models else TRANSCRIBE_MODELS[:]
        self.progress_callback = progress_callback
        self.fallback_transcription = fallback_transcription
        self.source_folder = source_folder if (source_folder and os.path.isdir(source_folder)) else SONGS_FOLDER
        self.force_asr = force_asr
        self.stats = {
            "total": 0,
            "processed": 0,
            "db_hits": 0,
            "lrclib_synced": 0,
            "plain_lyrics": 0,
            "whisper_runs": 0,
            "model_winners": {},
            "completed": 0,
            "failed": 0,
        }

    def _emit(self, stage: str, message: str, percent: int = 0, track: Optional[str] = None) -> None:
        """
        Dispatch a progress event to the registered listener if present.
        """
        event = {
            "stage": stage,
            "message": message,
            "percent": percent,
            "track": track,
            "stats": self.stats,
        }
        logger.info("[%s] %s (%d%%)", stage, message, percent)
        if self.progress_callback:
            try:
                self.progress_callback(event)
            except Exception as e:
                logger.debug("Progress callback failed: %s", e)

    def process_tracks(self, audio_paths: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Execute the synchronization pipeline on the provided audio files or default folder.

        Why:
        Unified entry point capable of processing either an individual track or a batch,
        dispatching status updates for the Web Studio SSE endpoint.

        Args:
            audio_paths: Optional list of audio file paths. If None, scans SONGS_FOLDER.

        Returns:
            Dictionary summary of the synchronization run.
        """
        run_folder = make_run_folder()
        init_anchors_log(run_folder)

        db_manager.initialize_folders()
        ingest.ingest_existing_lrcs_and_cleanup_songs(folder=self.source_folder)

        if audio_paths is None:
            flacs = mutils.find_flac_files(folder=self.source_folder)
        else:
            flacs = [p for p in audio_paths if os.path.isfile(p)]

        total_tracks = len(flacs)
        self.stats["total"] = total_tracks
        if total_tracks == 0:
            self._emit("IDLE", "No audio files found to process.", 100)
            return {"total": 0, "completed": 0, "run_folder": run_folder, "stats": self.stats}

        self._emit("INITIALIZING", f"Found {total_tracks} track(s) to process.", 5)

        results: List[Dict[str, Any]] = []

    def _process_single_track(
        self,
        fpath: str,
        idx: int,
        total_tracks: int,
        run_folder: str
    ) -> Optional[Dict[str, Any]]:
        """
        Execute the multi-tier retrieval, transcription, and alignment waterfall on a single track.

        Why:
        Isolates execution so unexpected exceptions on a single track do not abort batch processing.
        """
        basename = os.path.basename(fpath)
        flac_base = os.path.splitext(basename)[0]
        track_percent = int(10 + (idx - 1) / total_tracks * 85)

        self._emit("METADATA", f"Extracting metadata from {basename}...", track_percent, basename)

        info = mutils.extract_audio_info(fpath)
        artist = info.get("artist")
        title = info.get("title")
        album = info.get("album")
        duration = info.get("duration")

        # Tier 1: Canonical DB restore
        if not self.force_asr and artist and title:
            try:
                db_entry = db_manager.search_similar_in_db(title, artist)
                if db_entry:
                    dest = os.path.join(LYRICS_FOLDER, f"{flac_base}.lrc")
                    os.makedirs(LYRICS_FOLDER, exist_ok=True)
                    shutil.copy2(db_entry, dest)
                    append_anchor_entry(run_folder, basename, "restored_from_db", os.path.basename(db_entry))
                    self.stats["db_hits"] += 1
                    self.stats["completed"] += 1
                    self.stats["processed"] += 1
                    self._emit("RESTORED_DB", f"Restored canonical LRC from DB (ASR skipped): {os.path.basename(dest)}", track_percent, basename)
                    return {"track": basename, "status": "restored_db", "lrc": dest}
            except Exception as e:
                mutils.dbg(f"DB restore attempt failed: {e}")

        # Tier 2: Local .txt lyrics override
        local_txt_name = f"{flac_base}.txt"
        local_txt_path = os.path.join(self.source_folder, local_txt_name)
        lyrics_txt_path = None

        if os.path.exists(local_txt_path):
            dest_txt_path = os.path.join(run_folder, local_txt_name)
            try:
                shutil.move(local_txt_path, dest_txt_path)
                lyrics_txt_path = dest_txt_path
                self.stats["plain_lyrics"] += 1
                self._emit("LOCAL_TXT", f"Found local lyrics override: {local_txt_name}", track_percent, basename)
            except Exception:
                try:
                    shutil.copy2(local_txt_path, dest_txt_path)
                    os.remove(local_txt_path)
                    lyrics_txt_path = dest_txt_path
                    self.stats["plain_lyrics"] += 1
                except Exception:
                    pass

        # Tier 3: LRCLIB Native Client (Direct Synced & Plain Lyrics)
        if not lyrics_txt_path and artist and title:
            self._emit("LRCLIB", f"Querying LRCLIB for '{artist} - {title}'...", track_percent, basename)
            try:
                lrclib_data = lrclib_client.get_lyrics(title, artist, album, duration)
                if lrclib_data:
                    # If LRCLIB provides synced lyrics, write and archive directly (unless force_asr is requested)
                    synced_lrc = lrclib_data.get("syncedLyrics")
                    if synced_lrc and synced_lrc.strip():
                        if not self.force_asr:
                            dest = os.path.join(LYRICS_FOLDER, f"{flac_base}.lrc")
                            os.makedirs(LYRICS_FOLDER, exist_ok=True)
                            with open(dest, "w", encoding="utf-8") as lf:
                                lf.write(synced_lrc)
                            db_manager.copy_to_lyrics_db(dest, title, artist)
                            append_anchor_entry(run_folder, basename, "lrclib_synced_hit", "")
                            self.stats["lrclib_synced"] += 1
                            self.stats["completed"] += 1
                            self.stats["processed"] += 1
                            self._emit("LRCLIB_SYNCED", f"Instantly applied LRCLIB synced lyrics (ASR skipped): {basename}", track_percent, basename)
                            return {"track": basename, "status": "lrclib_synced", "lrc": dest}
                        else:
                            self._emit("LRCLIB_SYNCED", f"Found LRCLIB synced lyrics, but Force ASR is active: proceeding with multi-model evaluation.", track_percent, basename)

                    # If plain lyrics provided, use them for alignment
                    plain_lyrics = lrclib_data.get("plainLyrics")
                    if not plain_lyrics and synced_lrc:
                        import re
                        plain_lyrics = "\n".join([re.sub(r"^\[\d+:\d+(?:\.\d+)?\]", "", l).strip() for l in synced_lrc.splitlines() if l.strip()])

                    if plain_lyrics and plain_lyrics.strip():
                        outname = mutils.sanitize_filename(f"{title} - {artist}__lrclib.txt")
                        outpath = os.path.join(run_folder, outname)
                        with open(outpath, "w", encoding="utf-8") as pf:
                            pf.write(plain_lyrics)
                        lyrics_txt_path = outpath
                        self.stats["plain_lyrics"] += 1
                        self._emit("LRCLIB_PLAIN", f"Retrieved plain lyrics from LRCLIB for ASR alignment.", track_percent, basename)
            except Exception as e:
                mutils.dbg(f"LRCLIB query failed: {e}")

        # Tier 4: Genius API fallback
        if not lyrics_txt_path:
            self._emit("GENIUS", f"Querying Genius API fallback...", track_percent, basename)
            try:
                lyrics_txt_path = lyrics_retrieving.retrieve_for_flac(fpath, run_folder=run_folder)
                if lyrics_txt_path:
                    self.stats["plain_lyrics"] += 1
            except Exception as e:
                mutils.dbg(f"Genius retrieval error for {basename}: {e}")

        if not lyrics_txt_path and not self.fallback_transcription:
            append_anchor_entry(run_folder, basename, "lyrics_retrieval_failed", "")
            self.stats["failed"] += 1
            self.stats["processed"] += 1
            self._emit("SKIPPED", f"No lyrics obtained for {basename}; skipped.", track_percent, basename)
            return {"track": basename, "status": "no_lyrics_skipped"}

        # Tier 5: Multi-Model Transcription & Alignment
        self.stats["whisper_runs"] += 1
        self._emit("TRANSCRIBING", f"Starting multi-model ASR evaluation with: {self.models}...", track_percent, basename)

        def status_hook(m_name: str, status_code: str, details: str = ""):
            if status_code == "start":
                self._emit("MODEL_RUN", f"Evaluating [{m_name}] on '{basename}'...", track_percent, basename)
            elif status_code == "success":
                self._emit("MODEL_SUCCESS", f"[{m_name}] Transcription complete {details}", track_percent, basename)
            elif status_code == "failed":
                self._emit("MODEL_WARN", f"[{m_name}] Failed: {details}", track_percent, basename)

        trans_results = whisper_transcription.transcribe_iter_models(
            fpath,
            run_folder=run_folder,
            models=self.models,
            status_hook=status_hook
        )
        if not trans_results:
            append_anchor_entry(run_folder, basename, "all_models_failed", "")
            self._emit("ERROR", f"All ASR models failed for {basename}", track_percent, basename)
            return {"track": basename, "status": "transcription_failed"}

        model_evals = []
        for tr in trans_results:
            model_name = tr.get("model")
            out = tr.get("result")
            safe_model_tag = model_name.replace("/", "_")
            tmp_jsonl = os.path.join(run_folder, f"{flac_base}__{safe_model_tag}.jsonl")
            try:
                segs = []
                if isinstance(out, dict):
                    if "chunks" in out and isinstance(out["chunks"], list):
                        segs = out["chunks"]
                    elif "segments" in out and isinstance(out["segments"], list):
                        segs = out["segments"]
                    else:
                        segs = [{"start": 0.0, "end": 9999.0, "text": out.get("text", "")}]
                elif isinstance(out, list):
                    segs = out
                else:
                    segs = [{"start": 0.0, "end": 9999.0, "text": str(out)}]

                with open(tmp_jsonl, "w", encoding="utf-8") as fh:
                    for s in segs:
                        fh.write(json.dumps(s, ensure_ascii=False) + "\n")

                if lyrics_txt_path:
                    segments = lyrics_checker.read_segments_flex(Path(tmp_jsonl))
                    lyrics_lines, non_empty_idxs = lyrics_checker.load_lyrics_lines(Path(lyrics_txt_path))
                    line_assignments, seg_to_lines = lyrics_checker.assign_segments_to_lines(
                        segments, lyrics_lines, non_empty_idxs, max_window=3, threshold=0.50
                    )
                    line_ts = lyrics_checker.compute_line_timestamps(line_assignments, segments, lyrics_lines)
                    line_ts = lyrics_checker.interpolate_missing_timestamps(line_ts, lyrics_lines)
                    line_ts = lyrics_checker.fill_null_timestamps_with_neighbors(line_ts)

                    out_jsonl = os.path.join(run_folder, f"{flac_base}__{safe_model_tag}_aligned.jsonl")
                    out_log = os.path.join(run_folder, f"{flac_base}__{safe_model_tag}_aligned_log.csv")
                    entries, logs = lyrics_checker.group_lines_by_start_and_emit(
                        lyrics_lines,
                        line_ts,
                        {i: line_assignments.get(i, []) for i in range(len(lyrics_lines))},
                        segments[0]["audio_path"] if segments else None,
                        Path(out_jsonl),
                        Path(out_log),
                    )
                else:
                    out_jsonl = tmp_jsonl
                    try:
                        with open(tmp_jsonl, "r", encoding="utf-8") as fh:
                            entries = len([1 for _ in fh if _.strip()])
                    except Exception:
                        entries = 0

                model_evals.append({
                    "model": model_name,
                    "raw_jsonl": tmp_jsonl,
                    "aligned_jsonl": out_jsonl,
                    "entries_count": entries,
                })
            except Exception as e:
                mutils.dbg(f"Error evaluating model {model_name}: {e}")
                model_evals.append({
                    "model": model_name,
                    "raw_jsonl": tmp_jsonl,
                    "aligned_jsonl": None,
                    "entries_count": 0,
                })

        # Scoring and model selection
        self._emit("ALIGNING", "Evaluating alignment candidate scores...", track_percent, basename)
        enriched = decision_logic.compute_scores(model_evals, lyrics_txt_path if lyrics_txt_path else None)
        fallback_mode = (lyrics_txt_path is None and self.fallback_transcription)
        best = decision_logic.choose_best_model(enriched, fallback_to_transcription=fallback_mode)

        if not best:
            append_anchor_entry(run_folder, basename, "no_best_model", "")
            self.stats["failed"] += 1
            self.stats["processed"] += 1
            return {"track": basename, "status": "no_best_model"}

        chosen_model = best.get("model")
        chosen_score = best.get("_decision_score", 0.0)
        self.stats["model_winners"][chosen_model] = self.stats["model_winners"].get(chosen_model, 0) + 1
        append_anchor_entry(run_folder, basename, f"chosen:{chosen_model}", f"score={chosen_score:.3f}")

        # Write and post-process final LRC
        try:
            best_jsonl = best.get("aligned_jsonl")
            if best_jsonl and os.path.exists(best_jsonl):
                lrc_out = os.path.join(LYRICS_FOLDER, f"{flac_base}.lrc")
                entries = lrc_writer.read_jsonl(best_jsonl)
                lrc_writer.write_lrc(entries, lrc_out)

                try:
                    from modules.lrc_postprocess import split_capitalized_intralines, append_trailing_empty_line
                    split_capitalized_intralines(lrc_out, out_path=None, fallback_dur=5.0)
                    append_trailing_empty_line(lrc_out, extra_seconds=20.0, out_path=None)
                except Exception as e:
                    mutils.dbg(f"Postprocess warning: {e}")

                if artist and title:
                    db_manager.copy_to_lyrics_db(lrc_out, title, artist)

                self.stats["completed"] += 1
                self.stats["processed"] += 1
                self._emit("COMPLETED_TRACK", f"Generated synchronized LRC ({chosen_model}): {os.path.basename(lrc_out)}", track_percent, basename)
                return {"track": basename, "status": "generated", "model": chosen_model, "lrc": lrc_out}
        except Exception as e:
            append_anchor_entry(run_folder, basename, "write_lrc_failed", str(e))
            self.stats["failed"] += 1
            self.stats["processed"] += 1
            return {"track": basename, "status": "write_lrc_failed", "error": str(e)}

        return None

    def process_tracks(self, audio_paths: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Process multiple audio tracks in sequence.
        """
        if audio_paths:
            flacs = [p for p in audio_paths if os.path.exists(p)]
        else:
            flacs = mutils.find_flac_files(self.source_folder)

        total_tracks = len(flacs)
        self.stats["total_tracks"] = total_tracks

        # Prepare folders & ingest local LRCs
        ingest.prepare_folders()
        ingest.ingest_existing_lrcs(self.source_folder)

        # Initialize per-run logger
        run_folder = make_run_folder()
        init_anchors_log(run_folder)

        self._emit("INITIALIZING", f"Found {total_tracks} track(s) to process.", 5)

        results: List[Dict[str, Any]] = []

        for idx, fpath in enumerate(flacs, start=1):
            basename = os.path.basename(fpath)
            track_percent = int(10 + (idx - 1) / total_tracks * 85)
            try:
                res = self._process_single_track(fpath, idx, total_tracks, run_folder)
                if res:
                    results.append(res)
            except Exception as track_err:
                mutils.warn(f"Pipeline error on track '{basename}': {track_err}")
                append_anchor_entry(run_folder, basename, "track_error", str(track_err))
                self.stats["failed"] += 1
                self.stats["processed"] += 1
                self._emit("WARN", f"Skipping {basename} due to error: {track_err}", track_percent, basename)
                results.append({"track": basename, "status": "error", "error": str(track_err)})

        # Cleanup residual .txt in source folder
        if os.path.exists(self.source_folder):
            for entry in os.listdir(self.source_folder):
                if entry.lower().endswith(".txt"):
                    src = os.path.join(self.source_folder, entry)
                    dst = os.path.join(run_folder, entry)
                    try:
                        shutil.move(src, dst)
                    except Exception:
                        pass

        self._emit("FINISHED", f"Completed processing all tracks. Results: {len(results)} item(s).", 100)
        return {
            "total": total_tracks,
            "processed": len(results),
            "results": results,
            "run_folder": run_folder,
        }


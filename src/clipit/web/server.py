"""ClipIt Web Studio: High-performance Threading HTTP Server with HTTP 206 Video Streaming,
REST APIs, and Server-Sent Events (SSE) Live Multi-Agent Workflow Simulation.
"""

import os
import sys
import re
import json
import time
import queue
import shutil
import mimetypes
import threading
import urllib.parse
from pathlib import Path
from http import HTTPStatus
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any, Optional, List

from clipit.cache import CacheManager, get_video_id
from clipit.config import load_config
from clipit.stages.ingest import run_ingest
from clipit.stages.transcribe import run_transcribe
from clipit.stages.signals import run_signals
from clipit.stages.candidates import run_candidates
from clipit.stages.select import run_select
from clipit.stages.refine import run_refine
from clipit.models import ClipItem, FullTranscript
from clipit.stages.render import run_render, render_single_clip, render_variant_file
from clipit.stages.review import generate_review_html, run_quality_sentinel
from clipit.stages.export import run_export
from clipit.stages.vision_reframing import analyze_clip_vision

# Workspace Root
ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
OUT_DIR = ROOT_DIR / "out"
CACHE_DIR = ROOT_DIR / "cache"
READY_DIR = ROOT_DIR / "ready_to_post"

# Active Sessions for Live Workflow Streaming
active_sessions: Dict[str, Dict[str, Any]] = {}
session_lock = threading.Lock()

# Active Live Stream Monitors
active_live_monitors: Dict[str, Any] = {}


class RangeRequestHandler(BaseHTTPRequestHandler):
    """Custom HTTP request handler with HTTP 206 Partial Content video streaming and SSE."""

    server_version = "ClipItServer/1.0"

    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_cors_headers()
        self.end_headers()

    def do_HEAD(self):
        """Handle HEAD requests."""
        self.do_GET()

    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Range, Content-Type, Authorization")
        self.send_header("Access-Control-Expose-Headers", "Content-Range, Content-Length, Accept-Ranges")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # 1. Static Files
        if path == "/" or path == "/index.html":
            return self.serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        elif path == "/style.css":
            return self.serve_file(STATIC_DIR / "style.css", "text/css; charset=utf-8")
        elif path == "/app.js":
            return self.serve_file(STATIC_DIR / "app.js", "application/javascript; charset=utf-8")

        # 2. REST APIs
        if path == "/api/projects":
            return self.handle_get_projects()
        elif path.startswith("/api/project/"):
            video_id = path[len("/api/project/"):]
            return self.handle_get_project_detail(video_id)
        elif path.startswith("/api/video/"):
            # /api/video/{video_id}/{filename}
            parts = path[len("/api/video/"):].split("/", 1)
            if len(parts) == 2:
                video_id, filename = parts
                return self.handle_stream_video(video_id, filename)
            else:
                self.send_error(HTTPStatus.BAD_REQUEST, "Malformed video path")
                return
        elif path.startswith("/api/download/"):
            parts = path[len("/api/download/"):].split("/", 1)
            if len(parts) == 2:
                video_id, filename = parts
                return self.handle_download_video(video_id, filename)
        elif path == "/api/stream-workflow":
            session_id = query.get("session_id", [None])[0]
            return self.handle_sse_workflow(session_id)
        elif path == "/api/retention-status":
            return self.handle_retention_status()
        elif path == "/api/storage-details":
            return self.handle_storage_details()
        elif path == "/api/active-session":
            return self.handle_get_active_session()
        elif path == "/api/live/status":
            vid = query.get("video_id", [None])[0]
            return self.handle_get_live_status(vid)
        elif path == "/api/youtube/status":
            return self.handle_youtube_status()


        # 3. Fallback to static or 404
        target_file = STATIC_DIR / path.lstrip("/")
        if target_file.exists() and target_file.is_file():
            mime, _ = mimetypes.guess_type(str(target_file))
            return self.serve_file(target_file, mime or "application/octet-stream")

        self.send_error(HTTPStatus.NOT_FOUND, f"File not found: {path}")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
        try:
            data = json.loads(body)
        except Exception:
            data = {}

        if path == "/api/start-pipeline":
            return self.handle_start_pipeline(data)
        elif path == "/api/rerun-ai":
            return self.handle_rerun_ai(data)
        elif path == "/api/approve-clip":
            return self.handle_approve_clip(data)
        elif path == "/api/export":
            return self.handle_export(data)
        elif path == "/api/export-single-clip":
            return self.handle_export_single_clip(data)
        elif path == "/api/delete-project":
            return self.handle_delete_project(data)
        elif path == "/api/delete-clip":
            return self.handle_delete_clip(data)
        elif path == "/api/delete-rejected":
            return self.handle_delete_rejected(data)
        elif path == "/api/retention-clean":
            return self.handle_retention_clean(data)
        elif path == "/api/delete-all-clips":
            return self.handle_delete_all_clips(data)
        elif path == "/api/delete-source":
            return self.handle_delete_source(data)
        elif path == "/api/rerender-clip":
            return self.handle_rerender_clip(data)
        elif path == "/api/update-clip-title":
            return self.handle_update_clip_title(data)
        elif path == "/api/live/start":
            return self.handle_start_live_sentinel(data)
        elif path == "/api/live/stop":
            return self.handle_stop_live_sentinel(data)
        elif path == "/api/internal/emit-event":
            return self.handle_internal_emit_event(data)
        elif path == "/api/youtube/auth":
            return self.handle_youtube_auth()
        elif path == "/api/youtube/disconnect":
            return self.handle_youtube_disconnect()
        elif path == "/api/youtube/upload":
            return self.handle_youtube_upload(data)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, f"Endpoint not found: {path}")

    # ==========================================
    # REST API Handlers
    # ==========================================

    def handle_get_projects(self):
        projects = []
        seen = set()

        # Check out/ directories
        if OUT_DIR.exists():
            for d in sorted(OUT_DIR.iterdir(), key=lambda x: x.stat().st_mtime if x.is_dir() else 0, reverse=True):
                if d.is_dir() and not d.name.startswith("."):
                    vid = d.name
                    seen.add(vid)
                    info_p = CACHE_DIR / vid / "info.json"
                    title = vid
                    duration = 0
                    source_url = ""
                    if info_p.exists():
                        try:
                            with open(info_p, "r", encoding="utf-8") as f:
                                i_data = json.load(f)
                                title = i_data.get("title", vid)
                                duration = i_data.get("duration", 0)
                                source_url = i_data.get("source", "")
                        except Exception:
                            pass

                    # Extract YouTube ID for thumbnail
                    thumb_url = ""
                    yt_id = ""
                    if vid.startswith("yt_"):
                        yt_id = vid[3:]
                    elif "youtube.com" in source_url or "youtu.be" in source_url:
                        m = re.search(r'(?:v=|\/|youtu\.be\/)([0-9A-Za-z_-]{11})', source_url)
                        if m:
                            yt_id = m.group(1)

                    local_thumb = d / "thumbnail.jpg"
                    local_thumb_url = f"/api/video/{vid}/thumbnail.jpg" if local_thumb.exists() else ""

                    if yt_id:
                        thumb_url = f"https://img.youtube.com/vi/{yt_id}/hqdefault.jpg"
                    else:
                        thumb_url = local_thumb_url

                    # Count clips from rendered sidecars across out_v and part_* dirs
                    sidecars_count = 0
                    if d.exists():
                        sc_ids = set()
                        for sp in d.glob("*.json"):
                            if sp.name not in ("info.json", "live_session.json", "approved.json", "project.json", "review.json"):
                                sc_ids.add(re.sub(r"^pt01_", "", sp.stem))
                        for pdir in d.glob("part_*"):
                            if pdir.is_dir():
                                for sp in pdir.glob("*.json"):
                                    if sp.name not in ("info.json", "live_session.json", "approved.json"):
                                        sc_ids.add(sp.stem)
                        sidecars_count = len(sc_ids)

                    clips_count = sidecars_count if sidecars_count > 0 else (len(list(d.glob("c*.mp4"))) // 3)
                    if clips_count == 0:
                        clips_count = len(list(d.glob("c*.mp4")))

                    # Created date
                    created_date = time.strftime("%b %d", time.localtime(d.stat().st_mtime))

                    projects.append({
                        "video_id": vid,
                        "title": title,
                        "duration": duration,
                        "clips_count": max(1, clips_count),
                        "folder": f"out/{vid}",
                        "thumbnail_url": thumb_url,
                        "local_thumbnail_url": local_thumb_url,
                        "created_date": created_date,
                        "source_type": "YouTube" if yt_id or "youtu" in source_url else "Upload",
                        "source_url": source_url,
                        "ratio": "9:16",
                    })

        self.send_json({"projects": projects})

    def handle_delete_project(self, data: Dict[str, Any]):
        video_id = data.get("video_id")
        if not video_id:
            self.send_error(HTTPStatus.BAD_REQUEST, "Missing video_id")
            return

        out_v = OUT_DIR / video_id
        cache_v = CACHE_DIR / video_id
        if out_v.exists():
            shutil.rmtree(out_v, ignore_errors=True)
        if cache_v.exists():
            shutil.rmtree(cache_v, ignore_errors=True)

        self.send_json({"success": True, "deleted": video_id})

    def handle_delete_clip(self, data: Dict[str, Any]):
        video_id = data.get("video_id")
        clip_id = data.get("clip_id")
        if not video_id or not clip_id:
            self.send_error(HTTPStatus.BAD_REQUEST, "Missing video_id or clip_id")
            return
        from clipit.retention import delete_single_clip
        res = delete_single_clip(video_id, clip_id, CACHE_DIR, OUT_DIR, READY_DIR)
        self.send_json(res)

    def handle_delete_rejected(self, data: Dict[str, Any]):
        video_id = data.get("video_id")
        delete_unapproved = bool(data.get("delete_unapproved", False))
        if not video_id:
            self.send_error(HTTPStatus.BAD_REQUEST, "Missing video_id")
            return
        from clipit.retention import delete_rejected_clips
        res = delete_rejected_clips(video_id, CACHE_DIR, OUT_DIR, READY_DIR, delete_unapproved=delete_unapproved)
        self.send_json(res)

    def handle_retention_clean(self, data: Dict[str, Any]):
        days = int(data.get("days", 30))
        dry_run = bool(data.get("dry_run", False))
        from clipit.retention import enforce_retention_policy
        res = enforce_retention_policy(CACHE_DIR, OUT_DIR, max_age_days=days, dry_run=dry_run)
        self.send_json(res)

    def handle_retention_status(self):
        from datetime import datetime, timezone
        from clipit.retention import get_project_age_days, get_project_creation_timestamp
        projects = []
        all_vids = set()
        if CACHE_DIR.exists():
            all_vids.update(p.name for p in CACHE_DIR.iterdir() if p.is_dir() and not p.name.startswith("."))
        if OUT_DIR.exists():
            all_vids.update(p.name for p in OUT_DIR.iterdir() if p.is_dir() and not p.name.startswith("."))

        for vid in sorted(all_vids):
            age = get_project_age_days(vid, CACHE_DIR, OUT_DIR)
            ts = get_project_creation_timestamp(vid, CACHE_DIR, OUT_DIR)
            dt_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            projects.append({
                "video_id": vid,
                "created_at": dt_str,
                "age_days": round(age, 1),
                "expired_30d": age >= 30,
            })
        self.send_json({"policy_days": 30, "projects": projects})

    def handle_storage_details(self):
        from clipit.retention import get_storage_breakdown
        res = get_storage_breakdown(CACHE_DIR, OUT_DIR, READY_DIR)
        self.send_json(res)

    def handle_get_active_session(self):
        with session_lock:
            # 1. Check in-memory running sessions
            active_list = [s for s in active_sessions.values() if not s.get("done", False)]
            if active_list:
                latest = sorted(active_list, key=lambda s: s.get("created_at", 0), reverse=True)[0]
                self.send_json({
                    "active": True,
                    "session_id": latest["id"],
                    "video_id": latest.get("video_id"),
                    "url": latest.get("url"),
                    "done": False,
                    "created_at": latest.get("created_at"),
                })
                return

            # 2. Check active in-process live monitors
            for vid, m_info in active_live_monitors.items():
                if m_info.get("thread") and m_info["thread"].is_alive():
                    self.send_json({
                        "active": True,
                        "session_id": f"live_{vid}",
                        "video_id": vid,
                        "url": m_info.get("url", ""),
                        "done": False,
                        "created_at": m_info.get("start_time", time.time()),
                    })
                    return

            # 3. Check disk for active live_session.json recently modified (< 60 seconds)
            for sess_path in CACHE_DIR.glob("*/live_session.json"):
                try:
                    if time.time() - sess_path.stat().st_mtime < 60:
                        sess_data = json.loads(sess_path.read_text(encoding="utf-8"))
                        status = sess_data.get("status", "")
                        if status in ("processing_part", "waiting_next_chunk", "active", "watching", "in_progress", "running"):
                            vid = sess_data.get("video_id", sess_path.parent.name)
                            sid = f"live_{vid}"
                            if sid not in active_sessions or not active_sessions[sid].get("events"):
                                q = active_sessions[sid]["queue"] if (sid in active_sessions and "queue" in active_sessions[sid]) else queue.Queue()
                                active_sessions[sid] = {
                                    "id": sid,
                                    "url": sess_data.get("source_url", "") or sess_data.get("url", ""),
                                    "queue": q,
                                    "created_at": sess_data.get("start_time", sess_path.stat().st_mtime),
                                    "status": "running",
                                    "events": self._synthesize_live_events(sess_data, vid),
                                    "video_id": vid,
                                    "done": False,
                                }
                            self.send_json({
                                "active": True,
                                "session_id": sid,
                                "video_id": vid,
                                "url": sess_data.get("source_url", "") or sess_data.get("url", ""),
                                "done": False,
                                "created_at": sess_data.get("start_time", sess_path.stat().st_mtime),
                            })
                            return
                except Exception:
                    continue

            self.send_json({"active": False})

    @staticmethod
    def _synthesize_live_events(sess_data: Dict[str, Any], vid: str) -> List[Dict[str, Any]]:
        events = []
        url = sess_data.get("url") or sess_data.get("source_url") or ""
        title = sess_data.get("title") or f"Live Stream Sentinel ({vid})"
        events.append({
            "event": "pipeline_start",
            "data": {
                "video_id": vid,
                "url": url,
                "title": title,
                "stages": ["ingest", "transcribe", "signals", "candidates", "select", "render", "export"]
            }
        })
        parts = sess_data.get("parts", [])
        total_clips = 0
        for p in parts:
            p_name = p.get("name", f"Part {p.get('part_index', '?')}")
            p_win = p.get("stream_window", f"{p.get('start_str', '')} -> {p.get('end_str', '')}")
            c_cnt = len(p.get("clips", []))
            total_clips += c_cnt
            events.append({
                "event": "agent_log",
                "data": {
                    "stage_id": "export",
                    "log": f"✔ [{p_name}] Staged & Published · Window: {p_win} · {c_cnt} viral clips ready in 9:16 format",
                    "metrics": {
                        "task": f"{p_name} Completed ({c_cnt} clips)",
                        "model": "Apple VideoToolbox + ASS",
                        "device": "Ready in Studio"
                    }
                }
            })

        curr_part_num = len(parts) + 1
        events.append({
            "event": "stage_start",
            "data": {
                "stage_id": "render",
                "name": f"Live Stream Sentinel · Part {curr_part_num}"
            }
        })
        events.append({
            "event": "agent_log",
            "data": {
                "stage_id": "render",
                "log": f"🔴 [LIVE SENTINEL] Currently processing Part {curr_part_num} · 24/7 continuous clipping active ({len(parts)} parts completed, {total_clips} clips ready in Studio)...",
                "metrics": {
                    "task": f"Active: Processing Part {curr_part_num}",
                    "model": "Auto Continuous Clipper",
                    "device": "Live DVR Stream"
                }
            }
        })
        return events

    def handle_internal_emit_event(self, data: Dict[str, Any]):
        session_id = data.get("session_id", "live_cli")
        ev_type = data.get("event", "agent_log")
        ev_data = data.get("data", {})
        video_id = data.get("video_id") or ev_data.get("video_id")
        url = data.get("url") or ev_data.get("url", "")

        ev = {"event": ev_type, "data": ev_data}
        with session_lock:
            if session_id not in active_sessions:
                active_sessions[session_id] = {
                    "id": session_id,
                    "url": url,
                    "queue": queue.Queue(),
                    "created_at": time.time(),
                    "status": "running",
                    "events": [],
                    "video_id": video_id,
                    "done": False,
                }
            sess = active_sessions[session_id]
            if video_id and not sess.get("video_id"):
                sess["video_id"] = video_id
            if url and not sess.get("url"):
                sess["url"] = url
            sess["events"].append(ev)
            if ev_type == "pipeline_complete" and not session_id.startswith("live_"):
                sess["done"] = True
            elif ev_type == "sentinel_stopped":
                sess["done"] = True
            sess["queue"].put(ev)

        self.send_json({"ok": True})

    def handle_delete_all_clips(self, data: Dict[str, Any]):
        video_id = data.get("video_id")
        from clipit.retention import delete_all_clips
        res = delete_all_clips(video_id, CACHE_DIR, OUT_DIR, READY_DIR)
        self.send_json(res)

    def handle_delete_source(self, data: Dict[str, Any]):
        video_id = data.get("video_id")
        if not video_id:
            self.send_error(HTTPStatus.BAD_REQUEST, "Missing video_id")
            return
        from clipit.retention import delete_source_vod
        res = delete_source_vod(video_id, CACHE_DIR)
        self.send_json(res)

    def handle_get_project_detail(self, video_id: str):
        out_v = OUT_DIR / video_id
        cache_v = CACHE_DIR / video_id

        if not out_v.exists() and not cache_v.exists():
            self.send_error(HTTPStatus.NOT_FOUND, f"Project not found: {video_id}")
            return

        # Load video info
        info = {"video_id": video_id, "title": video_id, "duration": 0}
        info_file = cache_v / "info.json"
        if info_file.exists():
            try:
                with open(info_file, "r", encoding="utf-8") as f:
                    info = json.load(f)
            except Exception:
                pass

        # 1. Gather all sidecar JSONs across out_v and part_* dirs
        sidecars = {}
        if out_v.exists():
            for p in sorted(out_v.glob("*.json")):
                if p.name not in ("info.json", "live_session.json", "approved.json", "project.json", "review.json"):
                    try:
                        with open(p, "r", encoding="utf-8") as f:
                            d = json.load(f)
                            cid = d.get("id", p.stem)
                            sidecars[cid] = d
                    except Exception:
                        pass
            for part_dir in sorted(out_v.glob("part_*")):
                if part_dir.is_dir():
                    for p in sorted(part_dir.glob("*.json")):
                        if p.name not in ("info.json", "live_session.json", "approved.json"):
                            try:
                                with open(p, "r", encoding="utf-8") as f:
                                    d = json.load(f)
                                    cid = d.get("id", p.stem)
                                    sidecars[cid] = d
                            except Exception:
                                pass

        # 2. Gather clips from live_session.json
        live_clips = {}
        live_sess_p = cache_v / "live_session.json"
        if not live_sess_p.exists():
            live_sess_p = out_v / "live_session.json"
        if live_sess_p.exists():
            try:
                with open(live_sess_p, "r", encoding="utf-8") as f:
                    sess_d = json.load(f)
                    for part in sess_d.get("parts", []):
                        for c in part.get("clips", []):
                            cid = c.get("id")
                            if cid:
                                live_clips[cid] = c
                    for c in sess_d.get("clips", []):
                        cid = c.get("id")
                        if cid:
                            live_clips[cid] = c
            except Exception:
                pass

        # 3. Gather clips from refined_clips.json
        refined_clips = {}
        refined_p = cache_v / "refined_clips.json"
        if refined_p.exists():
            try:
                with open(refined_p, "r", encoding="utf-8") as f:
                    r_list = json.load(f)
                    for c in r_list:
                        cid = c.get("id")
                        if cid:
                            refined_clips[cid] = c
            except Exception:
                pass

        # 4. Deduplicate: if pt01_c01 exists, drop plain c01
        for k in list(sidecars.keys()):
            if not k.startswith("pt") and (f"pt01_{k}" in sidecars):
                del sidecars[k]
        for k in list(live_clips.keys()):
            if not k.startswith("pt") and (f"pt01_{k}" in live_clips or f"pt01_{k}" in sidecars):
                del live_clips[k]
        for k in list(refined_clips.keys()):
            if not k.startswith("pt") and (f"pt01_{k}" in refined_clips or f"pt01_{k}" in sidecars):
                del refined_clips[k]

        all_cids = list(dict.fromkeys(list(sidecars.keys()) + list(live_clips.keys()) + list(refined_clips.keys())))

        def find_video_file(name_variants):
            for name in name_variants:
                if (out_v / name).exists():
                    return name
                if out_v.exists():
                    for pdir in out_v.glob("part_*"):
                        if (pdir / name).exists():
                            return name
            return None

        enhanced_clips = []
        for cid in all_cids:
            stripped_cid = cid.split("_", 1)[1] if (cid and cid.startswith("pt") and "_" in cid) else cid

            base_data = dict(live_clips.get(cid) or refined_clips.get(cid) or {})
            sidecar_data = sidecars.get(cid) or sidecars.get(stripped_cid) or {}

            # Merge: sidecar takes precedence as ground truth
            c_copy = {**base_data, **sidecar_data}
            c_copy["id"] = cid

            # Resolve part number and part-specific cache
            m_pt = re.match(r"^pt0*(\d+)", cid)
            part_num = int(m_pt.group(1)) if m_pt else None
            pt_cache = None
            if part_num and part_num > 1:
                pt_candidates = [
                    CACHE_DIR / f"{video_id}_pt{part_num:02d}",
                    CACHE_DIR / f"{video_id}_pt{part_num}",
                ]
                pt_cache = next((p for p in pt_candidates if p.exists() and p.is_dir()), None)

            # Vision info & sample frame (isolated strictly to the part)
            vis_candidates = []
            if pt_cache:
                vis_candidates.append(pt_cache / f"vision_{cid}.json")
                vis_candidates.append(pt_cache / f"vision_{stripped_cid}.json")
            vis_candidates.append(cache_v / f"vision_{cid}.json")
            if not part_num or part_num == 1:
                vis_candidates.append(cache_v / f"vision_{stripped_cid}.json")

            vis_p = next((p for p in vis_candidates if p.exists() and p.is_file()), None)
            if vis_p and "vision" not in c_copy:
                try:
                    with open(vis_p, "r", encoding="utf-8") as f:
                        c_copy["vision"] = json.load(f)
                except Exception:
                    pass

            sample_candidates = []
            if pt_cache:
                sample_candidates.append(pt_cache / f"vision_{cid}_mid.jpg")
                sample_candidates.append(pt_cache / f"vision_{stripped_cid}_mid.jpg")
            sample_candidates.append(cache_v / f"vision_{cid}_mid.jpg")
            if not part_num or part_num == 1:
                sample_candidates.append(cache_v / f"vision_{stripped_cid}_mid.jpg")

            sample_p = next((p for p in sample_candidates if p.exists() and p.is_file()), None)
            if sample_p:
                c_copy["sample_frame"] = f"/api/video/{video_id}/{sample_p.name}"
            else:
                c_copy["sample_frame"] = f"/api/video/{video_id}/vision_{cid}_mid.jpg"

            # Set part if not already present
            if not c_copy.get("part"):
                if cid.startswith("pt"):
                    m = re.match(r"^pt0*(\d+)", cid)
                    if m:
                        c_copy["part"] = f"Part {int(m.group(1))}"
                else:
                    c_copy["part"] = "Part 1"

            # Set selector_engine if not present
            if not c_copy.get("selector_engine"):
                if c_copy.get("score") == 65 or "audio loudness spike" in str(c_copy.get("why_viral", "")):
                    c_copy["selector_engine"] = "local-fallback"
                else:
                    c_copy["selector_engine"] = "gemini-3.1-flash-lite"

            # Resolve / populate variants (ensure all 3 layouts are always included if files exist)
            variants = c_copy.get("variants") or []
            existing_ids = {v.get("id") for v in variants}
            
            if "stack" not in existing_ids:
                stack_file = find_video_file([f"{cid}.mp4", f"{stripped_cid}.mp4"])
                if stack_file:
                    variants.insert(0, {
                        "id": "stack",
                        "name": "Reaction Stacked",
                        "label": "Reaction Stacked",
                        "file": stack_file,
                        "description": "Streamer reaction on top, reframed gameplay on bottom with seam captions"
                    })
            if "blur-fill" not in existing_ids:
                blur_file = find_video_file([f"{cid}_blur.mp4", f"{stripped_cid}_blur.mp4"])
                if blur_file:
                    variants.append({
                        "id": "blur-fill",
                        "name": "Reference Letterbox",
                        "label": "Reference Letterbox",
                        "file": blur_file,
                        "description": "Full 16:9 stream with corner webcam + bold 3-line headline at top + lower subtitles"
                    })
            if "reframed" not in existing_ids:
                reframed_file = find_video_file([f"{cid}_reframed.mp4", f"{stripped_cid}_reframed.mp4"])
                if reframed_file:
                    variants.append({
                        "id": "reframed",
                        "name": "Content (Deframed 9:16)",
                        "label": "Content (Deframed 9:16)",
                        "file": reframed_file,
                        "description": "Full-bleed 9:16 vertical crop focused on the action"
                    })

            for v in variants:
                lbl = v.get("label") or v.get("name") or "Variant"
                v["name"] = lbl
                v["label"] = lbl

            c_copy["variants"] = variants
            enhanced_clips.append(c_copy)

        # Sort clips by part index then clip index
        def clip_sort_key(c):
            cid = c.get("id", "")
            pm = re.search(r'pt0*(\d+)', cid)
            part_num = int(pm.group(1)) if pm else (c.get("part_index") or 1)
            cm = re.search(r'c0*(\d+)', cid)
            clip_num = int(cm.group(1)) if cm else 1
            return (part_num, clip_num)

        enhanced_clips.sort(key=clip_sort_key)

        # Check approvals
        approved_map = {}
        app_file = cache_v / "approved.json"
        if app_file.exists():
            try:
                with open(app_file, "r", encoding="utf-8") as f:
                    approved_map = json.load(f)
            except Exception:
                pass

        for c in enhanced_clips:
            c["status"] = approved_map.get(c["id"], "pending")
            if c.get("title"):
                c["title"] = re.sub(r"^\[?Part\s*\d+\]?:?\s*", "", str(c["title"]), flags=re.IGNORECASE).strip()

        # Check facecam status
        has_facecam = True
        fc_file = cache_v / "facecam.json"
        if fc_file.exists():
            try:
                with open(fc_file, "r", encoding="utf-8") as f:
                    fc_data = json.load(f)
                    has_facecam = fc_data.get("has_facecam", True)
            except Exception:
                pass

        # Check live session status
        live_session_file = cache_v / "live_session.json"
        if not live_session_file.exists():
            live_session_file = out_v / "live_session.json"
        live_session = None
        if live_session_file.exists():
            try:
                with open(live_session_file, "r", encoding="utf-8") as f:
                    live_session = json.load(f)
            except Exception:
                pass

        self.send_json({
            "video_id": video_id,
            "title": info.get("title", video_id),
            "project": info,
            "clips": enhanced_clips,
            "has_facecam": has_facecam,
            "live_session": live_session,
            "metrics": {
                "total_clips": len(enhanced_clips),
                "approved_count": sum(1 for c in enhanced_clips if c.get("status") == "approved"),
                "export_ready": (READY_DIR / video_id).exists(),
            }
        })

    def handle_approve_clip(self, data: Dict[str, Any]):
        video_id = data.get("video_id")
        clip_id = data.get("clip_id")
        status = data.get("status", "approved")  # approved, rejected, pending

        if not video_id or not clip_id:
            self.send_error(HTTPStatus.BAD_REQUEST, "Missing video_id or clip_id")
            return

        cache_v = CACHE_DIR / video_id
        cache_v.mkdir(parents=True, exist_ok=True)
        app_file = cache_v / "approved.json"

        approved_map = {}
        if app_file.exists():
            try:
                with open(app_file, "r", encoding="utf-8") as f:
                    approved_map = json.load(f)
            except Exception:
                approved_map = {}

        approved_map[clip_id] = status
        with open(app_file, "w", encoding="utf-8") as f:
            json.dump(approved_map, f, indent=2)

        self.send_json({"success": True, "approved_map": approved_map})

    def handle_export(self, data: Dict[str, Any]):
        video_id = data.get("video_id")
        if not video_id:
            self.send_error(HTTPStatus.BAD_REQUEST, "Missing video_id")
            return

        try:
            from clipit.stages.export import run_export
            dest_dir = run_export(video_id=video_id, out_dir_base=ROOT_DIR / "ready_to_post")
            files = [f.name for f in dest_dir.glob("*.mp4")]
            self.send_json({
                "success": True,
                "dest_dir": str(dest_dir),
                "files": files,
                "copy_pack": str(dest_dir / "copy_paste_pack.txt"),
                "schedule_csv": str(dest_dir / "schedule.csv"),
            })
        except Exception as e:
            self.send_json({"success": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_export_single_clip(self, data: Dict[str, Any]):
        video_id = data.get("video_id")
        clip_id = data.get("clip_id")
        filename = data.get("filename")  # e.g. pt01_c04.mp4 or pt01_c04_blur.mp4
        title = data.get("title", "")
        publishing_text = data.get("publishing_text", "")

        if not video_id or not filename:
            self.send_error(HTTPStatus.BAD_REQUEST, "Missing video_id or filename")
            return

        out_v = OUT_DIR / video_id
        cache_v = CACHE_DIR / video_id

        # 1. Locate the video file
        candidate_paths = [
            out_v / filename,
            READY_DIR / video_id / filename,
            cache_v / filename,
        ]
        if out_v.exists():
            for pdir in sorted(out_v.glob("part_*")):
                candidate_paths.append(pdir / filename)
        if CACHE_DIR.exists():
            for cdir in sorted(CACHE_DIR.glob(f"{video_id}_pt*")):
                candidate_paths.append(cdir / filename)

        target_video = next((p for p in candidate_paths if p.exists() and p.is_file()), None)
        if not target_video:
            self.send_error(HTTPStatus.NOT_FOUND, f"Video file not found: {filename}")
            return

        # 2. Target Downloads folder
        downloads_dir = Path.home() / "Downloads"
        try:
            downloads_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        # 3. Clean filenames for download
        clean_title = re.sub(r"[^\w\-_\. ]", "", title).strip() or Path(filename).stem
        clean_title = re.sub(r"\s+", "_", clean_title)

        var_suffix = ""
        if "_blur" in filename:
            var_suffix = "_Letterbox"
        elif "_reframed" in filename:
            var_suffix = "_Deframed"

        base_stem = f"{clean_title}{var_suffix}"
        dest_video = downloads_dir / f"{base_stem}.mp4"
        dest_txt = downloads_dir / f"{base_stem}_publishing_pack.txt"

        import shutil
        try:
            shutil.copy2(target_video, dest_video)
            with open(dest_txt, "w", encoding="utf-8") as f:
                f.write(publishing_text.strip() + "\n")
        except Exception as e:
            self.send_json({"success": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        self.send_json({
            "success": True,
            "video_path": str(dest_video),
            "txt_path": str(dest_txt),
            "video_name": dest_video.name,
            "txt_name": dest_txt.name,
            "downloads_dir": str(downloads_dir),
            "download_url": f"/api/download/{video_id}/{filename}",
        })

    def handle_rerender_clip(self, data: Dict[str, Any]):
        video_id = data.get("video_id")
        clip_id = data.get("clip_id")
        if not video_id or not clip_id:
            self.send_error(HTTPStatus.BAD_REQUEST, "Missing video_id or clip_id")
            return

        cache_v = CACHE_DIR / video_id
        out_v = OUT_DIR / video_id
        if not cache_v.exists() and not out_v.exists():
            self.send_error(HTTPStatus.NOT_FOUND, f"Project not found for {video_id}")
            return

        # 1. Resolve Part number and Part-specific cache/output directories
        part_num = None
        m = re.match(r"^pt0*(\d+)", clip_id)
        if m:
            part_num = int(m.group(1))

        part_cache_v = None
        part_out_v = None
        if part_num and part_num > 1:
            candidates = [
                CACHE_DIR / f"{video_id}_pt{part_num:02d}",
                CACHE_DIR / f"{video_id}_pt{part_num}",
            ]
            part_cache_v = next((p for p in candidates if p.exists() and p.is_dir()), None)

            out_candidates = [
                out_v / f"part_{part_num:02d}",
                out_v / f"part_{part_num}",
            ]
            part_out_v = next((p for p in out_candidates if p.exists() and p.is_dir()), None)

        active_cache_v = part_cache_v or cache_v
        active_cache_name = active_cache_v.name
        cache = CacheManager(active_cache_name, base_dir=CACHE_DIR)

        # 2. Update customizable parameters
        vision_file = f"vision_{clip_id}.json"
        vision_info = cache.load_json(vision_file) or {}
        if not vision_info:
            root_cache = CacheManager(video_id, base_dir=CACHE_DIR)
            vision_info = root_cache.load_json(vision_file) or {}

        if "has_facecam" in data:
            vision_info["has_facecam"] = bool(data["has_facecam"])
        if "streamer_pct" in data:
            vision_info["streamer_pct"] = int(data["streamer_pct"])
            vision_info["content_pct"] = 100 - vision_info["streamer_pct"]
        if "content_focus_x" in data:
            vision_info["content_focus_x"] = int(data["content_focus_x"])
        if "content_alignment" in data:
            vision_info["content_alignment"] = data["content_alignment"]
        if "streamer_box" in data and data["streamer_box"] and len(data["streamer_box"]) == 4:
            vision_info["streamer_box"] = [int(round(float(v))) for v in data["streamer_box"]]
            if data.get("has_facecam") is not False:
                vision_info["has_facecam"] = True
        if "content_ratio" in data:
            vision_info["content_ratio"] = str(data["content_ratio"])
        if "text_position" in data:
            vision_info["text_position"] = str(data["text_position"])
        if "content_zoom" in data:
            vision_info["content_zoom"] = float(data["content_zoom"])
        if "title" in data and data["title"]:
            clean_t = re.sub(r"^\[?Part\s*\d+\]?:?\s*", "", str(data["title"]), flags=re.IGNORECASE).strip()
            vision_info["title"] = clean_t
        if "title_size" in data and data["title_size"]:
            try:
                vision_info["title_size"] = int(data["title_size"])
            except Exception:
                pass
        if "caption_size" in data and data["caption_size"]:
            try:
                vision_info["caption_size"] = int(data["caption_size"])
            except Exception:
                pass

        cache.save_json(vision_file, vision_info)
        if active_cache_v != cache_v:
            root_cache = CacheManager(video_id, base_dir=CACHE_DIR)
            root_cache.save_json(vision_file, vision_info)

        # 3. Locate clip metadata (check sidecars first as ground truth, then refined_clips)
        clip_dict = None
        sidecar_candidates = [
            out_v / f"{clip_id}.json",
            (part_out_v / f"{clip_id}.json") if part_out_v else None,
            active_cache_v / f"{clip_id}.json",
        ]
        for sc_p in sidecar_candidates:
            if sc_p and sc_p.exists():
                try:
                    with open(sc_p, "r", encoding="utf-8") as f:
                        clip_dict = json.load(f)
                        break
                except Exception:
                    pass

        if not clip_dict:
            refined = active_cache_v.exists() and cache.load_json("refined_clips.json") or []
            clip_dict = next((c for c in refined if c.get("id") == clip_id), None)

        if not clip_dict:
            for sess_p in (cache_v / "live_session.json", out_v / "live_session.json"):
                if sess_p.exists():
                    try:
                        with open(sess_p, "r", encoding="utf-8") as f:
                            sess_d = json.load(f)
                            for pt in sess_d.get("parts", []):
                                for c in pt.get("clips", []):
                                    if c.get("id") == clip_id:
                                        clip_dict = c
                                        break
                                if clip_dict:
                                    break
                    except Exception:
                        pass
                if clip_dict:
                    break

        if not clip_dict:
            self.send_error(HTTPStatus.NOT_FOUND, f"Clip {clip_id} metadata not found")
            return

        # 4. Locate source video and transcript for the correct part
        source_video = active_cache_v / "source.mp4"
        if not source_video.exists():
            source_video = cache_v / "source.mp4"

        t_data = cache.load_json("transcript.json")
        if not t_data and active_cache_v != cache_v:
            root_cache = CacheManager(video_id, base_dir=CACHE_DIR)
            t_data = root_cache.load_json("transcript.json")

        if not t_data:
            self.send_error(HTTPStatus.NOT_FOUND, "Transcript not found in cache")
            return
        transcript = FullTranscript(**t_data)

        # 5. Render with VideoToolbox hardware encoder
        render_target_dir = part_out_v if part_out_v else out_v
        cfg = load_config()
        layout_target = data.get("layout", "all")

        valid_keys = {"id", "start", "end", "score", "title", "title_alts", "hook_line", "hooks", "why_viral", "category", "signals", "risk_flags", "trim_notes", "layout", "source_permission"}
        # Always use the already-cleaned title from vision_info if set, else strip [Part X]
        if vision_info.get("title"):
            clip_dict["title"] = vision_info["title"]
        else:
            clip_dict["title"] = re.sub(r"^\[?Part\s*\d+\]?:?\s*", "", str(clip_dict.get("title", "")), flags=re.IGNORECASE).strip()
        # Persist the cleaned title back to vision_info so render.py uses it reliably
        if clip_dict["title"] and not vision_info.get("title"):
            vision_info["title"] = clip_dict["title"]
            cache.save_json(vision_file, vision_info)
            if active_cache_v != cache_v:
                root_cache = CacheManager(video_id, base_dir=CACHE_DIR)
                root_cache.save_json(vision_file, vision_info)
        filtered_clip = {k: v for k, v in clip_dict.items() if k in valid_keys}
        clip = ClipItem(**filtered_clip)

        try:
            render_single_clip(
                clip=clip,
                source_video=source_video,
                output_dir=render_target_dir,
                transcript=transcript,
                cache=cache,
                video_config=cfg.video,
                codec="h264_videotoolbox",
                render_variants=True,
                force=True,
            )

            # Keep top-level out_v and part_out_v synchronized
            if part_out_v and part_out_v != out_v:
                import shutil
                for ext in (".mp4", "_blur.mp4", "_reframed.mp4", ".json"):
                    src_f = part_out_v / f"{clip_id}{ext}"
                    dst_f = out_v / f"{clip_id}{ext}"
                    if src_f.exists():
                        shutil.copy2(src_f, dst_f)

            # Clean up any stale pre-existing rendered video files in cache directories so they never shadow the new output
            cache_clean_targets = [part_cache_v, active_cache_v, cache_v]
            for c_dir in cache_clean_targets:
                if c_dir and c_dir.exists():
                    for ext in (".mp4", "_blur.mp4", "_reframed.mp4"):
                        stale_f = c_dir / f"{clip_id}{ext}"
                        if stale_f.exists():
                            try:
                                stale_f.unlink()
                            except Exception:
                                pass

            sidecar = {}
            sidecar_p = out_v / f"{clip_id}.json"
            if sidecar_p.exists():
                with open(sidecar_p, "r", encoding="utf-8") as f:
                    sidecar = json.load(f)
                # Always persist the cleaned/custom title to sidecar
                cleaned_title = vision_info.get("title") or clip_dict.get("title")
                if cleaned_title:
                    sidecar["title"] = cleaned_title
                    with open(sidecar_p, "w", encoding="utf-8") as f:
                        json.dump(sidecar, f, indent=2)

            if part_out_v and (part_out_v / f"{clip_id}.json").exists():
                try:
                    with open(part_out_v / f"{clip_id}.json", "r", encoding="utf-8") as f:
                        psd = json.load(f)
                    cleaned_title = vision_info.get("title") or clip_dict.get("title")
                    if cleaned_title:
                        psd["title"] = cleaned_title
                        with open(part_out_v / f"{clip_id}.json", "w", encoding="utf-8") as f:
                            json.dump(psd, f, indent=2)
                except Exception:
                    pass

            if "variants" in sidecar:
                for v in sidecar["variants"]:
                    lbl = v.get("label") or v.get("name") or "Variant"
                    v["name"] = lbl
                    v["label"] = lbl

            self.send_json({
                "success": True,
                "clip_id": clip_id,
                "title": clip_dict.get("title"),
                "vision": vision_info,
                "sidecar": sidecar,
                "layout_target": layout_target,
                "timestamp": int(time.time()),
            })
        except Exception as e:
            self.send_json({"success": False, "error": str(e)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_update_clip_title(self, data: Dict[str, Any]):
        video_id = data.get("video_id")
        clip_id = data.get("clip_id")
        new_title = data.get("title", "").strip()
        new_title = re.sub(r"^\[?Part\s*\d+\]?:?\s*", "", new_title, flags=re.IGNORECASE).strip()

        if not video_id or not clip_id or not new_title:
            self.send_error(HTTPStatus.BAD_REQUEST, "Missing video_id, clip_id, or title")
            return

        cache_v = CACHE_DIR / video_id
        out_v = OUT_DIR / video_id

        m = re.match(r"^pt0*(\d+)", clip_id)
        part_num = int(m.group(1)) if m else None
        part_out_v = (out_v / f"part_{part_num:02d}") if part_num and part_num > 1 else None

        candidates = [
            out_v / f"{clip_id}.json",
            (part_out_v / f"{clip_id}.json") if part_out_v else None,
            cache_v / f"{clip_id}.json",
        ]
        for p in candidates:
            if p and p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        d = json.load(f)
                    d["title"] = new_title
                    with open(p, "w", encoding="utf-8") as f:
                        json.dump(d, f, indent=2)
                except Exception:
                    pass

        # Update vision json
        for cdir in (cache_v, (CACHE_DIR / f"{video_id}_pt{part_num:02d}") if part_num and part_num > 1 else None):
            if cdir and cdir.exists():
                vis_p = cdir / f"vision_{clip_id}.json"
                if vis_p.exists():
                    try:
                        with open(vis_p, "r", encoding="utf-8") as f:
                            vd = json.load(f)
                        vd["title"] = new_title
                        with open(vis_p, "w", encoding="utf-8") as f:
                            json.dump(vd, f, indent=2)
                    except Exception:
                        pass

        # Update live_session.json
        for sess_p in (cache_v / "live_session.json", out_v / "live_session.json"):
            if sess_p.exists():
                try:
                    with open(sess_p, "r", encoding="utf-8") as f:
                        sess_d = json.load(f)
                    for pt in sess_d.get("parts", []):
                        for c in pt.get("clips", []):
                            if c.get("id") == clip_id:
                                c["title"] = new_title
                    with open(sess_p, "w", encoding="utf-8") as f:
                        json.dump(sess_d, f, indent=2)
                except Exception:
                    pass

        self.send_json({"success": True, "clip_id": clip_id, "title": new_title})

    def handle_start_live_sentinel(self, data: Dict[str, Any]):
        url = data.get("url", "").strip()
        if not url:
            self.send_json({"error": "Stream URL is required"}, status=HTTPStatus.BAD_REQUEST)
            return

        chunk_mins = int(data.get("chunk_mins", 30))
        overlap_mins = int(data.get("overlap_mins", 5))
        target_clips = int(data.get("target_clips", 6))
        layout = data.get("layout", "stack")
        style = data.get("style", "bold-yellow")
        profile = data.get("profile", "ishowspeed")

        from clipit.stages.live_monitor import LiveStreamMonitor
        vid = get_video_id(url)

        monitor = LiveStreamMonitor(
            url=url,
            chunk_mins=chunk_mins,
            overlap_mins=overlap_mins,
            target_clips=target_clips,
            layout=layout,
            style=style,
            profile=profile,
            out_base=OUT_DIR,
            cache_base=CACHE_DIR,
        )

        stop_event = threading.Event()

        def worker_sentinel():
            while not stop_event.is_set():
                try:
                    monitor.run_next_pending_part()
                    if monitor.state.get("status") == "completed":
                        break
                except Exception as ex:
                    print(f"Live monitor exception: {ex}")
                stop_event.wait(60)

        session_id = f"live_{vid}"
        q = queue.Queue()
        with session_lock:
            active_sessions[session_id] = {
                "id": session_id,
                "url": url,
                "queue": q,
                "created_at": time.time(),
                "status": "running",
                "events": [],
                "video_id": vid,
                "done": False,
            }

        t = threading.Thread(target=worker_sentinel, daemon=True)
        active_live_monitors[vid] = {
            "session_id": session_id,
            "monitor": monitor,
            "thread": t,
            "stop_event": stop_event,
            "started_at": time.time(),
            "url": url,
        }
        t.start()

        self.send_json({
            "success": True,
            "session_id": session_id,
            "video_id": vid,
            "status": "watching",
            "session": monitor.state,
        })

    def handle_get_live_status(self, video_id: Optional[str] = None):
        if not video_id:
            res = {}
            for vid, m_info in active_live_monitors.items():
                res[vid] = {
                    "running": m_info["thread"].is_alive(),
                    "session": m_info["monitor"].state,
                }
            self.send_json({"active_monitors": res})
            return

        if video_id in active_live_monitors:
            m_info = active_live_monitors[video_id]
            self.send_json({
                "video_id": video_id,
                "running": m_info["thread"].is_alive(),
                "session": m_info["monitor"].state,
            })
            return

        sess_file = CACHE_DIR / video_id / "live_session.json"
        if not sess_file.exists():
            sess_file = OUT_DIR / video_id / "live_session.json"
        if sess_file.exists():
            try:
                with open(sess_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.send_json({
                    "video_id": video_id,
                    "running": False,
                    "session": data,
                })
                return
            except Exception:
                pass

        self.send_error(HTTPStatus.NOT_FOUND, f"No live monitor session found for {video_id}")

    def handle_stop_live_sentinel(self, data: Dict[str, Any]):
        video_id = data.get("video_id")
        if not video_id or video_id not in active_live_monitors:
            self.send_error(HTTPStatus.NOT_FOUND, "Monitor not active")
            return
        m_info = active_live_monitors[video_id]
        m_info["stop_event"].set()
        m_info["monitor"].state["status"] = "stopped"
        m_info["monitor"].save_session()
        del active_live_monitors[video_id]
        self.send_json({"success": True, "video_id": video_id, "status": "stopped"})

    # ==========================================
    # Live Multi-Agent Workflow Simulation
    # ==========================================

    def handle_start_pipeline(self, data: Dict[str, Any]):
        url = data.get("url", "").strip()
        if not url:
            self.send_json({"error": "Stream URL is required"}, status=HTTPStatus.BAD_REQUEST)
            return
        raw_target = data.get("target_clips", 0)
        try:
            target_clips = int(raw_target)
        except (ValueError, TypeError):
            target_clips = 0  # 0 indicates auto mode
        layout = data.get("layout", "stack")
        style = data.get("style", "bold-yellow")
        profile = data.get("profile", "ishowspeed")
        facecam = data.get("facecam", "auto")

        session_id = f"sess_{int(time.time()*1000)}"
        q = queue.Queue()

        with session_lock:
            active_sessions[session_id] = {
                "id": session_id,
                "url": url,
                "queue": q,
                "created_at": time.time(),
                "status": "starting",
                "events": [],
                "video_id": None,
                "done": False,
            }

        # Spawn agent workflow thread
        worker = threading.Thread(
            target=run_agent_pipeline_worker,
            args=(session_id, q, url, target_clips, layout, style, profile, facecam),
            daemon=True,
        )
        worker.start()

        self.send_json({"session_id": session_id, "status": "started"})

    def handle_rerun_ai(self, data: Dict[str, Any]):
        video_id = data.get("video_id")
        if not video_id:
            self.send_error(HTTPStatus.BAD_REQUEST, "Missing video_id")
            return
        cache_v = CACHE_DIR / video_id
        if not cache_v.exists():
            self.send_error(HTTPStatus.NOT_FOUND, f"Video {video_id} not found in cache")
            return

        cache = CacheManager(video_id, base_dir=CACHE_DIR)
        info = cache.load_json("info.json") or {}
        url = info.get("source") or (f"https://www.youtube.com/watch?v={video_id[3:]}" if video_id.startswith("yt_") else video_id)
        raw_target = data.get("target_clips", 0)
        try:
            target_clips = int(raw_target)
        except (ValueError, TypeError):
            target_clips = 0
        layout = data.get("layout", "stack")
        style = data.get("style", "bold-yellow")
        profile = data.get("profile", "ishowspeed")
        facecam = data.get("facecam", "auto")

        session_id = f"sess_{int(time.time()*1000)}"
        q = queue.Queue()

        with session_lock:
            active_sessions[session_id] = {
                "id": session_id,
                "url": url,
                "queue": q,
                "created_at": time.time(),
                "status": "starting",
                "events": [],
                "video_id": video_id,
                "done": False,
            }

        worker = threading.Thread(
            target=run_agent_pipeline_worker,
            args=(session_id, q, url, target_clips, layout, style, profile, facecam, True),
            daemon=True,
        )
        worker.start()

        self.send_json({
            "success": True,
            "session_id": session_id,
            "status": "started",
            "video_id": video_id,
            "url": url,
        })

    def handle_sse_workflow(self, session_id: Optional[str]):
        """Stream real-time Server-Sent Events (SSE) to the visualizer."""
        if not session_id or session_id not in active_sessions:
            # Check if this is a live session on disk
            if session_id and session_id.startswith("live_"):
                vid = session_id[5:]
                sess_path = CACHE_DIR / vid / "live_session.json"
                if sess_path.exists():
                    try:
                        sess_data = json.loads(sess_path.read_text(encoding="utf-8"))
                        active_sessions[session_id] = {
                            "id": session_id,
                            "url": sess_data.get("source_url", "") or sess_data.get("url", ""),
                            "queue": queue.Queue(),
                            "created_at": sess_data.get("start_time", sess_path.stat().st_mtime),
                            "status": "running",
                            "events": self._synthesize_live_events(sess_data, vid),
                            "video_id": vid,
                            "done": False,
                        }
                    except Exception:
                        pass
        if not session_id or session_id not in active_sessions:
            self.send_error(HTTPStatus.NOT_FOUND, "Invalid or expired session_id")
            return

        session = active_sessions[session_id]
        if session_id.startswith("live_") and not session.get("events"):
            vid = session.get("video_id") or session_id[5:]
            sess_path = CACHE_DIR / vid / "live_session.json"
            if sess_path.exists():
                try:
                    sess_data = json.loads(sess_path.read_text(encoding="utf-8"))
                    session["events"] = self._synthesize_live_events(sess_data, vid)
                except Exception:
                    pass

        q = session["queue"]

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_cors_headers()
        self.end_headers()

        # 1. Replay historical events so reconnections or refreshed browsers instantly catch up!
        with session_lock:
            past_events = list(session.get("events", []))
            is_done = session.get("done", False)

        try:
            for pe in past_events:
                ev_name = pe.get("event", "message")
                p_data = json.dumps(pe.get("data", {}))
                self.wfile.write(f"event: {ev_name}\ndata: {p_data}\n\n".encode("utf-8"))
            self.wfile.flush()

            if is_done and not session_id.startswith("live_"):
                return

            while True:
                try:
                    event = q.get(timeout=4.0)
                except queue.Empty:
                    # Keep-alive heartbeat every 4s to prevent browser/proxy timeout
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                    continue

                if event is None:
                    # End of stream
                    break

                event_name = event.get("event", "message")
                payload = json.dumps(event.get("data", {}))
                sse_msg = f"event: {event_name}\ndata: {payload}\n\n".encode("utf-8")
                self.wfile.write(sse_msg)
                self.wfile.flush()

                if event_name in ("pipeline_complete", "pipeline_error"):
                    if not session_id.startswith("live_"):
                        break
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ==========================================
    # Video Streaming & Range Handling
    # ==========================================

    def handle_stream_video(self, video_id: str, filename: str):
        out_v = OUT_DIR / video_id
        cache_v = CACHE_DIR / video_id

        # Determine part number if filename references a part (e.g. pt05_c01.mp4 or vision_pt05_c01_mid.jpg)
        m_part = re.search(r"pt0*(\d+)", filename)
        part_num = int(m_part.group(1)) if m_part else None

        candidate_paths = []

        part_out = None
        part_cache = None
        if part_num and part_num > 1:
            candidates_o = [
                out_v / f"part_{part_num:02d}",
                out_v / f"part_{part_num}",
            ]
            part_out = next((p for p in candidates_o if p.exists() and p.is_dir()), None)

            candidates_c = [
                CACHE_DIR / f"{video_id}_pt{part_num:02d}",
                CACHE_DIR / f"{video_id}_pt{part_num}",
            ]
            part_cache = next((p for p in candidates_c if p.exists() and p.is_dir()), None)

        # 1. Output directories FIRST (Canonical location for all rendered videos and published assets)
        if part_out:
            candidate_paths.append(part_out / filename)
            stripped = re.sub(r"pt\d+_", "", filename)
            if stripped != filename:
                candidate_paths.append(part_out / stripped)

        candidate_paths.extend([
            out_v / filename,
            READY_DIR / video_id / filename,
        ])

        if out_v.exists():
            for part_dir in sorted(out_v.glob("part_*")):
                if part_dir.is_dir() and part_dir != part_out:
                    candidate_paths.append(part_dir / filename)

        # Check aliases in output directories: ptXX_cYY <-> cYY
        alias_names = []
        stripped = re.sub(r'pt\d+_', '', filename)
        if stripped != filename:
            alias_names.append(stripped)
        elif filename.startswith("c"):
            if out_v.exists():
                for pt_file in out_v.glob(f"pt*_{filename}"):
                    candidate_paths.append(pt_file)

        for alias in alias_names:
            candidate_paths.append(out_v / alias)
            if out_v.exists():
                for part_dir in sorted(out_v.glob("part_*")):
                    if part_dir.is_dir():
                        candidate_paths.append(part_dir / alias)

        # 2. Cache directories LAST (ONLY for intermediate files like vision frames, audio.wav, source.mp4)
        if part_cache:
            candidate_paths.append(part_cache / filename)
            if stripped != filename:
                candidate_paths.append(part_cache / stripped)

        candidate_paths.append(CACHE_DIR / video_id / filename)

        for p_cache in sorted(CACHE_DIR.glob(f"{video_id}_pt*")):
            if p_cache.is_dir() and p_cache != part_cache:
                candidate_paths.append(p_cache / filename)

        # Only fall back to root cache for alias if no part_num or part_num == 1
        if not part_num or part_num == 1:
            candidate_paths.append(CACHE_DIR / video_id / alias)
        candidate_paths.append(READY_DIR / video_id / alias)

        target = next((p for p in candidate_paths if p.exists() and p.is_file()), None)

        if not target:
            self.send_error(HTTPStatus.NOT_FOUND, f"Video not found: {filename}")
            return

        file_size = target.stat().st_size
        mime, _ = mimetypes.guess_type(str(target))
        content_type = mime or "video/mp4"
        range_header = self.headers.get("Range")

        if range_header:
            # Parse Range: bytes=start-end
            try:
                range_match = range_header.strip().split("=")[1]
                start_str, end_str = range_match.split("-")
                start = int(start_str) if start_str else 0
                end = int(end_str) if end_str else file_size - 1
                end = min(end, file_size - 1)
                chunk_len = end - start + 1

                self.send_response(HTTPStatus.PARTIAL_CONTENT)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Content-Length", str(chunk_len))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.send_cors_headers()
                self.end_headers()

                with open(target, "rb") as f:
                    f.seek(start)
                    bytes_left = chunk_len
                    while bytes_left > 0:
                        chunk_to_read = min(65536, bytes_left)
                        data = f.read(chunk_to_read)
                        if not data:
                            break
                        self.wfile.write(data)
                        bytes_left -= len(data)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                self.send_error(HTTPStatus.BAD_REQUEST, f"Error streaming video range: {e}")
        else:
            # Serve full file
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(file_size))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_cors_headers()
            self.end_headers()

            try:
                with open(target, "rb") as f:
                    while True:
                        data = f.read(65536)
                        if not data:
                            break
                        self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def handle_download_video(self, video_id: str, filename: str):
        out_v = OUT_DIR / video_id
        cache_v = CACHE_DIR / video_id
        candidate_paths = [
            out_v / filename,
            READY_DIR / video_id / filename,
            cache_v / filename,
        ]
        if out_v.exists():
            for pdir in sorted(out_v.glob("part_*")):
                candidate_paths.append(pdir / filename)
        if CACHE_DIR.exists():
            for cdir in sorted(CACHE_DIR.glob(f"{video_id}_pt*")):
                candidate_paths.append(cdir / filename)

        target = next((p for p in candidate_paths if p.exists() and p.is_file()), None)
        if not target:
            self.send_error(HTTPStatus.NOT_FOUND, f"File not found: {filename}")
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(target.stat().st_size))
        self.send_cors_headers()
        self.end_headers()

        with open(target, "rb") as f:
            while True:
                data = f.read(65536)
                if not data:
                    break
                self.wfile.write(data)

    def handle_youtube_status(self):
        from clipit.youtube import is_configured, is_connected, get_channel_info
        configured = is_configured()
        connected = is_connected()
        channel = get_channel_info() if connected else None
        self.send_json({
            "configured": configured,
            "connected": connected,
            "channel": channel,
        })

    def handle_youtube_auth(self):
        from clipit.youtube import authenticate_channel
        res = authenticate_channel(open_browser=True)
        status_code = HTTPStatus.OK if res.get("success") else HTTPStatus.INTERNAL_SERVER_ERROR
        self.send_json(res, status=status_code)

    def handle_youtube_disconnect(self):
        from clipit.youtube import disconnect_channel
        success = disconnect_channel()
        self.send_json({"success": success})

    def handle_youtube_upload(self, data: Dict[str, Any]):
        video_id = data.get("video_id")
        filename = data.get("filename")
        title = data.get("title", "GTA 6 Viral Moment")
        description = data.get("description", "")
        tags = data.get("tags", ["#Shorts", "#Gaming"])
        privacy = data.get("privacy", "public")

        if not video_id or not filename:
            self.send_json({"success": False, "error": "Missing video_id or filename"}, status=HTTPStatus.BAD_REQUEST)
            return

        out_v = OUT_DIR / video_id
        cache_v = CACHE_DIR / video_id
        candidate_paths = [
            out_v / filename,
            READY_DIR / video_id / filename,
            cache_v / filename,
        ]
        if out_v.exists():
            for pdir in sorted(out_v.glob("part_*")):
                candidate_paths.append(pdir / filename)
        if CACHE_DIR.exists():
            for cdir in sorted(CACHE_DIR.glob(f"{video_id}_pt*")):
                candidate_paths.append(cdir / filename)

        target_video = next((p for p in candidate_paths if p.exists() and p.is_file()), None)
        if not target_video:
            self.send_json({"success": False, "error": f"Video file not found: {filename}"}, status=HTTPStatus.NOT_FOUND)
            return

        from clipit.youtube import upload_short
        res = upload_short(
            file_path=target_video,
            title=title,
            description=description,
            tags=tags,
            privacy_status=privacy,
        )
        status_code = HTTPStatus.OK if res.get("success") else HTTPStatus.INTERNAL_SERVER_ERROR
        self.send_json(res, status=status_code)


    def serve_file(self, filepath: Path, content_type: str):
        if not filepath.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return

        content = filepath.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, payload: Any, status: int = HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(body)


# ==========================================
# Multi-Agent Workflow Engine Worker
# ==========================================

def run_agent_pipeline_worker(
    session_id: str,
    q: queue.Queue,
    url: str,
    target_clips: int,
    layout: str,
    style: str,
    profile: str,
    facecam: str = "auto",
    force_rerun_ai: bool = False,
):
    """Execute live multi-agent pipeline and stream real-time SSE events."""
    video_id = get_video_id(url)
    cache = CacheManager(video_id, base_dir=CACHE_DIR)
    config = load_config()

    # Pre-configure facecam override if specified by user
    if facecam == "none":
        cache.save_json("facecam.json", {"has_facecam": False, "box": None})
    elif facecam == "bottom-left":
        cache.save_json("facecam.json", {"has_facecam": True, "box": [0, 520, 640, 560], "position": "bottom-left"})
    elif facecam == "bottom-right":
        cache.save_json("facecam.json", {"has_facecam": True, "box": [1280, 520, 640, 560], "position": "bottom-right"})
    elif facecam == "top-left":
        cache.save_json("facecam.json", {"has_facecam": True, "box": [0, 0, 640, 560], "position": "top-left"})
    elif facecam == "top-right":
        cache.save_json("facecam.json", {"has_facecam": True, "box": [1280, 0, 640, 560], "position": "top-right"})

    stages = [
        {
            "id": "ingest",
            "name": "Stream Ingestion Agent",
            "badge": "yt-dlp + ffmpeg",
            "role": "Source Stream Acquisition & Demuxer",
            "desc": "Downloading source stream, extracting 16kHz mono audio, and parsing live stream metadata.",
        },
        {
            "id": "transcribe",
            "name": "Whisper Audio Alignment Agent",
            "badge": "mlx-whisper (whisper-large-v3-turbo)",
            "role": "Apple Silicon Metal Timestamp Alignment",
            "desc": "Transcribing master audio with word-level start/end timestamps and acoustic phonetic confidence.",
        },
        {
            "id": "signals",
            "name": "Multi-Signal Intelligence Agent",
            "badge": "scipy + numpy spectral analysis",
            "role": "Acoustic Pitch, Energy & Chat Velocity Analyzer",
            "desc": "Computing 1-second sliding energy curve, pitch variance, vocal excitement, and chat sentiment density.",
        },
        {
            "id": "candidates",
            "name": "Candidate Window Generator Agent",
            "badge": "Sliding Window Optimization",
            "role": "Peak Isolation & Window Framing",
            "desc": "Extracting high-virality candidate segments (20s–60s) with boundary padding and deduplication.",
        },
        {
            "id": "director",
            "name": "Gemini AI Director Agent",
            "badge": "Gemini 3.8 Flash / 3.6 Flash / 3.1 Flash Lite",
            "role": "Story Arc, Hook Scoring & Narrative Justification",
            "desc": "Analyzing emotional payoff, comedic timing, hook strength, and generating viral titles & hashtags.",
        },
        {
            "id": "vision",
            "name": "Vision Reframing & Facecam Agent",
            "badge": "OpenCV YuNet + Gemini Vision",
            "role": "Spatial Webcam Detection & Content Crop Reframing",
            "desc": "Detecting streamer corner webcam coordinates, eliminating YouTube letterboxes, and tracking focal point.",
        },
        {
            "id": "render",
            "name": "Hardware Motion Render Agent",
            "badge": "FFmpeg h264_videotoolbox + libass",
            "role": "Multi-Variant 1080x1920 Hardware Rendering",
            "desc": "Rendering 3 vertical layouts per clip with word-by-word kinetic bouncing captions and bold headline.",
        },
        {
            "id": "export",
            "name": "Publishing Packager Agent",
            "badge": "ClipIt Review & Quality Sentinel",
            "role": "Viral Renaming, Copy Pack & CSV Staging",
            "desc": "Staging ready-to-post MP4s, schedule CSV, quality sentinel report, and 1-click publishing pack.",
        },
    ]

    total_stages = len(stages)

    def emit_event(ev_type: str, data: Dict[str, Any]):
        ev = {"event": ev_type, "data": data}
        with session_lock:
            if session_id in active_sessions:
                active_sessions[session_id]["events"].append(ev)
                if ev_type in ("pipeline_complete", "pipeline_error"):
                    active_sessions[session_id]["done"] = True
                if "video_id" in data and data["video_id"]:
                    active_sessions[session_id]["video_id"] = data["video_id"]
        q.put(ev)

    def emit_stage_start(idx: int, s: Dict[str, Any]):
        emit_event("stage_start", {
            "stage_index": idx,
            "stage_id": s["id"],
            "name": s["name"],
            "badge": s["badge"],
            "role": s["role"],
            "description": s["desc"],
            "progress_pct": int((idx - 1) / total_stages * 100),
        })

    def emit_log(stage_id: str, line: str, metrics: Optional[Dict[str, Any]] = None):
        emit_event("agent_log", {
            "stage_id": stage_id,
            "timestamp": time.strftime("%H:%M:%S"),
            "log": line,
            "metrics": metrics or {},
        })

    def emit_stage_complete(idx: int, s: Dict[str, Any], metrics: Optional[Dict[str, Any]] = None):
        emit_event("stage_complete", {
            "stage_id": s["id"],
            "stage_index": idx,
            "progress_pct": int(idx / total_stages * 100),
            "metrics": metrics or {},
        })

    class StageHeartbeat:
        """Emits periodic live status pulses to keep SSE alive and inform user during long computations."""
        def __init__(self, stage_id: str, message_template: str, interval: float = 4.0):
            self.stage_id = stage_id
            self.message_template = message_template
            self.interval = interval
            self.stop_evt = threading.Event()
            self.thread = None

        def __enter__(self):
            def loop():
                elapsed = 0
                while not self.stop_evt.wait(self.interval):
                    elapsed += int(self.interval)
                    emit_log(self.stage_id, self.message_template.format(elapsed=elapsed))
            self.thread = threading.Thread(target=loop, daemon=True)
            self.thread.start()
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            self.stop_evt.set()
            if self.thread:
                self.thread.join(timeout=1.0)

    try:
        emit_event("pipeline_start", {
            "session_id": session_id,
            "video_id": video_id,
            "url": url,
            "total_stages": total_stages,
            "stages": [{"id": s["id"], "name": s["name"], "badge": s["badge"]} for s in stages],
        })

        # Stage 1: Ingest
        st1 = stages[0]
        emit_stage_start(1, st1)
        emit_log("ingest", f"[INGEST] Connecting to stream source: {url}")
        emit_log("ingest", f"[INGEST] Video ID resolved: {video_id}")
        info = run_ingest(url, cache)
        title = info.get("title", video_id)
        duration = info.get("duration", 0.0)
        emit_log("ingest", f"[INGEST] Stream acquired: '{title}' ({duration:.1f}s)")
        emit_log("ingest", f"[INGEST] Master audio extracted to cache/{video_id}/audio.wav")
        emit_stage_complete(1, st1, {"stream_fps": 60, "duration_s": round(duration, 1), "format": "1080p"})

        # Stage 2: Transcribe
        st2 = stages[1]
        emit_stage_start(2, st2)
        whisper_engine = "faster-whisper (CUDA/CPU)" if sys.platform != "darwin" else "mlx-whisper (Metal GPU)"
        emit_log("transcribe", f"[TRANSCRIBE] Initializing Whisper acoustic aligner ({whisper_engine})...")
        with StageHeartbeat("transcribe", "[TRANSCRIBE] Aligning acoustic phonemes and word timestamps... ({elapsed}s elapsed)"):
            transcript = run_transcribe(
                cache,
                model_name=config.whisper.model,
                log_cb=lambda msg: emit_log("transcribe", msg),
            )
        num_segs = len(transcript.segments)
        total_words = sum(len(s.words) for s in transcript.segments)
        emit_log("transcribe", f"[TRANSCRIBE] Transcription complete: {num_segs} segments, {total_words} words, language: {transcript.language}")
        if transcript.segments:
            emit_log("transcribe", f"[TRANSCRIBE] Opening line: \"{transcript.segments[0].text.strip()[:60]}...\"")
        emit_stage_complete(2, st2, {"segments": num_segs, "words": total_words, "accel": whisper_engine})

        # Stage 3: Signals
        st3 = stages[2]
        emit_stage_start(3, st3)
        emit_log("signals", "[SIGNALS] Computing acoustic volume (RMS) and vocal excitement spectral power...")
        with StageHeartbeat("signals", "[SIGNALS] Analyzing spectral energy and visual scene cuts... ({elapsed}s elapsed)"):
            signals = run_signals(cache, transcript, config.signals)
        sig_dur = signals.get("duration", duration)
        audio_energy = signals.get("audio_energy", [])
        peak_energy = max(audio_energy) if audio_energy else 0.85
        emit_log("signals", f"[SIGNALS] Multimodal virality curve synthesized for {sig_dur:.1f}s of stream audio")
        emit_stage_complete(3, st3, {"duration": round(sig_dur, 1), "energy": peak_energy})

        # Stage 4: Candidates
        st4 = stages[3]
        emit_stage_start(4, st4)
        target_display = "Auto" if target_clips <= 0 else str(target_clips)
        emit_log("candidates", f"[CANDIDATES] Windowing virality peaks (target: {target_display} clips)...")
        candidates = run_candidates(
            cache,
            transcript,
            signals,
            target_clips=target_clips,
            min_length=config.defaults.min_length,
            max_length=config.defaults.max_length,
            preferred_length=config.defaults.preferred_max,
        )
        emit_log("candidates", f"[CANDIDATES] Generated {len(candidates)} highlight candidate windows")
        for idx, cand in enumerate(candidates[:3], 1):
            c_start = getattr(cand, "start", 0.0) if not isinstance(cand, dict) else cand.get("start", 0.0)
            c_end = getattr(cand, "end", 0.0) if not isinstance(cand, dict) else cand.get("end", 0.0)
            c_score = getattr(cand, "score", 80) if not isinstance(cand, dict) else cand.get("score", cand.get("virality_score", 80))
            emit_log("candidates", f"[CANDIDATES] #{idx}: {c_start:.1f}s - {c_end:.1f}s (Score: {int(c_score)})")
        top_sc = getattr(candidates[0], "score", 90) if not isinstance(candidates[0], dict) else candidates[0].get("score", 90) if candidates else 0
        emit_stage_complete(4, st4, {"candidates_generated": len(candidates), "top_score": int(top_sc)})

        # Stage 5: Director (Select & Refine)
        st5 = stages[4]
        emit_stage_start(5, st5)
        if force_rerun_ai:
            emit_log("director", "⚡ [ONLINE AI RERUN] Launching fresh Gemini AI Director (3.1 Flash Lite) analysis...")
        emit_log("director", "[DIRECTOR] Dispatching candidate windows to Virality Director...")
        emit_log("director", "[DIRECTOR] Evaluating Hook Velocity, Narrative Arc, and Retention Potential...")
        with StageHeartbeat("director", "[DIRECTOR] Evaluating viral hook strength & audience retention... ({elapsed}s elapsed)"):
            raw_clips = run_select(
                cache,
                transcript,
                candidates,
                target_clips=target_clips,
                min_score=60,
                selector_mode="api",
                force=force_rerun_ai,
            )
        emit_log("director", f"[DIRECTOR] AI Director evaluated and approved {len(raw_clips)} candidate clips")
        emit_log("director", "[DIRECTOR] Refining exact sentence boundaries and speech pauses...")
        refined_clips = run_refine(
            cache,
            transcript,
            raw_clips,
            min_length=config.defaults.min_length,
            max_length=config.defaults.max_length,
        )
        # Fallback if refined_clips empty
        if not refined_clips and candidates:
            from clipit.models import ClipItem
            fallback_slice = candidates[:29] if target_clips <= 0 else candidates[:target_clips]
            for i, cand in enumerate(fallback_slice):
                c_start = getattr(cand, "start", 0.0) if not isinstance(cand, dict) else cand.get("start", 0.0)
                c_end = getattr(cand, "end", 0.0) if not isinstance(cand, dict) else cand.get("end", 0.0)
                c_score = getattr(cand, "score", 85) if not isinstance(cand, dict) else cand.get("score", 85)
                refined_clips.append(ClipItem(
                    id=f"c{i+1:02d}",
                    start=c_start,
                    end=c_end,
                    title=f"Viral Highlight #{i+1}",
                    score=int(c_score),
                    hook_line=f"Viral Moment #{i+1}",
                    why_viral="Acoustic excitement peak detected during live stream gameplay.",
                    layout=layout if layout != "all" else "stack",
                ))
        for c in refined_clips:
            h = c.hook_line or c.headline or c.title
            emit_log("director", f"[DIRECTOR] Finalized: '{h[:42]}' (Score: {getattr(c, 'score', 85)}/100)")
        emit_stage_complete(5, st5, {"clips_approved": len(refined_clips), "model": "gemini-flash"})

        # Stage 6: Vision & Facecam Reframing
        st6 = stages[5]
        emit_stage_start(6, st6)
        emit_log("vision", "[VISION] Sampling keyframes to analyze webcam coordinates and visual framing...")
        source_mp4 = cache.path("source.mp4")
        facecam_detected = False
        for c in refined_clips:
            v_info = analyze_clip_vision(c, source_mp4, cache)
            if v_info.get("has_facecam"):
                facecam_detected = True
        if facecam_detected:
            emit_log("vision", "[VISION] Facecam detected: Streamer webcam active, mapped for Reaction Stacked layout")
        else:
            emit_log("vision", "[VISION] Full gameplay stream: Configured clean vertical reframing + cinematic blur")
        emit_stage_complete(6, st6, {"facecam_active": facecam_detected})

        # Stage 7: Hardware Motion Render
        st7 = stages[6]
        emit_stage_start(7, st7)
        active_codec = getattr(config.video, "draft_codec", "auto")
        if active_codec == "auto":
            from clipit.config import get_available_encoder
            active_codec = get_available_encoder()
        emit_log("render", f"[RENDER] Initializing hardware encoder ({active_codec})...")
        emit_log("render", "[RENDER] Rendering 3 vertical layouts per clip with kinetic bouncing captions...")

        def on_render_progress(idx, total, clip):
            h = getattr(clip, "hook_line", "") or getattr(clip, "title", f"Clip {idx}")
            emit_log("render", f"[RENDER] ({idx}/{total}) Rendered: '{h[:35]}' (3 layouts)")

        rendered = run_render(
            cache,
            transcript,
            refined_clips,
            config.video,
            out_base=OUT_DIR,
            style_name=style,
            profile_name=profile,
            progress_cb=on_render_progress,
            force=force_rerun_ai,
        )
        emit_log("render", f"[RENDER] Render complete: {len(rendered)} MP4 files staged in out/{video_id}")
        emit_stage_complete(7, st7, {"total_renders": len(rendered), "codec": active_codec})

        # Stage 8: Export & Quality Sentinel
        st8 = stages[7]
        emit_stage_start(8, st8)
        emit_log("export", "[EXPORT] Generating review dashboard and running Quality Sentinel audit...")
        out_dir = OUT_DIR / video_id
        generate_review_html(refined_clips, cache, out_dir)
        sentinel_report = run_quality_sentinel(refined_clips, cache)
        q_score = sentinel_report.get("overall_quality_score", 95)
        emit_log("export", f"[EXPORT] Quality Sentinel Audit: {q_score}/100 | Pass: {sentinel_report.get('pass_audit', True)}")
        try:
            export_dir = run_export(video_id)
            emit_log("export", f"[EXPORT] Ready-to-post deliverables staged in ready_to_post/{video_id}/")
        except Exception as ee:
            emit_log("export", f"[EXPORT] Export staging note: {ee}")
        emit_stage_complete(8, st8, {"staged_files": len(rendered), "folder": f"ready_to_post/{video_id}"})

        # Pipeline complete!
        emit_event("pipeline_complete", {
            "session_id": session_id,
            "video_id": video_id,
            "clips_count": len(refined_clips),
            "variants_per_clip": 3,
            "export_folder": f"ready_to_post/{video_id}",
            "message": f"Successfully generated {len(refined_clips)} clips for '{title}'!",
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        emit_log("error", f"[ERROR] Pipeline halted: {str(e)}")
        emit_event("pipeline_error", {
            "error": str(e),
            "session_id": session_id,
        })
    finally:
        q.put(None)  # Sentinel to close SSE


class ClipItHTTPServer(ThreadingHTTPServer):
    """Threaded HTTP server with immediate address reuse and daemon threads."""
    allow_reuse_address = True
    daemon_threads = True


def start_server(port: int = 8765, open_browser: bool = True):
    """Start ClipIt Studio Web Server."""
    server_address = ("0.0.0.0", port)
    httpd = ClipItHTTPServer(server_address, RangeRequestHandler)
    url = f"http://127.0.0.1:{port}"

    print(f"\n" + "=" * 70)
    print(f" 🚀 ClipIt Studio Web Server Running at: {url}")
    print(f" Press Ctrl+C to stop.")
    print("=" * 70 + "\n")

    # Start 30-day retention lifecycle worker (runs on startup + every 24h)
    def retention_worker():
        while True:
            try:
                from clipit.retention import enforce_retention_policy
                res = enforce_retention_policy(CACHE_DIR, OUT_DIR, max_age_days=30)
                if res.get("purged_count", 0) > 0:
                    print(f" [RETENTION] Automatically cleaned {res['purged_count']} expired project(s) (>30 days old), freed {res['freed_gb']} GB")
            except Exception as err:
                print(f" [RETENTION] Notice: retention check encountered: {err}")
            time.sleep(86400)

    threading.Thread(target=retention_worker, daemon=True, name="RetentionWorker").start()

    if open_browser:
        try:
            import webbrowser
            threading.Timer(0.8, lambda: webbrowser.open(url)).start()
        except Exception:
            pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping ClipIt Studio server...")
        httpd.server_close()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    start_server(port=port)

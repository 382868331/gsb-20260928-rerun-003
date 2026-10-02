"""基于标准库的 HTTP 服务：工程编辑 API + 可取消导出任务 + 前端静态托管。

不依赖 Flask 等第三方 Web 框架。导出在后台线程执行并原子替换文件，
取消时仅清理临时文件，不会在导出目录留下成品。
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .audio import ExportCancelled, write_wav_atomic, waveform_peaks
from .engine import render
from .model import Clip, Project, Track
from .store import ImportedMedia, Session


class ExportJob:
    def __init__(self, job_id: str, output_path: str):
        self.id = job_id
        self.output_path = output_path
        self.status = "running"   # running | done | cancelled | error
        self.progress = 0.0
        self.message = ""
        self.peak = 0.0
        self.peak_before_master = 0.0
        self.over_range = False
        self.over_count = 0
        self.over_ranges: list[dict] = []
        self.duck_ranges: list[dict] = []
        self._cancel = threading.Event()

    def request_cancel(self) -> None:
        self._cancel.set()

    def cancelled(self) -> bool:
        return self._cancel.is_set()


class StudioState:
    def __init__(self, session_root: str, frontend_dist: str | None = None):
        self.session_root = os.path.abspath(session_root)
        os.makedirs(self.session_root, exist_ok=True)
        project_file = os.path.join(self.session_root, "project.json")
        self.session = Session.open(self.session_root) if os.path.exists(project_file) \
            else Session.create(self.session_root)
        self.frontend_dist = frontend_dist
        self.lock = threading.Lock()
        self.jobs: dict[str, ExportJob] = {}

    def start_export(self, output_path: str, subtype: str,
                     start_time: float | None = None,
                     end_time: float | None = None,
                     include_tail: bool = False) -> ExportJob:
        job = ExportJob(uuid.uuid4().hex[:12], os.path.abspath(output_path))
        with self.lock:
            self.jobs[job.id] = job
        thread = threading.Thread(
            target=self._run_export,
            args=(job, subtype, start_time, end_time, include_tail), daemon=True,
        )
        thread.start()
        return job

    def _run_export(self, job: ExportJob, subtype: str,
                    start_time: float | None, end_time: float | None,
                    include_tail: bool) -> None:
        try:
            with self.lock:
                session = self.session
                project = Project.from_dict(session.project.to_dict())
            # 渲染用的是工程快照；取消或重复导出都不会改动已保存的工程，
            # 也不会把上一次导出的效果叠加进工程。
            result = render(
                project, session.load_source,
                start_time=0.0 if start_time is None else start_time,
                end_time=end_time,
                include_tail=include_tail,
                should_cancel=job.cancelled,
            )
            job.peak = result.peak
            job.peak_before_master = result.peak_before_master
            job.over_range = result.over_range
            job.over_count = result.over_count
            job.over_ranges = [
                {"start": r.start, "end": r.end, "peak": r.peak}
                for r in result.over_ranges
            ]
            job.duck_ranges = [
                {"start": r.start, "end": r.end}
                for r in result.duck_ranges
            ]
            write_wav_atomic(
                job.output_path, result.mix, result.sample_rate, subtype=subtype,
                should_cancel=job.cancelled,
                progress=lambda p: setattr(job, "progress", p),
            )
            if job.cancelled():
                job.status = "cancelled"
                job.message = "导出已取消，未生成成品文件"
            else:
                job.status = "done"
                job.progress = 1.0
                job.message = "导出完成"
        except ExportCancelled:
            job.status = "cancelled"
            job.message = "导出已取消，未生成成品文件"
        except Exception as exc:  # noqa: BLE001 - 服务层需要把错误回传前端
            job.status = "error"
            job.message = str(exc)

    def cancel_export(self, job_id: str) -> ExportJob | None:
        with self.lock:
            job = self.jobs.get(job_id)
        if job and job.status == "running":
            job.request_cancel()
        return job


def make_handler(state: StudioState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AudioStudio/1.0"

        def log_message(self, fmt: str, *args) -> None:  # 安静一点
            return

        # ---------- 工具 ----------
        def _send_json(self, payload, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def _error(self, message: str, status: int = 400) -> None:
            self._send_json({"error": message}, status)

        # ---------- 路由 ----------
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            route, query = parsed.path, parse_qs(parsed.query)
            if route == "/api/project":
                with state.lock:
                    self._send_json(state.session.project.to_dict())
            elif route == "/api/media/info":
                self._media_info(query)
            elif route == "/api/media/waveform":
                self._waveform(query)
            elif route.startswith("/api/export/"):
                self._export_status(route.rsplit("/", 1)[-1])
            else:
                self._serve_static(route)

        def do_POST(self) -> None:  # noqa: N802
            route = urlparse(self.path).path
            try:
                if route == "/api/project":
                    self._replace_project()
                elif route == "/api/media/import":
                    self._import_media()
                elif route == "/api/project/save":
                    with state.lock:
                        state.session.save()
                    self._send_json({"ok": True})
                elif route == "/api/render/preview":
                    self._render_preview()
                elif route == "/api/export":
                    self._start_export()
                elif route.startswith("/api/export/") and route.endswith("/cancel"):
                    self._cancel(route.split("/")[3])
                else:
                    self._error("未知接口", 404)
            except Exception as exc:  # noqa: BLE001
                self._error(str(exc), 400)

        # ---------- API 实现 ----------
        def _media_info(self, query: dict) -> None:
            source = query.get("source", [""])[0]
            from .audio import probe
            info = probe(state.session.media_abspath(source))
            self._send_json({
                "source": source,
                "sample_rate": info.sample_rate,
                "channels": info.channels,
                "frames": info.frames,
                "duration": info.duration,
            })

        def _replace_project(self) -> None:
            data = self._read_json()
            project = Project.from_dict(data)
            with state.lock:
                state.session.project = project
                state.session.clear_cache()
            self._send_json({"ok": True})

        def _import_media(self) -> None:
            data = self._read_json()
            src = os.path.abspath(data["path"])
            if not os.path.isfile(src):
                self._error(f"文件不存在: {src}", 404)
                return
            with state.lock:
                imported: ImportedMedia = state.session.import_file(src, data.get("name"))
            self._send_json({
                "source": imported.relpath,
                "original_name": imported.original_name,
                "sample_rate": imported.info.sample_rate,
                "channels": imported.info.channels,
                "duration": imported.info.duration,
            })

        def _waveform(self, query: dict) -> None:
            source = query.get("source", [""])[0]
            buckets = int(query.get("buckets", ["1000"])[0])
            with state.lock:
                data = state.session.load_source(source)
            self._send_json({"peaks": waveform_peaks(data, buckets)})

        def _render_preview(self) -> None:
            data = self._read_json()
            project = Project.from_dict(data) if data else state.session.project
            with state.lock:
                snapshot = Project.from_dict(project.to_dict())
                loader = state.session.load_source
            result = render(snapshot, loader)
            self._send_json({
                "duration": result.duration,
                "peak": result.peak,
                "peak_before_master": result.peak_before_master,
                "over_range": result.over_range,
                "over_count": result.over_count,
                "over_ranges": [
                    {"start": r.start, "end": r.end} for r in result.over_ranges
                ],
                "duck_ranges": [
                    {"start": r.start, "end": r.end} for r in result.duck_ranges
                ],
            })

        def _start_export(self) -> None:
            data = self._read_json()
            output = os.path.abspath(data["output_path"])
            start_time = data.get("start_time")
            end_time = data.get("end_time")
            start_time = None if start_time is None else float(start_time)
            end_time = None if end_time is None else float(end_time)
            include_tail = bool(data.get("include_tail", False))
            if not data.get("allow_over_range", False):
                with state.lock:
                    snapshot = Project.from_dict(state.session.project.to_dict())
                    loader = state.session.load_source
                result = render(snapshot, loader, start_time=start_time or 0.0,
                                end_time=end_time, include_tail=include_tail)
                if result.over_range:
                    self._send_json({
                        "over_range": True,
                        "over_count": result.over_count,
                        "peak_before_master": result.peak_before_master,
                        "over_ranges": [
                            {"start": r.start, "end": r.end} for r in result.over_ranges
                        ],
                        "duck_ranges": [
                            {"start": r.start, "end": r.end} for r in result.duck_ranges
                        ],
                        "message": "叠加超出输出范围，确认降低增益后再导出，或勾选强制导出",
                    }, status=409)
                    return
            subtype = data.get("subtype", "PCM_16")
            job = state.start_export(output, subtype, start_time, end_time, include_tail)
            self._send_json({"job_id": job.id, "output_path": job.output_path})

        def _export_status(self, job_id: str) -> None:
            with state.lock:
                job = state.jobs.get(job_id)
            if not job:
                self._error("任务不存在", 404)
                return
            self._send_json({
                "id": job.id,
                "status": job.status,
                "progress": job.progress,
                "message": job.message,
                "peak": job.peak,
                "peak_before_master": job.peak_before_master,
                "over_range": job.over_range,
                "over_count": job.over_count,
                "over_ranges": job.over_ranges,
                "duck_ranges": job.duck_ranges,
                "output_path": job.output_path if job.status == "done" else None,
            })

        def _cancel(self, job_id: str) -> None:
            job = state.cancel_export(job_id)
            if not job:
                self._error("任务不存在", 404)
                return
            self._send_json({"ok": True, "status": "cancelling"})

        # ---------- 静态前端 ----------
        def _serve_static(self, route: str) -> None:
            if not state.frontend_dist or not os.path.isdir(state.frontend_dist):
                self._error("前端尚未构建：请在 frontend/ 下 npm install && npm run build", 404)
                return
            rel = "index.html" if route == "/" else route.lstrip("/")
            candidate = os.path.abspath(os.path.join(state.frontend_dist, rel))
            if not candidate.startswith(os.path.abspath(state.frontend_dist) + os.sep):
                self._error("非法路径", 403)
                return
            if not os.path.isfile(candidate):
                candidate = os.path.join(state.frontend_dist, "index.html")
            ctype = {
                ".html": "text/html; charset=utf-8",
                ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".json": "application/json",
                ".svg": "image/svg+xml",
            }.get(os.path.splitext(candidate)[1], "application/octet-stream")
            with open(candidate, "rb") as handle:
                body = handle.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def run(host: str = "127.0.0.1", port: int = 8000,
        session_root: str = "session", frontend_dist: str | None = None) -> None:
    state = StudioState(session_root, frontend_dist)
    httpd = ThreadingHTTPServer((host, port), make_handler(state))
    print(f"声音后期制作工作室: http://{host}:{port}")
    print(f"工程目录: {state.session_root}")
    httpd.serve_forever()

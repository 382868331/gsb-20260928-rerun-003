"""导出任务管理：可查询进度、可取消，取消即删除半成品文件。"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .assets import AssetLibrary
from .engine import RenderCancelled, render
from .models import Project


@dataclass
class Job:
    id: str
    output_path: str
    status: str = "running"  # running | done | cancelled | error
    progress: float = 0.0
    result: dict | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    _cancel: threading.Event = field(default_factory=threading.Event)


class JobManager:
    def __init__(self, library: AssetLibrary, export_dir: str | Path):
        self.library = library
        self.export_dir = Path(export_dir)
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def start(self, job_id: str, project: Project, filename: str | None = None) -> Job:
        filename = filename or f"mix_{job_id}.wav"
        # 防止路径穿越
        filename = Path(filename).name
        out = self.export_dir / filename
        job = Job(id=job_id, output_path=str(out))
        with self._lock:
            self._jobs[job_id] = job
        thread = threading.Thread(target=self._run, args=(job, project), daemon=True)
        thread.start()
        return job

    def _run(self, job: Job, project: Project) -> None:
        try:
            job.result = render(
                project,
                self.library,
                job.output_path,
                cancel_check=lambda: self._raise_if_cancelled(job),
                progress=lambda p: setattr(job, "progress", round(p, 4)),
            )
            job.progress = 1.0
            job.status = "done"
        except RenderCancelled:
            job.status = "cancelled"
            Path(job.output_path).unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            job.error = str(exc)
            Path(job.output_path).unlink(missing_ok=True)
            Path(job.output_path + ".part").unlink(missing_ok=True)

    @staticmethod
    def _raise_if_cancelled(job: Job) -> None:
        if job._cancel.is_set():
            raise RenderCancelled()

    def cancel(self, job_id: str) -> Job | None:
        job = self.get(job_id)
        if job is None:
            return None
        job._cancel.set()
        # 同步兜底：删除可能已留下的半成品
        Path(job.output_path).unlink(missing_ok=True)
        Path(job.output_path + ".part").unlink(missing_ok=True)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def snapshot(self, job: Job) -> dict:
        return {
            "id": job.id,
            "status": job.status,
            "progress": job.progress,
            "output_path": job.output_path,
            "result": job.result,
            "error": job.error,
        }

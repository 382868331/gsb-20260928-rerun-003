"""FastAPI 服务：素材上传、工程存取、混音导出。"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .assets import AssetLibrary
from .jobs import JobManager
from .models import Project, new_id
from .project import load_project, save_project

DATA_ROOT = Path(os.environ.get("STUDIO_DATA", Path(__file__).resolve().parents[2] / "data"))
PROJECT_PATH = DATA_ROOT / "project.json"

library = AssetLibrary(DATA_ROOT)
jobs = JobManager(library, DATA_ROOT / "exports")

app = FastAPI(title="声音后期制作工作室", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/project")
def get_project() -> dict:
    if PROJECT_PATH.exists():
        return load_project(PROJECT_PATH).model_dump()
    return Project().model_dump()


@app.put("/api/project")
def put_project(project: Project) -> dict:
    save_project(project, PROJECT_PATH)
    return {"ok": True, "updated_at": project.updated_at}


@app.get("/api/assets")
def list_assets() -> list[dict]:
    return [m.model_dump() for m in library.list_assets()]


@app.post("/api/assets")
async def upload_asset(file: UploadFile = File(...), sample_rate: int = 48000) -> dict:
    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
    try:
        meta = library.import_file(tmp_path, sample_rate)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"无法读取音频: {exc}") from exc
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return meta.model_dump()


@app.post("/api/render")
def start_render(project: Project) -> dict:
    job_id = new_id()
    job = jobs.start(job_id, project)
    return jobs.snapshot(job)


@app.get("/api/render/{job_id}")
def render_status(job_id: str) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="导出任务不存在")
    return jobs.snapshot(job)


@app.post("/api/render/{job_id}/cancel")
def cancel_render(job_id: str) -> dict:
    job = jobs.cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="导出任务不存在")
    return {"ok": True, "id": job_id}


@app.get("/api/render/{job_id}/download")
def download_render(job_id: str):
    job = jobs.get(job_id)
    if job is None or job.status != "done":
        raise HTTPException(status_code=404, detail="成品不存在")
    path = Path(job.output_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="成品文件缺失")
    return FileResponse(path, media_type="audio/wav", filename=path.name)

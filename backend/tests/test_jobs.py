import time
from pathlib import Path

from studio.jobs import JobManager
from studio.models import Clip, Project, Track


def _wait(job, timeout=5.0):
    deadline = time.time() + timeout
    while job.status == "running" and time.time() < deadline:
        time.sleep(0.01)
    return job


def test_render_job_done(tone_assets, library, tmp_path):
    low = tone_assets[0]
    project = Project(
        sample_rate=48000,
        tracks=[Track(clips=[Clip(asset_id=low.id, offset=0, start=0, duration=1.0)])],
    )
    mgr = JobManager(library, tmp_path / "exports")
    job = mgr.start("j1", project, "song.wav")
    _wait(job)
    assert job.status == "done"
    assert Path(job.output_path).exists()


def test_cancel_job_removes_file(tone_assets, library, tmp_path):
    low = tone_assets[0]
    project = Project(
        sample_rate=48000,
        tracks=[
            Track(clips=[Clip(asset_id=low.id, offset=0, start=0, duration=1.0)]),
            Track(clips=[Clip(asset_id=low.id, offset=0, start=0, duration=1.0)]),
        ],
    )
    mgr = JobManager(library, tmp_path / "exports")
    job = mgr.start("j2", project, "song2.wav")
    mgr.cancel("j2")
    _wait(job)
    assert job.status == "cancelled"
    assert not Path(job.output_path).exists()
    # 下载目录里没有任何冒充成品的 wav
    assert not list((tmp_path / "exports").glob("*.wav"))

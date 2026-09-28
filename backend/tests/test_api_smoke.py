"""端到端冒烟（直接调用路由函数，避开沙箱内 ASGI 传输层的线程限制）：
上传三段人工声音 → 排时间线 → 保存/重开 → 导出 → 取消。
"""
import io
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.datastructures import UploadFile

import studio.main as mm
from studio.models import Clip, Project, Track


@pytest.fixture(autouse=True)
def _fresh_data(tmp_path, monkeypatch):
    from studio.assets import AssetLibrary
    from studio.jobs import JobManager

    monkeypatch.setattr(mm, "DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(mm, "PROJECT_PATH", tmp_path / "data" / "project.json")
    monkeypatch.setattr(mm, "library", AssetLibrary(tmp_path / "data"))
    monkeypatch.setattr(mm, "jobs", JobManager(mm.library, tmp_path / "data" / "exports"))


def _tone(freq, sr, duration=1.0):
    t = np.arange(int(sr * duration)) / sr
    return (0.4 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


async def _upload(path):
    f = UploadFile(filename=path.name, file=io.BytesIO(path.read_bytes()))
    return await mm.upload_asset(file=f, sample_rate=48000)


def _wait(job_id, n=200):
    for _ in range(n):
        st = mm.jobs.snapshot(mm.jobs.get(job_id))
        if st["status"] != "running":
            return st
        time.sleep(0.02)
    raise AssertionError("导出任务超时")


def test_full_workflow(tmp_path):
    import asyncio

    low_p, mid_p, high_p = (tmp_path / n for n in ("l.wav", "m.wav", "h.wav"))
    sf.write(low_p, _tone(220, 44100), 44100)
    sf.write(mid_p, _tone(440, 48000), 48000)
    sf.write(high_p, _tone(900, 48000, 0.5), 48000)
    low, mid, high = [asyncio.run(_upload(p)) for p in (low_p, mid_p, high_p)]
    assert low["sample_rate"] == 48000 and low["source_sample_rate"] == 44100
    assert len(mm.list_assets()) == 3

    project = Project(
        name="demo",
        tracks=[
            Track(name="低频", clips=[Clip(asset_id=low["id"], offset=0, start=0, duration=1.0)]),
            Track(name="中频", clips=[Clip(asset_id=mid["id"], offset=1.0, start=0, duration=1.0)]),
            Track(name="高频", clips=[Clip(asset_id=high["id"], offset=0.5, start=0, duration=0.5)]),
        ],
        asset_ids=[low["id"], mid["id"], high["id"]],
    )
    mm.put_project(project)
    reopened = Project.model_validate(mm.get_project())
    assert reopened.name == "demo"
    assert reopened.tracks[0].clips[0].offset == 0

    started = mm.start_render(reopened)
    st = _wait(started["id"])
    assert st["status"] == "done", st
    assert st["result"]["duration"] == pytest.approx(2.0, abs=1e-3)

    resp = mm.download_render(started["id"])
    assert resp.status_code == 200
    out = tmp_path / "mix.wav"
    out.write_bytes(Path(resp.path).read_bytes())
    audio, sr = sf.read(out)

    def dom(a):
        sp = np.abs(np.fft.rfft(a.mean(axis=1)))
        fr = np.fft.rfftfreq(len(a), 1 / sr)
        m = fr >= 100
        return fr[m][np.argmax(sp[m])]

    # 时间位置核对：开头 220Hz；1.5~2s 只剩 440Hz
    assert abs(dom(audio[: sr // 2]) - 220) < 20
    assert abs(dom(audio[int(1.5 * sr) :]) - 440) < 20


def test_cancel_export_leaves_no_file(tmp_path):
    import asyncio

    p = tmp_path / "loud.wav"
    sf.write(p, _tone(300, 48000), 48000)
    a = asyncio.run(_upload(p))
    project = Project(
        tracks=[Track(name="x", clips=[Clip(asset_id=a["id"], offset=0, start=0, duration=1)])]
    )
    started = mm.start_render(project)
    mm.cancel_render(started["id"])
    st = _wait(started["id"])
    assert st["status"] == "cancelled"
    assert not Path(st["output_path"]).exists()
    with pytest.raises(Exception):
        mm.download_render(started["id"])


def test_clipping_warning(tmp_path):
    import asyncio

    p = tmp_path / "loud.wav"
    sf.write(p, _tone(300, 48000) + 0.9, 48000)
    a = asyncio.run(_upload(p))
    project = Project(
        tracks=[
            Track(name="a", clips=[Clip(id="c1", asset_id=a["id"], offset=0, start=0, duration=1)]),
            Track(name="b", clips=[Clip(id="c2", asset_id=a["id"], offset=0, start=0, duration=1)]),
        ]
    )
    st = _wait(mm.start_render(project)["id"])
    assert st["result"]["clipped"] is True
    assert "削波" in st["result"]["warnings"][0]

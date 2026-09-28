import numpy as np
import pytest
import soundfile as sf

from conftest import dominant_freq, energy
from studio.engine import RenderCancelled, render, render_to_array
from studio.models import Clip, Project, Track


def _project(tone_assets):
    low, mid, high = tone_assets
    return Project(
        sample_rate=48000,
        tracks=[
            Track(name="A", clips=[Clip(asset_id=low.id, offset=0.0, start=0.0, duration=1.0)]),
            Track(name="B", clips=[Clip(asset_id=mid.id, offset=1.0, start=0.0, duration=1.0)]),
            Track(name="C", clips=[Clip(asset_id=high.id, offset=0.5, start=0.0, duration=0.5)]),
        ],
    )


def test_clip_positions_and_frequencies(tone_assets, library):
    project = _project(tone_assets)
    master, info = render_to_array(project, library)
    sr = 48000
    # 总时长 = 最后一段结束时刻 2.0s
    assert master.shape[0] == int(2.0 * sr)

    seg = lambda a, b: master[int(a * sr) : int(b * sr)]
    # 0~0.5s 只有 220Hz 轨
    assert abs(dominant_freq(seg(0.0, 0.5), sr) - 220) < 15
    # 0.5~1.0s 是 220 + 880 叠加，两个峰都应明显
    spec = np.abs(np.fft.rfft(seg(0.5, 1.0).mean(axis=1)))
    freqs = np.fft.rfftfreq(seg(0.5, 1.0).shape[0], 1 / sr)
    for target in (220, 880):
        assert spec[np.argmin(np.abs(freqs - target))] > 0.3 * spec.max()
    # 1.0~2.0s 只有重采样来的 440Hz 素材
    assert abs(dominant_freq(seg(1.0, 2.0), sr) - 440) < 15


def test_mute_and_solo(tone_assets, library):
    project = _project(tone_assets)
    sr = 48000
    project.tracks[0].muted = True
    master, _ = render_to_array(project, library)
    # 开头只剩静音（C 从 0.5 开始）
    assert energy(master[: int(0.5 * sr)]) < 1e-6

    project.tracks[0].muted = False
    project.tracks[1].solo = True
    master, _ = render_to_array(project, library)
    # 独听 B：0~1s 静音，1s 后有声音
    assert energy(master[: int(1.0 * sr)]) < 1e-6
    assert energy(master[int(1.0 * sr) :]) > 0.1


def test_trim_is_non_destructive(tone_assets, library, tmp_path):
    low = tone_assets[0]
    raw = library.load_audio(low.id).copy()
    project = Project(
        sample_rate=48000,
        tracks=[Track(clips=[Clip(asset_id=low.id, offset=0.0, start=0.3, duration=0.4)])],
    )
    out = tmp_path / "mix.wav"
    result = render(project, library, out)
    assert result["duration"] == pytest.approx(0.4, abs=1e-3)
    # 素材缓存没有被裁切改动
    assert np.array_equal(raw, library.load_audio(low.id))


def test_project_roundtrip_renders_identical(tone_assets, library, tmp_path):
    from studio.project import load_project, save_project

    project = _project(tone_assets)
    p = tmp_path / "project.json"
    save_project(project, p)
    reopened = load_project(p)
    a, _ = render_to_array(project, library)
    b, _ = render_to_array(reopened, library)
    assert np.array_equal(a, b)


def test_clipping_detected_on_overflow(library, tmp_path):
    import soundfile as sf
    from conftest import make_tone

    sr = 48000
    p = tmp_path / "loud.wav"
    sf.write(p, make_tone(330, sr, 0.2, amp=0.9), sr)
    meta = library.import_file(p, sr)
    project = Project(
        sample_rate=sr,
        tracks=[
            Track(clips=[Clip(asset_id=meta.id, offset=0, start=0, duration=0.2)]),
            Track(clips=[Clip(asset_id=meta.id, offset=0, start=0, duration=0.2)]),
        ],
    )
    out = tmp_path / "mix.wav"
    result = render(project, library, out)
    assert result["clipped"] is True
    assert result["peak"] > 1.0
    assert "削波" in result["warnings"][0]
    data, _ = sf.read(out)
    assert np.abs(data).max() <= 1.0 + 1e-6


def test_cancel_leaves_no_output(tone_assets, library, tmp_path):
    project = _project(tone_assets)
    out = tmp_path / "nope.wav"
    calls = {"n": 0}

    def cancel_check():
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RenderCancelled()

    with pytest.raises(RenderCancelled):
        render(project, library, out, cancel_check=cancel_check)
    assert not out.exists()
    assert not (tmp_path / "nope.wav.part").exists()


def test_empty_project_rejected(tone_assets, library, tmp_path):
    with pytest.raises(ValueError):
        render(Project(sample_rate=48000), library, tmp_path / "x.wav")

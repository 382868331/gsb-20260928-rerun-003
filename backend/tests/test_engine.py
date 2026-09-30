"""核对：时间位置、混音结果、静音/独听、裁切非破坏、采样率统一、溢出提示、取消。"""

from __future__ import annotations

import os
import sys

import numpy as np
import soundfile as sf
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from studio.audio import ExportCancelled, write_wav_atomic
from studio.engine import render
from studio.model import AutomationPoint, Clip, Project, Track
from studio.store import Session


def _tone(path: str, freq: float, duration: float, sr: int = 44100) -> None:
    t = np.arange(int(duration * sr)) / sr
    sig = (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    sf.write(path, sig[:, None], sr, subtype="PCM_16")


@pytest.fixture()
def session(tmp_path):
    sess = Session.create(str(tmp_path / "session"), sample_rate=44100)
    _tone(str(tmp_path / "a440.wav"), 440, 1.0, sr=44100)
    _tone(str(tmp_path / "b880.wav"), 880, 1.0, sr=22050)
    sess.import_file(str(tmp_path / "a440.wav"), "a440.wav")
    sess.import_file(str(tmp_path / "b880.wav"), "b880.wav")
    return sess


def _sources_by_freq(sess):
    files = [f for f in os.listdir(sess.media_dir) if f.endswith(".wav")]
    profiled = []
    for name in files:
        seg = sess.load_source(name)[1000:5000, 0]
        zc = int(np.sum(np.diff(np.signbit(seg))))
        profiled.append((zc, name))
    profiled.sort()
    return profiled[0][1], profiled[1][1]  # 低频(440), 高频(880)


def _zero_crossings(x):
    return int(np.sum(np.diff(np.signbit(x))))


def test_placement_and_timing(session):
    src_a, src_b = _sources_by_freq(session)
    p = session.project
    p.tracks = [
        Track("t1", "A", clips=[Clip("c1", src_a, start=0.0, in_point=0.0, out_point=1.0)]),
        Track("t2", "B", clips=[Clip("c2", src_b, start=1.0, in_point=0.0, out_point=1.0)]),
    ]
    result = render(p, session.load_source)
    assert result.sample_rate == 44100
    assert result.duration == pytest.approx(2.0, abs=1e-3)
    seg_a = result.mix[int(0.5 * 44100) - 2205:int(0.5 * 44100) + 2205, 0]
    seg_b = result.mix[int(1.5 * 44100) - 2205:int(1.5 * 44100) + 2205, 0]
    assert _zero_crossings(seg_b) > _zero_crossings(seg_a) * 1.5
    assert np.max(np.abs(result.mix[:4410])) > 0
    assert np.max(np.abs(result.mix[int(1.9 * 44100):])) > 0


def test_resample_unified(session):
    _src_a, src_b = _sources_by_freq(session)
    data = session.load_source(src_b)
    assert abs(data.shape[0] - 44100) < 100


def test_trim_move_and_automation_work_at_48k(tmp_path):
    sess = Session.create(str(tmp_path / "session48"), sample_rate=48000)
    _tone(str(tmp_path / "pulse.wav"), 660, 1.0, sr=96000)
    imported = sess.import_file(str(tmp_path / "pulse.wav"), "pulse.wav")
    clip = Clip(
        "c1", imported.relpath, start=0.25, in_point=0.1, out_point=0.9,
        volume_points=[AutomationPoint(0.0, 0.0), AutomationPoint(0.4, 1.0)],
    )
    sess.project.tracks = [Track("t1", effects=[{"type": "delay", "time": 0.1, "mix": 0.2}], clips=[clip])]
    result = render(sess.project, sess.load_source)
    assert result.sample_rate == 48000
    assert result.duration == pytest.approx(1.05, abs=1e-4)
    assert np.max(np.abs(result.mix[:int(0.24 * 48000)])) == 0.0
    sess.save()
    reopened = Session.open(sess.root)
    assert np.allclose(result.mix, render(reopened.project, reopened.load_source).mix, atol=1e-6)


def test_mix_sum(session):
    src_a, src_b = _sources_by_freq(session)
    p = session.project
    p.tracks = [
        Track("t1", clips=[Clip("c1", src_a, 0, 0, 1.0)]),
        Track("t2", clips=[Clip("c2", src_b, 0, 0, 1.0)]),
    ]
    result = render(p, session.load_source)
    assert result.peak_before_master < 1.01


def test_over_range_reported(session):
    src_a, src_b = _sources_by_freq(session)
    p = session.project
    p.tracks = [
        Track("t1", clips=[Clip("c1", src_a, 0, 0, 1.0, gain_db=12)]),
        Track("t2", clips=[Clip("c2", src_b, 0, 0, 1.0, gain_db=12)]),
    ]
    result = render(p, session.load_source)
    assert result.over_range
    assert result.over_count > 0
    assert result.over_ranges
    assert result.over_ranges[0].start >= 0.0


def test_mute_and_solo(session):
    src_a, src_b = _sources_by_freq(session)
    p = session.project
    t1 = Track("t1", clips=[Clip("c1", src_a, 0, 0, 1.0)])
    t2 = Track("t2", clips=[Clip("c2", src_b, 0, 0, 1.0)], solo=True)
    p.tracks = [t1, t2]
    seg = render(p, session.load_source).mix[2000:6000, 0]
    assert _zero_crossings(seg) > 100  # 听到的是 880 而不是 440
    t2.solo = False
    t2.muted = True
    seg2 = render(p, session.load_source).mix[2000:6000, 0]
    assert _zero_crossings(seg2) < _zero_crossings(seg)


def test_mute_and_solo_do_not_change_export_length(session):
    src_a, src_b = _sources_by_freq(session)
    p = session.project
    p.tracks = [
        Track("t1", clips=[Clip("c1", src_a, start=0.0, in_point=0.0, out_point=1.0)]),
        Track("t2", clips=[Clip("c2", src_b, start=1.0, in_point=0.0, out_point=1.0)], muted=True),
    ]
    assert render(p, session.load_source).duration == pytest.approx(2.0)
    p.tracks[0].solo = True
    assert render(p, session.load_source).duration == pytest.approx(2.0)
    assert np.max(np.abs(render(p, session.load_source).mix[int(1.05 * 44100):])) == 0.0


def test_clip_automation_uses_clip_relative_time(session):
    src_a, _src_b = _sources_by_freq(session)
    clip = Clip(
        "c1", src_a, start=0.0, in_point=0.0, out_point=1.0,
        volume_points=[AutomationPoint(0.0, 0.0), AutomationPoint(0.5, 1.0)],
        pan_points=[AutomationPoint(0.0, -1.0), AutomationPoint(1.0, 1.0)],
    )
    p = session.project
    p.tracks = [Track("t1", clips=[clip])]
    first = render(p, session.load_source).mix
    assert abs(float(first[100, 0])) > abs(float(first[100, 1]))
    right_sample = int(0.87 * 44100)
    assert abs(float(first[right_sample, 1])) > abs(float(first[right_sample, 0]))

    clip.start = 0.75
    moved = render(p, session.load_source).mix
    assert np.max(np.abs(moved[:int(0.75 * 44100)])) == 0.0
    assert np.allclose(first[:44100], moved[int(0.75 * 44100):int(1.75 * 44100)], atol=1e-6)


def test_track_automation_remains_on_project_time(session):
    src_a, _src_b = _sources_by_freq(session)
    clip = Clip("c1", src_a, start=0.0, in_point=0.0, out_point=1.0)
    track = Track(
        "t1", clips=[clip],
        volume_points=[AutomationPoint(0.0, 0.0), AutomationPoint(0.5, 1.0)],
        pan_points=[AutomationPoint(0.0, -1.0), AutomationPoint(1.0, 1.0)],
    )
    p = session.project
    p.tracks = [track]
    clip.start = 0.5
    result = render(p, session.load_source).mix
    start = int(0.55 * 44100)
    end = int(0.95 * 44100)
    assert float(np.mean(np.abs(result[start:end, 1]))) > float(np.mean(np.abs(result[start:end, 0])))


def test_track_effect_amount_automation_and_reexport_is_stable(session):
    src_a, _src_b = _sources_by_freq(session)
    before_source = session.load_source(src_a).copy()
    p = session.project
    p.tracks = [Track(
        "t1",
        effects=[{
            "type": "gain",
            "gain_db": -24,
            "amount_points": [
                {"time": 0.0, "value": 0.0},
                {"time": 0.499, "value": 0.0},
                {"time": 0.5, "value": 1.0},
                {"time": 0.75, "value": 1.0},
                {"time": 0.751, "value": 0.0},
            ],
        }],
        clips=[Clip("c1", src_a, start=0.0, in_point=0.0, out_point=1.0)],
    )]
    first = render(p, session.load_source).mix
    second = render(p, session.load_source).mix
    assert np.allclose(first, second, atol=0.0)
    assert np.allclose(session.load_source(src_a), before_source, atol=0.0)
    assert np.mean(np.abs(first[int(0.2 * 44100):int(0.3 * 44100)])) > 0.2
    assert np.mean(np.abs(first[int(0.55 * 44100):int(0.7 * 44100)])) < 0.05
    assert np.mean(np.abs(first[int(0.85 * 44100):int(0.95 * 44100)])) > 0.2


def test_cancelled_render_leaves_project_data_unchanged(session):
    src_a, _src_b = _sources_by_freq(session)
    p = session.project
    p.tracks = [Track("t1", clips=[Clip("c1", src_a, start=0.0, in_point=0.0, out_point=1.0)])]
    before = p.to_dict()
    with pytest.raises(ExportCancelled):
        render(p, session.load_source, should_cancel=lambda: True)
    assert p.to_dict() == before


def test_trim_is_nondestructive(session):
    src_a, _src_b = _sources_by_freq(session)
    original, _ = sf.read(os.path.join(session.media_dir, src_a))
    clip = Clip("c1", src_a, start=2.0, in_point=0.0, out_point=1.0)
    clip.trim(in_point=0.25, out_point=0.75)
    p = session.project
    p.tracks = [Track("t1", clips=[clip])]
    assert render(p, session.load_source).duration == pytest.approx(2.5, abs=1e-3)
    again, _ = sf.read(os.path.join(session.media_dir, src_a))
    assert np.array_equal(original, again)
    clip.trim(in_point=0.0, out_point=1.0)
    assert render(p, session.load_source).duration == pytest.approx(3.0, abs=1e-3)


def test_save_restore_roundtrip(session):
    src_a, src_b = _sources_by_freq(session)
    session.project.tracks = [
        Track("t1", "A", clips=[Clip(
            "c1", src_a, 0.3, 0.1, 0.8, gain_db=-3,
            volume_points=[AutomationPoint(0.0, 0.2), AutomationPoint(0.5, 1.0)],
            pan_points=[AutomationPoint(0.0, -1.0)],
        )], effects=[{
            "type": "delay", "time": 0.1, "feedback": 0.1, "mix": 0.2,
            "amount_points": [{"time": 0.0, "value": 0.0}, {"time": 1.0, "value": 1.0}],
        }], volume_points=[AutomationPoint(0.0, 1.0)], pan_points=[AutomationPoint(0.0, 0.0)]),
        Track("t2", "B", muted=True, clips=[Clip("c2", src_b, 1.2, 0.0, 1.0)]),
    ]
    session.save()
    before = render(session.project, session.load_source).mix
    reopened = Session.open(session.root)
    after = render(reopened.project, reopened.load_source).mix
    assert before.shape == after.shape
    assert np.allclose(before, after, atol=1e-6)


def test_export_cancel_leaves_no_file(tmp_path):
    sr = 44100
    data = np.zeros((sr * 5, 1), dtype=np.float32)
    out = tmp_path / "mix.wav"
    with pytest.raises(ExportCancelled):
        write_wav_atomic(str(out), data, sr, should_cancel=lambda: True)
    assert not out.exists()
    assert not list(tmp_path.glob(".export-*"))


def test_export_cancel_during_chunked_write_removes_partial(tmp_path):
    sr = 44100
    data = np.linspace(-0.2, 0.2, sr * 5, dtype=np.float32)[:, None]
    out = tmp_path / "chunked.wav"
    checks = iter([False, False, True])
    with pytest.raises(ExportCancelled):
        write_wav_atomic(str(out), data, sr, should_cancel=lambda: next(checks))
    assert not out.exists()
    assert not list(tmp_path.glob(".export-*"))


def test_chunked_export_preserves_all_samples(tmp_path):
    sr = 44100
    frames = int(sr * 2.5)
    data = (0.1 * np.sin(2 * np.pi * np.arange(frames) / 32)).astype(np.float32)[:, None]
    out = tmp_path / "complete.wav"
    write_wav_atomic(str(out), data, sr)
    loaded, loaded_sr = sf.read(str(out), always_2d=True)
    assert loaded_sr == sr
    assert loaded.shape == data.shape
    assert np.allclose(loaded, data, atol=1e-4)


def test_export_success(tmp_path):
    sr = 44100
    data = (0.2 * np.ones((sr // 2, 2), dtype=np.float32))
    out = tmp_path / "mix.wav"
    write_wav_atomic(str(out), data, sr)
    loaded, loaded_sr = sf.read(str(out))
    assert loaded_sr == sr
    assert loaded.shape == data.shape

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
from studio.model import Clip, Project, Track
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
        Track("t1", "A", clips=[Clip("c1", src_a, 0.3, 0.1, 0.8, gain_db=-3)]),
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


def test_export_success(tmp_path):
    sr = 44100
    data = (0.2 * np.ones((sr // 2, 2), dtype=np.float32))
    out = tmp_path / "mix.wav"
    write_wav_atomic(str(out), data, sr)
    loaded, loaded_sr = sf.read(str(out))
    assert loaded_sr == sr
    assert loaded.shape == data.shape

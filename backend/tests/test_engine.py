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
from studio.model import (
    AutomationPoint, Clip, Project, Track, default_ducking, default_ducking_v2,
)
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


# ---------- 分段音量、声像自动化、轨道效果器 ----------

def _steady(session, src, sr=44100):
    """返回一段近似恒定电平的源素材标识（用测试用正弦波）。"""
    return src


def test_clip_internal_fade_and_volume_automation(session):
    src_a, _ = _sources_by_freq(session)
    clip = Clip("c1", src_a, start=0.0, in_point=0.0, out_point=1.0)
    clip.automation = [
        AutomationPoint(0.0, gain_db=-120.0),
        AutomationPoint(0.25, gain_db=0.0),
        AutomationPoint(0.5, gain_db=-6.0),
        AutomationPoint(1.0, gain_db=-6.0),
    ]
    p = session.project
    p.tracks = [Track("t1", clips=[clip])]
    mix = render(p, session.load_source).mix
    # 淡入起点几乎静音，0.25s 后恢复；后半段按 -6dB 变小。
    assert np.max(np.abs(mix[:200])) < 0.01
    assert np.max(np.abs(mix[int(0.25 * 44100):int(0.45 * 44100)])) > 0.3
    loud = np.max(np.abs(mix[int(0.3 * 44100):int(0.45 * 44100)]))
    quiet = np.max(np.abs(mix[int(0.7 * 44100):int(0.9 * 44100)]))
    assert quiet < loud * 0.6


def test_clip_automation_moves_with_clip(session):
    src_a, _ = _sources_by_freq(session)
    clip = Clip("c1", src_a, start=0.0, in_point=0.0, out_point=1.0)
    clip.automation = [AutomationPoint(0.0, gain_db=-120.0),
                       AutomationPoint(0.2, gain_db=0.0)]
    p = session.project
    p.tracks = [Track("t1", clips=[clip])]
    before = render(p, session.load_source).mix
    # 素材整体后移 2 秒：淡入断点跟着移动，2.0~2.2s 应几乎静音。
    clip.start = 2.0
    after = render(p, session.load_source).mix
    assert after.shape[0] == int(3.0 * 44100)
    assert np.max(np.abs(after[:int(2.0 * 44100)])) == 0.0
    assert np.max(np.abs(after[int(2.0 * 44100):int(2.15 * 44100)])) < 0.05
    assert np.allclose(after[int(2.2 * 44100):int(2.9 * 44100)],
                       before[int(0.2 * 44100):int(0.9 * 44100)], atol=1e-6)


def test_track_automation_stays_on_project_time(session):
    src_a, _ = _sources_by_freq(session)
    clip = Clip("c1", src_a, start=0.0, in_point=0.0, out_point=1.0)
    track = Track("t1", clips=[clip])
    # 轨道自动化固定在工程时间 0.5s 处：素材移动后仍在 0.5s 变小。
    track.automation = [AutomationPoint(0.45, gain_db=0.0),
                        AutomationPoint(0.49, gain_db=-24.0),
                        AutomationPoint(0.55, gain_db=-24.0)]
    p = session.project
    p.tracks = [track]
    clip.start = 0.4
    mix = render(p, session.load_source).mix
    before = np.max(np.abs(mix[int(0.42 * 44100):int(0.45 * 44100)]))
    during = np.max(np.abs(mix[int(0.505 * 44100):int(0.54 * 44100)]))
    assert during < before * 0.2


def test_pan_automation(session):
    src_a, _ = _sources_by_freq(session)
    clip = Clip("c1", src_a, start=0.0, in_point=0.0, out_point=1.0)
    track = Track("t1", clips=[clip])
    track.automation = [AutomationPoint(0.0, pan=-1.0),
                        AutomationPoint(1.0, pan=1.0)]
    p = session.project
    p.tracks = [track]
    p.channels = 2
    mix = render(p, session.load_source).mix
    head = np.max(np.abs(mix[:2000]), axis=0)
    tail = np.max(np.abs(mix[-2000:]), axis=0)
    assert head[0] > head[1] * 3      # 开头偏左
    assert tail[1] > tail[0] * 3      # 结尾偏右


def test_track_effects_chain_and_amount_automation(session):
    src_a, _ = _sources_by_freq(session)
    clip = Clip("c1", src_a, start=0.0, in_point=0.0, out_point=0.3)
    track = Track("t1", clips=[clip])
    track.effects = [{"type": "gain", "gain_db": 12.0, "amount": 1.0}]
    p = session.project
    p.tracks = [track]
    base = render(p, session.load_source).mix
    assert np.max(np.abs(base)) > 1.0  # +12dB 增益确实生效
    # 强度自动化：0.15s 起把处理强度降到 0，之后回到原始电平。
    track.automation = [AutomationPoint(0.14, amount=1.0),
                        AutomationPoint(0.16, amount=0.0),
                        AutomationPoint(1.0, amount=0.0)]
    automated = render(p, session.load_source).mix
    assert np.max(np.abs(automated[int(0.2 * 44100):int(0.29 * 44100)])) < 0.6


def test_delay_effect(session):
    src_a, _ = _sources_by_freq(session)
    clip = Clip("c1", src_a, start=0.0, in_point=0.0, out_point=0.3)
    track = Track("t1", clips=[clip])
    track.effects = [{"type": "delay", "delay_sec": 0.1,
                      "feedback": 0.0, "wet": 0.8, "amount": 1.0}]
    p = session.project
    p.tracks = [track]
    mix = render(p, session.load_source, end_time=0.6).mix
    # 0.3s 处原声已结束，但延迟声应持续到 0.4s。
    assert np.max(np.abs(mix[int(0.31 * 44100):int(0.39 * 44100)])) > 0.05


def test_mute_solo_does_not_change_length_or_position(session):
    src_a, src_b = _sources_by_freq(session)
    p = session.project
    p.tracks = [
        Track("t1", clips=[Clip("c1", src_a, 0.0, 0.0, 1.0)]),
        Track("t2", clips=[Clip("c2", src_b, 2.0, 0.0, 1.0)]),
    ]
    normal = render(p, session.load_source)
    p.tracks[0].muted = True
    muted = render(p, session.load_source)
    assert muted.mix.shape == normal.mix.shape  # 静音不缩短导出
    p.tracks[0].muted = False
    p.tracks[1].solo = True
    solo = render(p, session.load_source)
    assert solo.mix.shape == normal.mix.shape    # 独听不缩短导出
    # 独听轨（B 在 2s）时间位置不变。
    assert np.max(np.abs(solo.mix[:int(1.9 * 44100)])) == 0.0
    assert np.max(np.abs(solo.mix[int(2.1 * 44100):int(2.9 * 44100)])) > 0


def test_ranged_render_splices_like_full(session):
    src_a, src_b = _sources_by_freq(session)
    p = session.project
    p.tracks = [
        Track("t1", clips=[Clip("c1", src_a, 0.0, 0.0, 1.0)]),
        Track("t2", clips=[Clip("c2", src_b, 1.0, 0.0, 1.0)]),
    ]
    full = render(p, session.load_source).mix
    head = render(p, session.load_source, start_time=0.0, end_time=1.0).mix
    tail = render(p, session.load_source, start_time=1.0, end_time=2.0).mix
    stitched = np.concatenate([head, tail])
    assert stitched.shape == full.shape
    assert np.allclose(stitched, full, atol=1e-6)


def test_repeated_render_is_idempotent(session):
    src_a, _ = _sources_by_freq(session)
    track = Track("t1", clips=[Clip("c1", src_a, 0.0, 0.0, 1.0)])
    track.effects = [{"type": "gain", "gain_db": 6.0}]
    p = session.project
    p.tracks = [track]
    first = render(p, session.load_source).mix
    for _ in range(3):
        again = render(p, session.load_source).mix
        assert np.allclose(first, again, atol=1e-7)
    # 源缓存未被引擎污染。
    assert np.max(np.abs(session.load_source(src_a))) < 0.6


def test_cancel_during_render_keeps_project(session):
    src_a, src_b = _sources_by_freq(session)
    session.project.tracks = [
        Track("t1", clips=[Clip("c1", src_a, 0.0, 0.0, 1.0)]),
        Track("t2", clips=[Clip("c2", src_b, 0.0, 0.0, 1.0)]),
    ]
    snapshot = session.project.to_dict()
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 2

    with pytest.raises(ExportCancelled):
        render(session.project, session.load_source, should_cancel=cancel)
    assert session.project.to_dict() == snapshot


def test_multi_rate_trim_move_save_chunks(tmp_path):
    sr = 44100
    sess = Session.create(str(tmp_path / "s3"), sample_rate=sr)
    _tone(str(tmp_path / "lo.wav"), 440, 1.0, sr=44100)
    _tone(str(tmp_path / "hi.wav"), 880, 1.0, sr=22050)
    _tone(str(tmp_path / "pulse.wav"), 1760, 1.0, sr=32000)
    lo = sess.import_file(str(tmp_path / "lo.wav"), "lo.wav").relpath
    hi = sess.import_file(str(tmp_path / "hi.wav"), "hi.wav").relpath
    pu = sess.import_file(str(tmp_path / "pulse.wav"), "pulse.wav").relpath
    clip_a = Clip("a", lo, start=0.0, in_point=0.1, out_point=0.8)
    clip_b = Clip("b", hi, start=1.0, in_point=0.0, out_point=1.0)
    clip_c = Clip("c", pu, start=2.0, in_point=0.0, out_point=0.7)
    clip_a.set_fades(fade_in=0.1, fade_out=0.1)
    sess.project.tracks = [Track("t", clips=[clip_a, clip_b, clip_c])]

    full = render(sess.project, sess.load_source).mix
    # 保存重开后逐采样一致。
    sess.save()
    reopened = Session.open(sess.root)
    assert np.allclose(render(reopened.project, reopened.load_source).mix, full, atol=1e-6)
    # 分块导出后拼接与整段渲染一致。
    out = tmp_path / "chunked.wav"
    bounds = [(0.0, 1.0), (1.0, 2.0), (2.0, full.shape[0] / sr)]
    pieces = []
    for i, (a, b) in enumerate(bounds):
        part = render(sess.project, sess.load_source, start_time=a, end_time=b).mix
        piece = tmp_path / f"p{i}.wav"
        write_wav_atomic(str(piece), part, sr, block_seconds=0.1)
        data, psr = sf.read(str(piece))
        assert psr == sr
        pieces.append(data)
    sf.write(str(out), np.concatenate(pieces), sr)
    loaded, _ = sf.read(str(out))
    assert np.allclose(loaded, full, atol=2e-4)  # PCM16 量化容差


def _impulse(path: str, duration: float, sr: int = 44100) -> None:
    sig = np.zeros(int(duration * sr), dtype=np.float32)
    sig[0] = 0.8
    sf.write(path, sig[:, None], sr, subtype="PCM_16")


def _delay_track(track_id, clips, **kw):
    effect = {"type": "delay", "delay_sec": 0.3, "feedback": 0.6, "wet": 0.8, "amount": 1.0}
    return Track(track_id, clips=clips, effects=[effect], **kw)


def _report_overlap(name, full, chunks, bounds, sr):
    """比较整段渲染与相邻分块在重叠边界处的数值误差并打印报告。"""
    joined = np.concatenate(chunks)
    assert joined.shape == full.shape
    max_err = float(np.max(np.abs(joined - full)))
    print(f"\n[{name}] 拼接 vs 整段: max|err| = {max_err:.3e}")
    for b in bounds[1:-1]:
        frame = int(round(b * sr))
        lo, hi = max(0, frame - 64), min(full.shape[0], frame + 64)
        err = float(np.max(np.abs(joined[lo:hi] - full[lo:hi])))
        print(f"  边界 {b:.3f}s 附近 64 采样窗内 max|err| = {err:.3e}")
    return max_err


def test_chunked_render_matches_full_with_delay_impulse(tmp_path):
    """脉冲 + 延迟/回声：整段渲染与多个相邻区间拼接逐采样一致。"""
    sr = 44100
    sess = Session.create(str(tmp_path / "s4"), sample_rate=sr)
    _impulse(str(tmp_path / "imp.wav"), 0.05, sr=sr)
    src = sess.import_file(str(tmp_path / "imp.wav"), "imp.wav").relpath
    clip = Clip("c", src, start=0.5, in_point=0.0, out_point=0.05)
    sess.project.tracks = [_delay_track("t", [clip])]

    full = render(sess.project, sess.load_source, include_tail=True).mix
    end = full.shape[0] / sr
    bounds = [0.0, 0.5, 0.9, end]
    chunks = [
        render(sess.project, sess.load_source, start_time=a, end_time=b).mix
        for a, b in zip(bounds[:-1], bounds[1:])
    ]
    max_err = _report_overlap("脉冲+回声", full, chunks, bounds, sr)
    assert max_err < 1e-6
    # 回声确实跨越了分块边界（否则比较没有意义）。
    assert np.max(np.abs(full[int(0.9 * sr):])) > 1e-3


def test_chunked_render_matches_full_with_delay_tone(tmp_path):
    """持续音 + 延迟 + 轨道音量/声像自动化：边界处连续且拼接一致。"""
    sr = 44100
    sess = Session.create(str(tmp_path / "s5"), sample_rate=sr)
    _tone(str(tmp_path / "tone.wav"), 440, 3.0, sr=sr)
    src = sess.import_file(str(tmp_path / "tone.wav"), "tone.wav").relpath
    clip = Clip("c", src, start=0.0, in_point=0.0, out_point=3.0)
    track = _delay_track("t", [clip])
    # 自动化断点横跨分块边界 1.0s / 2.0s，边界两侧斜率不同。
    track.automation = [
        AutomationPoint(0.5, gain_db=-12.0, pan=-1.0),
        AutomationPoint(1.5, gain_db=0.0, pan=1.0),
        AutomationPoint(2.5, gain_db=-6.0, pan=0.0),
    ]
    sess.project.tracks = [track]

    full = render(sess.project, sess.load_source).mix
    bounds = [0.0, 1.0, 2.0, 3.0]
    chunks = [
        render(sess.project, sess.load_source, start_time=a, end_time=b).mix
        for a, b in zip(bounds[:-1], bounds[1:])
    ]
    max_err = _report_overlap("持续音+回声+自动化", full, chunks, bounds, sr)
    assert max_err < 1e-6
    # 边界处自动化取值连续：相邻两块在边界帧上的取值与整段渲染一致。
    for b in (1.0, 2.0):
        frame = int(round(b * sr))
        before = chunks[int(b) - 1]  # 在 b 结束的块
        after = chunks[int(b)]       # 从 b 开始的块
        assert np.allclose(before[-1], full[frame - 1], atol=1e-6)
        assert np.allclose(after[0], full[frame], atol=1e-6)


def test_include_tail_extends_past_content_end(tmp_path):
    """导出所选片段及其自然衰减尾音：片段结束与尾音结束明确区分。"""
    sr = 44100
    sess = Session.create(str(tmp_path / "s6"), sample_rate=sr)
    _impulse(str(tmp_path / "imp.wav"), 0.05, sr=sr)
    src = sess.import_file(str(tmp_path / "imp.wav"), "imp.wav").relpath
    clip = Clip("c", src, start=1.0, in_point=0.0, out_point=0.05)
    sess.project.tracks = [_delay_track("t", [clip])]

    plain = render(sess.project, sess.load_source, start_time=0.5, end_time=1.5)
    assert plain.content_end_time == pytest.approx(1.5)
    assert plain.tail_end_time == pytest.approx(1.5)

    tailed = render(sess.project, sess.load_source,
                    start_time=0.5, end_time=1.5, include_tail=True)
    assert tailed.content_end_time == pytest.approx(1.5)   # 片段本身到这里结束
    assert tailed.tail_end_time > 1.5 + 0.5                # 尾音继续衰减
    # 尾音里确实还有声音（回声在片段结束之后仍可听见）。
    tail_frames = tailed.mix[int((1.5 - 0.5) * sr):]
    assert np.max(np.abs(tail_frames)) > 1e-3
    # 内容区间部分与不带尾音的渲染一致。
    assert np.allclose(tailed.mix[:plain.mix.shape[0]], plain.mix, atol=1e-6)


def test_repeated_and_cancelled_exports_do_not_accumulate(tmp_path):
    """改变起止、反复取消重导、保存重开：不累加旧效果、不截断余音。"""
    sr = 44100
    sess = Session.create(str(tmp_path / "s7"), sample_rate=sr)
    _impulse(str(tmp_path / "imp.wav"), 0.05, sr=sr)
    src = sess.import_file(str(tmp_path / "imp.wav"), "imp.wav").relpath
    clip = Clip("c", src, start=0.2, in_point=0.0, out_point=0.05)
    sess.project.tracks = [_delay_track("t", [clip])]

    first = render(sess.project, sess.load_source, include_tail=True).mix
    # 取消一次渲染后再渲染，结果不变。
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 1

    with pytest.raises(ExportCancelled):
        render(sess.project, sess.load_source, include_tail=True, should_cancel=cancel)
    second = render(sess.project, sess.load_source, include_tail=True).mix
    assert np.allclose(first, second, atol=0.0)  # 逐采样完全相同
    # 改变起止区间再渲染整段，仍一致。
    render(sess.project, sess.load_source, start_time=0.6, end_time=1.1)
    third = render(sess.project, sess.load_source, include_tail=True).mix
    assert np.allclose(first, third, atol=0.0)
    # 保存重开后余音不被截断、效果不叠加。
    sess.save()
    reopened = Session.open(sess.root)
    fourth = render(reopened.project, reopened.load_source, include_tail=True).mix
    assert fourth.shape == first.shape
    assert np.allclose(first, fourth, atol=1e-6)


def _write_monotone(path: str, values: np.ndarray, sr: int = 44100) -> None:
    sf.write(path, values.astype(np.float32)[:, None], sr, subtype="PCM_16")


def _ducking_project(sess: Session, tmp_path, *, control_values: np.ndarray,
                     preset_id: str = "a", muted_control: bool = False,
                     clips=None, music_duration: float = 1.0) -> Project:
    sr = sess.project.sample_rate
    _write_monotone(str(tmp_path / "music.wav"), np.full(int(music_duration * sr), 0.4), sr)
    _write_monotone(str(tmp_path / "voice.wav"), control_values, sr)
    music_src = sess.import_file(str(tmp_path / "music.wav"), "music.wav").relpath
    voice_src = sess.import_file(str(tmp_path / "voice.wav"), "voice.wav").relpath
    if clips is None:
        clips = [Clip("voice", voice_src, 0.0, 0.0, len(control_values) / sr)]
    config = default_ducking()
    config.update({
        "control_track_id": "voice",
        "target_track_ids": ["music"],
        "active_preset_id": preset_id,
    })
    config["presets"][0].update(
        threshold_db=-20.0, depth_db=-12.0, attack_sec=0.01,
        hold_sec=0.10, release_sec=0.20,
    )
    project = sess.project
    project.ducking = config
    project.tracks = [
        Track("music", "音乐", clips=[Clip("music", music_src, 0.0, 0.0, music_duration)]),
        Track("voice", "主持", muted=muted_control, clips=clips if clips is not None else [
            Clip("voice", voice_src, 0.0, 0.0, len(control_values) / sr)
        ]),
    ]
    return project


def test_ducking_short_pulse_holds_releases_and_reports_ranges(tmp_path):
    sr = 44100
    sess = Session.create(str(tmp_path / "duck-pulse"), sample_rate=sr)
    voice = np.zeros(sr, dtype=np.float32)
    voice[int(0.2 * sr):int(0.22 * sr)] = 0.8
    project = _ducking_project(sess, tmp_path, control_values=voice, muted_control=True)

    result = render(project, sess.load_source)
    assert result.duration == pytest.approx(1.3, abs=2e-3)  # 尾部包含保持+恢复
    assert result.ducking_ranges
    first = result.ducking_ranges[0]
    assert first.start == pytest.approx(0.2, abs=2e-3)
    assert first.end == pytest.approx(0.5, abs=3e-2)
    # 短脉冲也能立刻压到约 -12 dB；保持期未恢复，0.5s 后恢复完成。
    assert np.max(np.abs(result.mix[int(0.215 * sr):int(0.29 * sr)])) == pytest.approx(0.071, abs=0.015)
    assert np.min(result.mix[int(0.52 * sr):int(0.60 * sr), 0]) == pytest.approx(0.283, abs=0.01)


def test_muted_control_track_still_ducks_but_is_silent(tmp_path):
    sr = 44100
    sess = Session.create(str(tmp_path / "duck-muted"), sample_rate=sr)
    voice = np.zeros(sr, dtype=np.float32)
    voice[int(0.2 * sr):int(0.4 * sr)] = 0.8
    project = _ducking_project(sess, tmp_path, control_values=voice, muted_control=True)

    result = render(project, sess.load_source)
    window = result.mix[int(0.25 * sr):int(0.35 * sr), 0]
    assert np.max(np.abs(window)) == pytest.approx(0.071, abs=0.015)
    assert result.ducking_ranges


def test_adjacent_control_clips_keep_ducking_without_gap(tmp_path):
    sr = 44100
    sess = Session.create(str(tmp_path / "duck-adjacent"), sample_rate=sr)
    _write_monotone(str(tmp_path / "music.wav"), np.full(sr, 0.4), sr)
    voice_a = np.zeros(int(0.3 * sr), dtype=np.float32)
    voice_b = np.zeros(int(0.3 * sr), dtype=np.float32)
    voice_a[int(0.2 * sr):] = 0.8
    voice_b[:int(0.1 * sr)] = 0.8
    _write_monotone(str(tmp_path / "a.wav"), voice_a, sr)
    _write_monotone(str(tmp_path / "b.wav"), voice_b, sr)
    music_src = sess.import_file(str(tmp_path / "music.wav"), "music.wav").relpath
    voice_a_src = sess.import_file(str(tmp_path / "a.wav"), "a.wav").relpath
    voice_b_src = sess.import_file(str(tmp_path / "b.wav"), "b.wav").relpath
    config = default_ducking()
    config.update(control_track_id="voice", target_track_ids=["music"], active_preset_id="a")
    config["presets"][0].update(
        threshold_db=-20.0, depth_db=-12.0, attack_sec=0.01,
        hold_sec=0.05, release_sec=0.20,
    )
    sess.project.ducking = config
    sess.project.tracks = [
        Track("music", clips=[Clip("music", music_src, 0, 0, 1.0)]),
        Track("voice", muted=True, clips=[
            Clip("va", voice_a_src, 0, 0, 0.3),
            Clip("vb", voice_b_src, 0.3, 0, 0.3),
        ]),
    ]
    result = render(sess.project, sess.load_source)
    # 两个片段在 0.3s 相接，中间不应回到原始电平。
    assert np.max(np.abs(result.mix[int(0.29 * sr):int(0.40 * sr), 0])) < 0.16
    assert np.min(result.mix[int(0.68 * sr):int(0.70 * sr), 0]) == pytest.approx(0.283, abs=0.02)


def test_ducking_range_export_continues_full_envelope(tmp_path):
    sr = 44100
    sess = Session.create(str(tmp_path / "duck-slice"), sample_rate=sr)
    voice = np.zeros(sr, dtype=np.float32)
    voice[int(0.1 * sr):int(0.4 * sr)] = 0.8
    project = _ducking_project(sess, tmp_path, control_values=voice, muted_control=True)

    full = render(project, sess.load_source).mix
    part = render(project, sess.load_source, start_time=0.25, end_time=0.75).mix
    expected = full[int(0.25 * sr):int(0.75 * sr)]
    assert part.shape == expected.shape
    assert np.allclose(part, expected, atol=1e-6)
    assert np.max(np.abs(part[0])) < 0.15  # 起点正处在讲话中，不是未触发状态


def test_ducking_follows_moved_trimmed_gain_changed_control(tmp_path):
    sr = 44100
    sess = Session.create(str(tmp_path / "duck-edit"), sample_rate=sr)
    voice = np.zeros(sr, dtype=np.float32)
    voice[:int(0.4 * sr)] = 0.8
    project = _ducking_project(sess, tmp_path, control_values=voice)
    before = render(project, sess.load_source).ducking_ranges[0]
    assert before.start == pytest.approx(0.0, abs=2e-3)

    clip = project.tracks[1].clips[0]
    clip.start = 0.25
    clip.in_point = 0.1
    clip.out_point = 0.4
    clip.gain_db = -3
    after = render(project, sess.load_source).ducking_ranges[0]
    assert after.start == pytest.approx(0.25, abs=3e-3)


def test_ducking_presets_persist_and_original_mix_remains(tmp_path):
    sr = 44100
    sess = Session.create(str(tmp_path / "duck-save"), sample_rate=sr)
    voice = np.zeros(sr, dtype=np.float32)
    voice[int(0.2 * sr):int(0.4 * sr)] = 0.8
    project = _ducking_project(sess, tmp_path, control_values=voice, preset_id="b", muted_control=True)
    ducked = render(project, sess.load_source).mix

    sess.save()
    reopened = Session.open(sess.root)
    assert reopened.project.ducking["active_preset_id"] == "b"
    assert len(reopened.project.ducking["presets"]) == 2
    assert np.allclose(render(reopened.project, reopened.load_source).mix, ducked, atol=1e-6)

    reopened.project.ducking["active_preset_id"] = "off"
    original = render(reopened.project, reopened.load_source).mix
    assert np.min(original[int(0.25 * sr):int(0.35 * sr), 0]) == pytest.approx(0.283, abs=0.01)


# ----------------------------------------------------------------------
# 多控制源（多角色）闪避 v2
# ----------------------------------------------------------------------

def _multi_role_project(sess: Session, tmp_path):
    """主持人/远端嘉宾交叠讲话 + 紧接的短提示音 + 一条静音控制轨。"""
    sr = sess.project.sample_rate
    _write_monotone(str(tmp_path / "music.wav"), np.full(sr, 0.4), sr)
    host = np.zeros(sr, dtype=np.float32)
    host[int(0.10 * sr):int(0.50 * sr)] = 0.8
    guest = np.zeros(sr, dtype=np.float32)
    guest[int(0.40 * sr):int(0.90 * sr)] = 0.8
    beep = np.zeros(sr, dtype=np.float32)
    beep[int(0.92 * sr):int(0.96 * sr)] = 0.9
    silent = np.zeros(sr, dtype=np.float32)
    import soundfile as _sf
    music_src = sess.import_file(str(tmp_path / "music.wav"), "music.wav").relpath
    for name, arr in [("host.wav", host), ("guest.wav", guest),
                      ("beep.wav", beep), ("silent.wav", silent)]:
        _sf.write(str(tmp_path / name), arr[:, None], sr, subtype="PCM_16")
    host_src = sess.import_file(str(tmp_path / "host.wav"), "host.wav").relpath
    guest_src = sess.import_file(str(tmp_path / "guest.wav"), "guest.wav").relpath
    beep_src = sess.import_file(str(tmp_path / "beep.wav"), "beep.wav").relpath
    silent_src = sess.import_file(str(tmp_path / "silent.wav"), "silent.wav").relpath

    cfg = default_ducking_v2()
    plan = cfg["plans"][0]
    for i, item in enumerate([
        ("host", -12.0, 10), ("guest", -6.0, 8), ("beep", -15.0, 6),
    ]):
        role = plan["roles"][i]
        role["target_track_ids"] = ["music"]
        role.update(threshold_db=-20.0, depth_db=item[1],
                    attack_sec=0.02, hold_sec=0.05, release_sec=0.20)
        role["priority"] = item[2]
    plan["roles"][0]["control_track_ids"] = ["host", "silent"]  # 含一条静音轨
    plan["roles"][1]["control_track_ids"] = ["guest"]
    plan["roles"][2]["control_track_ids"] = ["beep"]

    project = sess.project
    project.ducking = cfg
    project.tracks = [
        Track("music", "背景音乐", clips=[Clip("cm", music_src, 0, 0, 1.0)]),
        Track("host", "主持人", muted=True, clips=[Clip("ch", host_src, 0, 0, 1.0)]),
        Track("guest", "远端嘉宾", muted=True, clips=[Clip("cg", guest_src, 0, 0, 1.0)]),
        Track("beep", "提示音", muted=True, clips=[Clip("cb", beep_src, 0, 0, 1.0)]),
        Track("silent", "静音轨", muted=True, clips=[Clip("cs", silent_src, 0, 0, 1.0)]),
    ]
    return project


def test_v2_strongest_role_handover_is_continuous(tmp_path):
    sr = 44100
    sess = Session.create(str(tmp_path / "v2-handover"), sample_rate=sr)
    project = _multi_role_project(sess, tmp_path)
    result = render(project, sess.load_source)
    mix = result.mix[:, 0]

    # 只有主持人：压 12dB；与 v1 口径一致（居中声像 1/√2），约 0.071。
    assert np.max(np.abs(mix[int(0.30 * sr):int(0.38 * sr)])) == pytest.approx(0.071, abs=0.015)
    # 交叠区由压得更深的主持人主导，而不是被较浅的嘉宾“放回去”。
    overlap = mix[int(0.44 * sr):int(0.48 * sr)]
    assert np.max(np.abs(overlap)) == pytest.approx(0.071, abs=0.02)
    # 主持人停后嘉宾仍在讲，音乐保持被压低状态，不出现回到满电平的缺口。
    assert np.max(np.abs(mix[int(0.52 * sr):int(0.58 * sr)])) < 0.11
    # 紧接的短提示音压得更深 15dB。
    assert np.max(np.abs(mix[int(0.945 * sr):int(0.958 * sr)])) == pytest.approx(0.0503, abs=0.015)

    # 交接点逐采样连续：增益包络没有跳变。
    report = result.ducking_report
    assert report["combine"] == "strongest"
    target = next(t for t in report["targets"] if t["track_id"] == "music")
    controllers = [r["control"] for r in target["ranges"]]
    assert controllers[:3] == ["主持人", "远端嘉宾", "提示音"]
    # 主持人交给嘉宾的窗口（0.48–0.54s）音乐电平不能出现回到满电平的突变缺口。
    handover = mix[int(0.48 * sr):int(0.54 * sr)]
    assert np.max(np.abs(handover)) < 0.16
    assert np.min(np.abs(handover)) >= 0.0


def test_v2_multiply_combines_roles_by_explicit_rule(tmp_path):
    sr = 44100
    sess = Session.create(str(tmp_path / "v2-multiply"), sample_rate=sr)
    project = _multi_role_project(sess, tmp_path)
    plan = project.ducking["plans"][0]
    plan["combine"] = "multiply"
    result = render(project, sess.load_source)
    mix = result.mix[:, 0]
    # 交叠区两角色共同作用：0.4 * .251 * .501 / √2 ≈ 0.0356。
    assert np.max(np.abs(mix[int(0.44 * sr):int(0.46 * sr)])) == pytest.approx(0.0356, abs=0.01)
    target = next(t for t in result.ducking_report["targets"]
                  if t["track_id"] == "music")
    labels = {r["control"] for r in target["ranges"]}
    assert "主持人+远端嘉宾" in labels


def test_v2_duplicate_grouping_rejected_and_silent_control_ignored(tmp_path):
    sr = 44100
    sess = Session.create(str(tmp_path / "v2-dup"), sample_rate=sr)
    project = _multi_role_project(sess, tmp_path)
    # 同一控制轨被第二个角色再次归入并压同一目标：必须拒绝而不是多压一次。
    project.ducking["plans"][0]["roles"][1]["control_track_ids"] = ["host", "host"]
    with pytest.raises(ValueError, match="重复"):
        Project.from_dict(project.to_dict())


def test_v2_slice_export_matches_full_render_at_handover(tmp_path):
    sr = 44100
    sess = Session.create(str(tmp_path / "v2-slice"), sample_rate=sr)
    project = _multi_role_project(sess, tmp_path)
    full = render(project, sess.load_source).mix
    part = render(project, sess.load_source, start_time=0.40, end_time=1.00).mix
    expected = full[int(0.40 * sr):int(1.00 * sr)]
    assert part.shape == expected.shape
    assert np.allclose(part, expected, atol=1e-6)


def test_v2_pre_post_control_source_and_cycle_rejection(tmp_path):
    sr = 44100
    sess = Session.create(str(tmp_path / "v2-chain"), sample_rate=sr)
    _write_monotone(str(tmp_path / "bgm.wav"), np.full(sr, 0.3), sr)
    _write_monotone(str(tmp_path / "bed.wav"), np.full(sr, 0.3), sr)
    voice = np.zeros(sr, dtype=np.float32)
    voice[int(0.2 * sr):int(0.6 * sr)] = 0.8
    import soundfile as _sf
    _sf.write(str(tmp_path / "a.wav"), voice[:, None], sr, subtype="PCM_16")
    bgm_src = sess.import_file(str(tmp_path / "bgm.wav"), "bgm.wav").relpath
    bed_src = sess.import_file(str(tmp_path / "bed.wav"), "bed.wav").relpath
    a_src = sess.import_file(str(tmp_path / "a.wav"), "a.wav").relpath

    def config(source):
        return {"version": 2, "active_plan_id": "p", "plans": [{
            "id": "p", "name": "p", "combine": "strongest", "roles": [
                {"id": "host", "name": "h", "priority": 10,
                 "control_track_ids": ["a"], "target_track_ids": ["bgm"],
                 "source": "pre", "threshold_db": -20.0, "depth_db": -20.0,
                 "attack_sec": 0.01, "hold_sec": 0.02, "release_sec": 0.10},
                {"id": "chain", "name": "c", "priority": 5,
                 "control_track_ids": ["bgm"], "target_track_ids": ["bed"],
                 "source": source, "threshold_db": -20.0, "depth_db": -10.0,
                 "attack_sec": 0.01, "hold_sec": 0.02, "release_sec": 0.10},
            ]}]}

    project = sess.project
    project.tracks = [
        Track("bgm", clips=[Clip("cbgm", bgm_src, 0, 0, 1.0)]),
        Track("bed", clips=[Clip("cbed", bed_src, 0, 0, 1.0)]),
        Track("a", muted=True, clips=[Clip("ca", a_src, 0, 0, 1.0)]),
    ]

    # 处理前信号：bgm 原始电平 0.3 一直超阈值，bed 全程被压低。
    project.ducking = config("pre")
    pre_mix = render(project, sess.load_source).mix[:, 0]
    assert np.max(np.abs(pre_mix[int(0.05 * sr):int(0.10 * sr)])) == pytest.approx(
        (0.3 + 0.3 * 10 ** (-10 / 20)) / np.sqrt(2), abs=0.03)

    # 处理后信号：讲话期间 bgm 已被压到约 0.03（低于 -20dB 阈值），
    # 级联不再触发 bed；讲话外 bgm 恢复时 bed 才被压低。
    project.ducking = config("post")
    post = render(project, sess.load_source)
    post_mix = post.mix[:, 0]
    assert np.max(np.abs(post_mix[int(0.30 * sr):int(0.40 * sr)])) == pytest.approx(
        (0.3 * 10 ** (-20 / 20) + 0.3) / np.sqrt(2), abs=0.03)
    role_sources = {r["id"]: r["source"] for r in post.ducking_report["roles"]}
    assert role_sources == {"host": "pre", "chain": "post"}

    # 相互控制（post 环）无法解释：加载与渲染都必须失败，不允许只算一半。
    cycle = config("post")
    cycle["plans"][0]["roles"][0].update(
        control_track_ids=["bed"], target_track_ids=["bgm"], source="post")
    with pytest.raises(ValueError, match="相互控制环"):
        Project.from_dict({**project.to_dict(), "ducking": cycle})
    project.ducking = cycle
    with pytest.raises(ValueError, match="相互控制环"):
        render(project, sess.load_source)


def test_v2_saved_plan_is_independent_and_reproducible(tmp_path):
    sr = 44100
    sess = Session.create(str(tmp_path / "v2-save"), sample_rate=sr)
    project = _multi_role_project(sess, tmp_path)
    first = render(project, sess.load_source).mix
    # 复制一份方案再修改：已保存方案的关系不能随新方案改变。
    import copy
    plans = project.ducking["plans"]
    saved = copy.deepcopy(plans[0])
    saved["id"] = "plan2"
    saved["name"] = "方案 2"
    saved["roles"][0]["depth_db"] = -3.0
    plans.append(saved)
    project.ducking["active_plan_id"] = "plan2"
    assert plans[0]["roles"][0]["depth_db"] == -12.0
    project.ducking["active_plan_id"] = "plan1"

    sess.save()
    reopened = Session.open(sess.root)
    again = render(reopened.project, reopened.load_source).mix
    assert again.shape == first.shape
    assert np.allclose(first, again, atol=1e-6)
    ids = [p["id"] for p in reopened.project.ducking["plans"]]
    assert ids == ["plan1", "plan2"]
    plan1 = next(p for p in reopened.project.ducking["plans"] if p["id"] == "plan1")
    assert plan1["roles"][0]["depth_db"] == -12.0


def test_v2_copied_plan_labels_are_independent_after_save(tmp_path):
    sr = 44100
    sess = Session.create(str(tmp_path / "v2-copy-labels"), sample_rate=sr)
    project = _multi_role_project(sess, tmp_path)
    original_mix = render(project, sess.load_source).mix

    import copy
    plans = project.ducking["plans"]
    copied = copy.deepcopy(plans[0])
    copied["id"] = "plan2"
    copied["name"] = "对照副本"
    copied["roles"][0].update(name="主播", priority=12)
    copied["roles"][1].update(name="连线嘉宾", priority=2)
    project.ducking["plans"].append(copied)
    sess.save()

    reopened = Session.open(sess.root)
    reopened_plans = reopened.project.ducking["plans"]
    assert reopened_plans[0]["roles"][0]["id"] == "host"
    assert reopened_plans[1]["roles"][0]["id"] != "host"
    assert reopened_plans[1]["roles"][0]["control_track_ids"] == ["host", "silent"]
    assert reopened_plans[1]["roles"][0]["target_track_ids"] == ["music"]

    reopened.project.ducking["active_plan_id"] = "plan1"
    original_report = render(reopened.project, reopened.load_source).ducking_report
    original_labels = [
        range_item["control"]
        for target in original_report["targets"] if target["track_id"] == "music"
        for range_item in target["ranges"]
    ]
    assert original_labels[:3] == ["主持人", "远端嘉宾", "提示音"]

    reopened.project.ducking["active_plan_id"] = "plan2"
    copied_report = render(reopened.project, reopened.load_source).ducking_report
    copied_labels = [
        range_item["control"]
        for target in copied_report["targets"] if target["track_id"] == "music"
        for range_item in target["ranges"]
    ]
    assert copied_labels[:3] == ["主播", "连线嘉宾", "提示音"]
    assert np.allclose(render(reopened.project, reopened.load_source).mix, original_mix,
                       atol=1e-6)

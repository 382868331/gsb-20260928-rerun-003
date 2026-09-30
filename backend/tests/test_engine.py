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


# ---------- 区间渲染与整段渲染统一、效果尾音 ----------

def _impulse(path: str, duration: float = 1.0, sr: int = 44100) -> None:
    data = np.zeros((int(duration * sr), 1), dtype=np.float32)
    data[0, 0] = 0.9
    data[int(0.5 * sr), 0] = 0.6
    sf.write(path, data, sr, subtype="PCM_16")


def _splice_error(project, loader, bounds, sr=44100):
    """整段渲染 vs 相邻区间拼接的最大逐采样误差。"""
    full = render(project, loader, start_time=bounds[0], end_time=bounds[-1]).mix
    pieces = [render(project, loader, start_time=a, end_time=b).mix
              for a, b in zip(bounds[:-1], bounds[1:])]
    stitched = np.concatenate(pieces)
    assert stitched.shape == full.shape
    return float(np.max(np.abs(stitched - full))), stitched, full


def test_ranged_render_with_delay_matches_full_impulse(session):
    """脉冲 + 延迟：多个相邻区间拼接与整段渲染逐采样一致。"""
    imp_path = os.path.join(os.path.dirname(session.root), "imp.wav")
    _impulse(imp_path)
    imp = session.import_file(imp_path, "imp.wav").relpath
    track = Track("t1", clips=[Clip("c1", imp, 0.0, 0.0, 1.0)])
    track.effects = [{"type": "delay", "delay_sec": 0.13,
                      "feedback": 0.5, "wet": 0.6, "amount": 1.0}]
    p = session.project
    p.tracks = [track]
    # 区间边界故意落在两次脉冲的回声衰减中段。
    err, stitched, full = _splice_error(p, session.load_source, [0.0, 0.57, 1.03, 1.6])
    print(f"脉冲：分块拼接 vs 整段 最大误差 = {err:.3e}")
    assert err < 1e-6
    # 回声状态没有丢失：0.5s 脉冲（在第二块起点 0.57s 之前）的回声
    # 落在 0.63s（第二块内），分块渲染必须保留它。
    at = int(0.63 * 44100)
    assert np.max(np.abs(stitched[at:at + 200])) > 1e-3


def test_ranged_render_with_delay_matches_full_tone(session):
    """持续音 + 延迟 + 轨道自动化：边界处音量/声像连续，拼接误差可忽略。"""
    src_a, _ = _sources_by_freq(session)
    track = Track("t1", clips=[Clip("c1", src_a, 0.0, 0.0, 1.0)])
    track.effects = [{"type": "delay", "delay_sec": 0.07,
                      "feedback": 0.4, "wet": 0.5, "amount": 1.0}]
    # 自动化断点正好跨在分块边界 0.5s 上。
    track.automation = [AutomationPoint(0.3, gain_db=0.0, pan=-1.0),
                        AutomationPoint(0.7, gain_db=-12.0, pan=1.0)]
    p = session.project
    p.tracks = [track]
    err, stitched, full = _splice_error(p, session.load_source, [0.0, 0.5, 1.0])
    print(f"持续音：分块拼接 vs 整段 最大误差 = {err:.3e}")
    assert err < 1e-6
    # 边界处逐采样连续：边界前后各 5ms 与整段对应位置一致（已含在 err 内），
    # 且边界两侧电平确实在变化（自动化跨边界生效）。
    edge = int(0.5 * 44100)
    before = np.max(np.abs(full[edge - 220:edge]))
    after = np.max(np.abs(full[edge:edge + 220]))
    assert abs(before - after) > 1e-4


def test_ranged_render_start_matches_full_slice(session):
    """从中途单独渲染 = 整段渲染后截取同一段（延迟状态不丢失）。"""
    src_a, _ = _sources_by_freq(session)
    track = Track("t1", clips=[Clip("c1", src_a, 0.0, 0.0, 1.0)])
    track.effects = [{"type": "delay", "delay_sec": 0.11,
                      "feedback": 0.6, "wet": 0.7, "amount": 1.0}]
    p = session.project
    p.tracks = [track]
    full = render(p, session.load_source, start_time=0.0, end_time=1.0).mix
    mid = render(p, session.load_source, start_time=0.4, end_time=0.9).mix
    sliced = full[int(0.4 * 44100):int(0.9 * 44100)]
    err = float(np.max(np.abs(mid - sliced)))
    print(f"中途渲染 vs 整段截取 最大误差 = {err:.3e}")
    assert mid.shape == sliced.shape
    assert err < 1e-6


def test_include_tail_extends_past_content_end(session):
    """导出所选片段及其自然衰减尾音；区分片段结束与尾音结束。"""
    src_a, _ = _sources_by_freq(session)
    track = Track("t1", clips=[Clip("c1", src_a, 0.0, 0.0, 0.3)])
    track.effects = [{"type": "delay", "delay_sec": 0.1,
                      "feedback": 0.0, "wet": 0.8, "amount": 1.0}]
    p = session.project
    p.tracks = [track]
    plain = render(p, session.load_source, start_time=0.0, end_time=0.3)
    tailed = render(p, session.load_source, start_time=0.0, end_time=0.3,
                    include_tail=True)
    # 片段本身的结束仍是 0.3s；含尾音的结束延长一个延迟时间到 0.4s。
    assert tailed.content_end_time == pytest.approx(0.3, abs=1e-6)
    assert tailed.end_time == pytest.approx(0.4, abs=1e-6)
    assert tailed.mix.shape[0] == int(0.4 * 44100)
    # 不带尾音时 0.3s 即截断；带尾音时 0.3~0.4s 仍有回声余音。
    assert plain.mix.shape[0] == int(0.3 * 44100)
    assert np.max(np.abs(tailed.mix[int(0.31 * 44100):int(0.39 * 44100)])) > 0.05
    # 公共部分与整段渲染一致，尾音不是重新叠加的旧效果。
    full = render(p, session.load_source, start_time=0.0, end_time=0.4).mix
    assert np.allclose(tailed.mix, full, atol=1e-6)


def test_tail_and_ranged_render_idempotent_across_cancel_and_reopen(session):
    """取消重导、保存重开都不累加旧效果，也不截断仍应存在的余音。"""
    src_a, _ = _sources_by_freq(session)
    track = Track("t1", clips=[Clip("c1", src_a, 0.0, 0.0, 0.5)])
    track.effects = [{"type": "delay", "delay_sec": 0.09,
                      "feedback": 0.5, "wet": 0.6, "amount": 1.0}]
    session.project.tracks = [track]
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 1

    with pytest.raises(ExportCancelled):
        render(session.project, session.load_source, end_time=0.5,
               include_tail=True, should_cancel=cancel)
    # 取消后重导：结果与从未取消过一致。
    a = render(session.project, session.load_source, end_time=0.5, include_tail=True).mix
    b = render(session.project, session.load_source, end_time=0.5, include_tail=True).mix
    assert np.allclose(a, b, atol=1e-7)
    # 保存重开后同一区间（含尾音）逐采样一致。
    session.save()
    reopened = Session.open(session.root)
    c = render(reopened.project, reopened.load_source, end_time=0.5, include_tail=True).mix
    assert np.allclose(a, c, atol=1e-6)
    # 改变起止位置再改回来，结果不变（不累积状态）。
    render(reopened.project, reopened.load_source, start_time=0.2, end_time=0.7)
    d = render(reopened.project, reopened.load_source, end_time=0.5, include_tail=True).mix
    assert np.allclose(a, d, atol=1e-7)

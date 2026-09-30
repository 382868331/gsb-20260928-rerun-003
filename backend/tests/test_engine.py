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

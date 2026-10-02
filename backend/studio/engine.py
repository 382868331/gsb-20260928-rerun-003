"""混音引擎：轨道 -> 输出的处理顺序在这里固定下来。

对每条被听见的轨道：
    源音频 -> 按素材 in/out 裁切 -> 素材增益与素材内自动化（随素材移动）
    -> 声像 -> 叠加到轨道缓冲
轨道缓冲：
    轨道效果器链（增益/延迟等，强度可被工程绝对时间自动化）
    -> 轨道推子增益/声像/轨道自动化（留在工程时间上）
    -> 叠加到母线
母线：各轨叠加 -> 主控增益 -> 输出范围检测 -> 导出

支持只渲染 [start, end) 区间，用于分块导出；区间渲染是纯计算，
不会修改工程或素材缓存，因此重复导出不会叠加上一次的效果。

区间渲染与整段渲染的含义统一：延迟/回声类效果器在区间起点之前
的状态通过"预卷"（从起点向前多渲染一段效果尾长再裁掉）保留，
因此把整段切成若干相邻区间分别渲染再拼接，与整段渲染逐采样一致；
音量/声像自动化按工程绝对时间逐采样插值，区间边界处取值连续。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .model import AutomationPoint, Clip, Project, Track, _db_to_gain


SourceLoader = Callable[[str], np.ndarray]
CancelFn = Callable[[], bool]


@dataclass
class OverRange:
    start: float
    end: float
    peak: float


@dataclass
class RenderResult:
    mix: np.ndarray                 # float32, [帧, 声道]，应用主控增益后
    sample_rate: int
    channels: int
    peak_before_master: float       # 叠加后、主控推子前峰值
    peak: float                     # 最终输出峰值
    over_range: bool
    over_count: int                 # 超范围采样点数
    over_ranges: list[OverRange] = field(default_factory=list)
    duck_ranges: list[OverRange] = field(default_factory=list)
    duration: float = 0.0
    start_time: float = 0.0
    # 内容（片段/所选区间）本身的结束时间，与效果尾音的结束时间区分开。
    content_end_time: float = 0.0   # 请求区间的结束（片段本身到这里为止）
    tail_end_time: float = 0.0      # 实际渲染结束（含自然衰减尾音）


class RenderError(Exception):
    pass


def _effect_tail_seconds(effect: dict) -> float:
    """单个效果器把声音往后拖多长（秒）。增益类为 0。"""
    kind = effect.get("type")
    if kind in ("delay", "echo"):
        delay_sec = max(0.0, float(effect.get("delay_sec", 0.25)))
        feedback = min(0.9, max(0.0, float(effect.get("feedback", 0.3))))
        if delay_sec <= 0:
            return 0.0
        if feedback > 0:
            taps = int(np.ceil(np.log(max(1e-4, 1.0 - feedback + 1e-9)) /
                               np.log(max(feedback, 1e-9))))
            taps = min(max(taps, 1), 32)
        else:
            taps = 1
        return delay_sec * taps
    if kind == "gain":
        return 0.0
    raise RenderError(f"未知效果器类型: {kind!r}")


def effect_tail_seconds(project: Project) -> float:
    """整条效果器链的自然衰减尾长（各效果器尾长相加），取所有轨道的最大值。"""
    tail = 0.0
    for track in project.tracks:
        track_tail = sum(_effect_tail_seconds(e) for e in track.effects)
        tail = max(tail, track_tail)
    return tail


def _clip_segment(source: np.ndarray, clip: Clip, sr: int) -> np.ndarray:
    """按素材 in/out 取源区间（非破坏：只切视图副本）。"""
    first = int(round(clip.in_point * sr))
    last = int(round(clip.out_point * sr))
    first = max(0, min(first, source.shape[0]))
    last = max(first, min(last, source.shape[0]))
    return source[first:last]


def _envelope(
    points: Sequence[AutomationPoint],
    key: str,
    times: np.ndarray,
    default: float,
) -> np.ndarray:
    """按断点线性插值出逐采样参数；区间外保持端点值，无断点时为默认值。"""
    values = [(p.time, getattr(p, key)) for p in points if getattr(p, key) is not None]
    if not values:
        return np.full(times.shape[0], default, dtype=np.float64)
    pt = np.array([v[0] for v in values], dtype=np.float64)
    pv = np.array([v[1] for v in values], dtype=np.float64)
    return np.interp(times, pt, pv, left=pv[0], right=pv[-1]).astype(np.float64)


def _db_to_gain_array(db: np.ndarray) -> np.ndarray:
    return np.power(10.0, db / 20.0)


def _pan_gains(pan: np.ndarray | float) -> tuple[np.ndarray | float, np.ndarray | float]:
    """线性恒功率近似声像：-1 全左、0 居中、1 全右。"""
    left = np.clip(1.0 - pan, 0.0, 1.0) * (1.0 / np.sqrt(2.0))
    right = np.clip(1.0 + pan, 0.0, 1.0) * (1.0 / np.sqrt(2.0))
    return left, right


def _apply_pan(audio: np.ndarray, pan_env: np.ndarray | float) -> np.ndarray:
    """把（可能单声道的）音频按逐采样声像摆位成立体声 [帧, 2]。"""
    if audio.shape[1] == 1:
        mono = audio[:, 0]
        left_g, right_g = _pan_gains(pan_env)
        return np.stack([mono * left_g, mono * right_g], axis=1)
    # 立体声素材：按声像在两声道间平衡（不改变自身宽度感）。
    left_g, right_g = _pan_gains(pan_env)
    out = audio.copy()
    out[:, 0] *= left_g
    out[:, 1] *= right_g
    return out


def _run_effect(
    audio: np.ndarray, effect: dict, sr: int, amount_env: np.ndarray | float,
) -> np.ndarray:
    """执行单个效果器。amount_env 为逐采样处理强度（0=干声，1=全效果）。"""
    kind = effect.get("type")
    amount = float(effect.get("amount", 1.0))
    wet_env = np.clip(amount_env * amount, 0.0, 1.0)

    if kind == "gain":
        gain_db = float(effect.get("gain_db", 0.0))
        scale = np.power(10.0, gain_db * wet_env / 20.0)
        return audio * scale[:, None]

    if kind in ("delay", "echo"):
        delay_sec = max(0.0, float(effect.get("delay_sec", 0.25)))
        feedback = min(0.9, max(0.0, float(effect.get("feedback", 0.3))))
        wet_level = min(1.0, max(0.0, float(effect.get("wet", 0.4))))
        frames = audio.shape[0]
        delay = int(round(delay_sec * sr))
        # 有限抽头近似；延迟时间为 0 或超出缓冲时不加回声。
        if delay <= 0 or delay >= frames:
            return audio
        wet = np.zeros_like(audio)
        scale = 1.0
        tap_delay = delay
        taps = max(1, int(np.ceil(np.log(max(1e-4, 1.0 - feedback + 1e-9)) /
                                  np.log(max(feedback, 1e-9))))) if feedback > 0 else 1
        taps = min(taps, 32)
        for _ in range(taps):
            if tap_delay >= frames:
                break
            wet[tap_delay:] += audio[:frames - tap_delay] * scale * wet_level
            scale *= feedback
            tap_delay += delay
        if isinstance(wet_env, np.ndarray) and wet_env.ndim == 1:
            wet = wet * wet_env[:, None]
        else:
            wet = wet * float(wet_env)
        return audio + wet

    raise RenderError(f"未知效果器类型: {kind!r}")


def _over_ranges(flag: np.ndarray, sr: int, max_gap_samples: int = 64) -> tuple[list[OverRange], int]:
    """把超范围采样点聚成时间区间（短间隙合并，避免报告过于碎）。"""
    idx = np.flatnonzero(flag)
    if idx.size == 0:
        return [], 0
    ranges: list[OverRange] = []
    run_start = int(idx[0])
    prev = int(idx[0])
    for value in idx[1:]:
        value = int(value)
        if value - prev > max_gap_samples:
            ranges.append((run_start, prev))
            run_start = value
        prev = value
    ranges.append((run_start, prev))
    return [
        OverRange(start=a / sr, end=(b + 1) / sr, peak=0.0)
        for a, b in ranges
    ], int(idx.size)


def _duck_envelope(level: np.ndarray, profile: dict[str, float], sr: int) -> np.ndarray:
    """由控制电平生成 0..1 的闪避深度，1 表示完全达到压低量。"""
    threshold = _db_to_gain(float(profile["threshold_db"]))
    attack = max(1, int(round(float(profile["attack_sec"]) * sr)))
    hold = max(0, int(round(float(profile["hold_sec"]) * sr)))
    release = max(1, int(round(float(profile["release_sec"]) * sr)))
    frames = level.shape[0]
    depth = np.zeros(frames, dtype=np.float64)
    if frames == 0:
        return depth

    trigger = level > threshold
    if not np.any(trigger):
        return depth

    # 把间隔短于保持时间的相邻触发片段合并，停顿期间继续保持压低。
    idx = np.flatnonzero(trigger)
    groups: list[tuple[int, int]] = []
    start = prev = int(idx[0])
    for value in idx[1:]:
        value = int(value)
        if value - prev > hold + 1:
            groups.append((start, prev))
            start = value
        prev = value
    groups.append((start, prev))

    pos = 0
    current = 0.0
    for first, last in groups:
        held_last = min(frames - 1, last + hold)
        gate_first = max(pos, first - attack)
        if gate_first > pos:
            release_frames = min(first - pos, release)
            if release_frames > 0 and current > 0.0:
                depth[pos:pos + release_frames] = current * (
                    1.0 - np.arange(1, release_frames + 1) / release
                )
        pos = gate_first
        attack_frames = max(1, first - pos)
        needed = 1.0 - current
        depth[pos:first] = current + needed * np.minimum(
            1.0, (np.arange(attack_frames) + 1) / attack_frames
        )
        depth[first:held_last + 1] = 1.0
        current = 1.0
        pos = held_last + 1

    if pos < frames and current > 0.0:
        release_frames = min(frames - pos, release)
        depth[pos:pos + release_frames] = 1.0 - np.minimum(
            1.0, np.arange(release_frames) / release
        )
    return depth


def _duck_gain(depth: np.ndarray, reduction_db: float) -> np.ndarray:
    return np.power(10.0, -float(reduction_db) * np.clip(depth, 0.0, 1.0) / 20.0)


def _render_track(
    track: Track,
    project: Project,
    load_source: SourceLoader,
    cache: dict[str, np.ndarray],
    first_frame: int,
    frames: int,
) -> np.ndarray:
    """从工程零点后的指定帧范围渲染一条轨，返回推子/声像后的立体声。"""
    sr = project.sample_rate
    end_frame = first_frame + frames
    track_bus = np.zeros((frames, 2), dtype=np.float64)

    def source(key: str) -> np.ndarray:
        if key not in cache:
            cache[key] = load_source(key)
        return cache[key]

    for clip in track.clips:
        audio = source(clip.source)
        segment = _clip_segment(audio, clip, sr)
        clip_first = int(round(clip.start * sr))
        clip_last = clip_first + segment.shape[0]
        if clip_last <= first_frame or clip_first >= end_frame:
            continue
        local_first = max(clip_first, first_frame) - first_frame
        local_last = min(clip_last, end_frame) - first_frame
        src_first = max(clip_first, first_frame) - clip_first
        part = segment[src_first:src_first + (local_last - local_first)].astype(np.float64)
        part_frames = part.shape[0]
        abs_times = (np.arange(part_frames) + first_frame + local_first) / sr
        rel_times = abs_times - clip.start
        gain_env = _db_to_gain_array(_envelope(clip.automation, "gain_db", rel_times, 0.0))
        pan_env = _envelope(clip.automation, "pan", rel_times, 0.0)
        part = part * (gain_env[:, None] * _db_to_gain(clip.gain_db))
        if part.shape[1] == 1:
            part = np.repeat(part, 2, axis=1)
        if any(p.pan is not None for p in clip.automation):
            part = _apply_pan(part, pan_env)
        track_bus[local_first:local_last] += part

    abs_times = (np.arange(frames) + first_frame) / sr
    amount_env = _envelope(track.automation, "amount", abs_times, 1.0)
    for effect in track.effects:
        track_bus = _run_effect(track_bus, effect, sr, amount_env)

    track_gain_env = _db_to_gain_array(
        _envelope(track.automation, "gain_db", abs_times, track.gain_db)
    )
    track_pan_env = _envelope(track.automation, "pan", abs_times, track.pan)
    return _apply_pan(track_bus, track_pan_env) * track_gain_env[:, None]


def render(
    project: Project,
    load_source: SourceLoader,
    duration: float | None = None,
    start_time: float = 0.0,
    end_time: float | None = None,
    include_tail: bool = False,
    should_cancel: CancelFn | None = None,
) -> RenderResult:
    """渲染工程（或其 [start_time, end_time) 区间），返回混合结果。

    本函数对输入工程与素材缓存都是只读的：重复调用结果相同，
    不会把上一次渲染的效果叠加进来。

    include_tail=True 时在 end_time 之后继续渲染效果器的自然衰减尾音，
    此时 content_end_time 是所选区间本身的结束，tail_end_time 是尾音
    真正衰减完的结束；两者相等表示没有尾音或未请求尾音。
    """
    sr = project.sample_rate
    channels = project.channels

    # 先确定渲染区间；所有有状态处理都从工程零点计算后再截取。
    timeline_end = project.timeline_length()
    requested_end = timeline_end if end_time is None else float(end_time)
    if end_time is None and duration is not None:
        requested_end = float(start_time) + duration
    start_time = max(0.0, float(start_time))
    content_end_time = max(start_time, requested_end)
    profile = project.ducking_profile()
    duck_tail = float(profile["release_sec"]) if profile is not None else 0.0
    render_end_time = content_end_time
    if include_tail:
        render_end_time += max(effect_tail_seconds(project), duck_tail)
    start_frame = int(round(start_time * sr))
    render_end_frame = max(start_frame, int(round(render_end_time * sr)))
    total_frames = render_end_frame - start_frame
    bus = np.zeros((total_frames, channels), dtype=np.float64)

    cache: dict[str, np.ndarray] = {}
    if profile is not None and any(t.ducking_role == "control" for t in project.tracks):
        control_level = np.zeros(render_end_frame, dtype=np.float64)
        for track in project.tracks:
            if track.ducking_role != "control":
                continue
            if should_cancel and should_cancel():
                from .audio import ExportCancelled
                raise ExportCancelled()
            rendered = _render_track(track, project, load_source, cache, 0, render_end_frame)
            control_level = np.maximum(control_level, np.max(np.abs(rendered), axis=1))
        duck_depth = _duck_envelope(control_level, profile, sr)
    else:
        duck_depth = np.zeros(render_end_frame, dtype=np.float64)

    audible_ids = {track.id for track in project.active_tracks()}
    for track in project.tracks:
        if track.id not in audible_ids:
            continue
        if should_cancel and should_cancel():
            from .audio import ExportCancelled
            raise ExportCancelled()

        track_bus = _render_track(track, project, load_source, cache, 0, render_end_frame)
        if should_cancel and should_cancel():
            from .audio import ExportCancelled
            raise ExportCancelled()
        if profile is not None and track.ducking_role == "music":
            track_bus = track_bus * _duck_gain(duck_depth, profile["reduction_db"])[:, None]
        track_bus = track_bus[start_frame:]
        if channels == 1:
            bus[:, 0] += track_bus.mean(axis=1)
        else:
            bus[:, :] += track_bus

    peak_before_master = float(np.max(np.abs(bus))) if bus.size else 0.0

    # 叠加超出输出范围（满刻度 1.0）时必须明确提示：在主控推子前检测。
    over_flags = np.max(np.abs(bus), axis=1) > 1.0
    ranges, over_count = _over_ranges(over_flags, sr)
    for item in ranges:
        item.start += start_time
        item.end += start_time

    duck_ranges, _duck_count = _over_ranges(duck_depth[start_frame:] > 0.05, sr, max_gap_samples=1)
    for item in duck_ranges:
        item.start += start_time
        item.end += start_time

    master = bus * project.master_gain()
    peak = float(np.max(np.abs(master))) if master.size else 0.0

    return RenderResult(
        mix=master.astype(np.float32),
        sample_rate=sr,
        channels=channels,
        peak_before_master=peak_before_master,
        peak=peak,
        over_range=over_count > 0 or peak > 1.0,
        over_count=over_count,
        over_ranges=ranges,
        duck_ranges=duck_ranges,
        duration=total_frames / sr,
        start_time=start_frame / sr,
        content_end_time=content_end_time,
        tail_end_time=start_frame / sr + total_frames / sr,
    )

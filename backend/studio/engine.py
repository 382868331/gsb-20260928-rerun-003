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

区间渲染与整段渲染含义统一：效果器（如延迟/回声）在区间起点之前
的状态会被回溯补齐——每条轨道按效果器链的尾长向前扩展渲染窗口，
效果处理完再切回请求区间，因此分段渲染拼接与整段渲染逐采样一致，
音量/声像自动化在区间边界也连续（断点按工程绝对时间逐采样插值）。
传 include_tail=True 时，导出区间会在 end 之后附带效果自然衰减的
尾音；结果里 content_end_time 是片段本身的结束，end_time 是含尾音
的结束。
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
    duration: float = 0.0
    start_time: float = 0.0
    end_time: float = 0.0            # 实际渲染结束（含尾音时大于内容结束）
    content_end_time: float = 0.0    # 片段本身的结束（请求的 end_time）


class RenderError(Exception):
    pass


def _delay_taps(feedback: float) -> int:
    """延迟效果器的有限抽头数（与 _run_effect 中保持一致）。"""
    if feedback <= 0:
        return 1
    taps = int(np.ceil(np.log(max(1e-4, 1.0 - feedback + 1e-9)) /
                       np.log(max(feedback, 1e-9))))
    return min(max(1, taps), 32)


def _effect_tail_frames(effect: dict, sr: int) -> int:
    """单个效果器在输入结束后仍会输出的尾长（帧）。"""
    kind = effect.get("type")
    if kind in ("delay", "echo"):
        delay_sec = max(0.0, float(effect.get("delay_sec", 0.25)))
        feedback = min(0.9, max(0.0, float(effect.get("feedback", 0.3))))
        delay = int(round(delay_sec * sr))
        if delay <= 0:
            return 0
        return delay * _delay_taps(feedback)
    return 0


def chain_tail_frames(effects: Sequence[dict], sr: int) -> int:
    """效果器链的总尾长：逐级串联时尾长相加（保守上界）。"""
    return sum(_effect_tail_frames(effect, sr) for effect in effects)


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
        taps = _delay_taps(feedback)
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

    include_tail=True 时，区间在 end_time 之后附带效果器自然衰减的
    尾音：content_end_time 仍是请求的片段结束，end_time 是含尾音的
    实际结束。
    """
    sr = project.sample_rate
    channels = project.channels

    # 先确定渲染区间。默认渲染整段（长度由所有素材决定，含静音/未独听轨）。
    timeline_end = project.timeline_length()
    if end_time is None:
        end_time = timeline_end if duration is None else start_time + duration
    start_time = max(0.0, float(start_time))
    end_time = max(start_time, float(end_time))
    start_frame = int(round(start_time * sr))
    content_end_frame = int(round(end_time * sr))
    content_end_time = end_time

    active = project.active_tracks()
    if include_tail:
        # 附带效果尾音：在内容结束后再延长效果器链的自然衰减长度。
        tail = max((chain_tail_frames(t.effects, sr) for t in active), default=0)
        end_frame = content_end_frame + tail
    else:
        end_frame = content_end_frame
    total_frames = end_frame - start_frame
    total_frames = max(0, total_frames)
    bus = np.zeros((total_frames, channels), dtype=np.float64)

    cache: dict[str, np.ndarray] = {}

    def get_source(key: str) -> np.ndarray:
        if key not in cache:
            data = load_source(key)
            if data.ndim != 2:
                raise RenderError(f"素材 {key} 未统一到工程格式")
            cache[key] = data
        return cache[key]

    for track in active:
        if should_cancel and should_cancel():
            from .audio import ExportCancelled
            raise ExportCancelled()

        # 为效果器状态回溯：按效果器链尾长把窗口向前扩展，让区间起点
        # 之前的输入（如尚未衰减完的回声）进入效果器；处理完再切回。
        lookback = chain_tail_frames(track.effects, sr)
        ext_start = max(0, start_frame - lookback)
        ext_frames = end_frame - ext_start
        # 先把整条轨道在（扩展后的）渲染区间内的素材混合成轨道缓冲。
        track_bus = np.zeros((ext_frames, 2), dtype=np.float64)
        for clip in track.clips:
            source = get_source(clip.source)  # 可能是单声道或立体声缓存
            segment = _clip_segment(source, clip, sr)
            clip_first = int(round(clip.start * sr))
            clip_last = clip_first + segment.shape[0]
            if clip_last <= ext_start or clip_first >= ext_start + ext_frames:
                continue
            local_first = max(clip_first, ext_start) - ext_start
            local_last = min(clip_last, ext_start + ext_frames) - ext_start
            src_first = max(clip_first, ext_start) - clip_first
            part = segment[src_first:src_first + (local_last - local_first)].astype(np.float64)

            frames_n = local_last - local_first
            local_times = (np.arange(frames_n) + ext_start + local_first) / sr
            rel_times = local_times - clip.start
            # 素材内自动化：时间相对素材起点，移动素材时断点一起移动。
            gain_env = _db_to_gain_array(_envelope(clip.automation, "gain_db", rel_times, 0.0))
            pan_env = _envelope(clip.automation, "pan", rel_times, 0.0)
            part = part * (gain_env[:, None] * _db_to_gain(clip.gain_db))
            # 素材声像自动化时才在素材级摆位；否则保持原声道，声像交给轨道级统一处理。
            if part.shape[1] == 1:
                part = np.repeat(part, 2, axis=1)
            if any(p.pan is not None for p in clip.automation):
                part = _apply_pan(part, pan_env)
            track_bus[local_first:local_last] += part

        if should_cancel and should_cancel():
            from .audio import ExportCancelled
            raise ExportCancelled()

        # 轨道效果器链：强度可被工程绝对时间自动化（不随素材移动）。
        ext_times = (np.arange(ext_frames) + ext_start) / sr
        amount_env = _envelope(track.automation, "amount", ext_times, 1.0)
        for effect in track.effects:
            # amount 自动化对整条链统一生效（无断点时效果器用自身 amount）。
            track_bus = _run_effect(track_bus, effect, sr, amount_env)

        # 效果处理完成，切回请求区间（丢弃为回溯状态而多渲染的前缀）。
        offset = start_frame - ext_start
        track_bus = track_bus[offset:offset + total_frames]

        # 轨道推子与轨道级自动化：绝对工程时间，素材移动不影响它们。
        abs_times = (np.arange(total_frames) + start_frame) / sr
        track_gain_env = _db_to_gain_array(
            _envelope(track.automation, "gain_db", abs_times, track.gain_db)
        )
        track_pan_env = _envelope(track.automation, "pan", abs_times, track.pan)
        track_bus = _apply_pan(track_bus, track_pan_env) * track_gain_env[:, None]
        if channels == 1:
            # 工程为单声道时，把摆位后的立体声等比例混合回单声道。
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
        duration=total_frames / sr,
        start_time=start_frame / sr,
        end_time=(start_frame + total_frames) / sr,
        content_end_time=content_end_time,
    )

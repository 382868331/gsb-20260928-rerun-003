"""混音引擎：轨道 -> 输出的处理顺序在这里固定下来。

对每条被听见的轨道：
    源音频 -> 按素材 in/out 裁切 -> 素材增益 -> 轨道效果器链(预留)
    -> 轨道推子增益 -> 叠加到母线
母线：各轨叠加 -> 主控增益 -> 输出范围检测 -> 导出
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .model import Clip, Project, Track, _db_to_gain


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


class RenderError(Exception):
    pass


def _clip_segment(source: np.ndarray, clip: Clip, sr: int) -> np.ndarray:
    """按素材 in/out 取源区间（非破坏：只切视图副本）。"""
    first = int(round(clip.in_point * sr))
    last = int(round(clip.out_point * sr))
    first = max(0, min(first, source.shape[0]))
    last = max(first, min(last, source.shape[0]))
    return source[first:last]


def _apply_effects(track_audio: np.ndarray, track: Track, sr: int) -> np.ndarray:
    """轨道效果器链位置预留。以后效果器按 effects 列表顺序在此插入。"""
    for effect in track.effects:
        raise RenderError(
            f"效果器 '{effect.get('type', '?')}' 尚未实现；处理位已在轨道 {track.name} 上预留"
        )
    return track_audio


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
    should_cancel: CancelFn | None = None,
) -> RenderResult:
    sr = project.sample_rate
    channels = project.channels

    # 先确定总长度（由时间线最末端素材决定，可外部指定）。
    timeline_end = project.timeline_length()
    if duration is None:
        duration = timeline_end
    total_frames = int(round(duration * sr))
    bus = np.zeros((total_frames, channels), dtype=np.float64)

    cache: dict[str, np.ndarray] = {}

    def get_source(key: str) -> np.ndarray:
        if key not in cache:
            data = load_source(key)
            if data.ndim != 2 or data.shape[1] != channels:
                raise RenderError(f"素材 {key} 未统一到工程格式")
            cache[key] = data
        return cache[key]

    for track in project.active_tracks():
        track_gain = _db_to_gain(track.gain_db)
        for clip in track.clips:
            if should_cancel and should_cancel():
                from .audio import ExportCancelled
                raise ExportCancelled()
            source = get_source(clip.source)
            segment = _clip_segment(source, clip, sr)
            if clip.gain_db:
                segment = segment * _db_to_gain(clip.gain_db)
            # 轨道效果器链（预留位置，目前为空链）。
            segment = _apply_effects(segment, track, sr)
            segment = segment * track_gain

            start_frame = int(round(clip.start * sr))
            end_frame = start_frame + segment.shape[0]
            if end_frame <= 0 or start_frame >= total_frames:
                continue
            src_start = max(0, -start_frame)
            dst_start = max(0, start_frame)
            dst_end = min(total_frames, end_frame)
            bus[dst_start:dst_end] += segment[src_start:src_start + (dst_end - dst_start)]

    peak_before_master = float(np.max(np.abs(bus))) if bus.size else 0.0

    # 叠加超出输出范围（满刻度 1.0）时必须明确提示：在主控推子前检测。
    over_flags = np.max(np.abs(bus), axis=1) > 1.0
    ranges, over_count = _over_ranges(over_flags, sr)

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
    )

"""混音引擎：源素材、自动化、效果器与母线的处理顺序在这里固定。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

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
    mix: np.ndarray
    sample_rate: int
    channels: int
    peak_before_master: float
    peak: float
    over_range: bool
    over_count: int
    over_ranges: list[OverRange] = field(default_factory=list)
    duration: float = 0.0


class RenderError(Exception):
    pass


def _clip_segment(source: np.ndarray, clip: Clip, sr: int) -> np.ndarray:
    first = max(0, min(int(round(clip.in_point * sr)), source.shape[0]))
    last = max(first, min(int(round(clip.out_point * sr)), source.shape[0]))
    return source[first:last]


def _automation_curve(
    points: list[AutomationPoint],
    frames: int,
    sr: int,
    offset: float = 0.0,
    default: float = 1.0,
) -> np.ndarray:
    if frames <= 0:
        return np.zeros(0, dtype=np.float64)
    if not points:
        return np.full(frames, default, dtype=np.float64)
    ordered = sorted(points, key=lambda point: point.time)
    times = np.asarray([point.time for point in ordered], dtype=np.float64)
    values = np.asarray([point.value for point in ordered], dtype=np.float64)
    query = np.arange(frames, dtype=np.float64) / sr + offset
    return np.interp(query, times, values, values[0], values[-1]).astype(np.float64)


def _amount_points(data: dict[str, Any]) -> list[AutomationPoint]:
    return [AutomationPoint(float(item["time"]), float(item["value"])) for item in data.get("amount_points", [])]


def _apply_pan(audio: np.ndarray, pan: np.ndarray) -> np.ndarray:
    if audio.shape[1] != 2:
        return audio
    panned = np.empty_like(audio, dtype=np.float64)
    panned[:, 0] = audio[:, 0] * np.sqrt(np.maximum(0.0, 1.0 - pan))
    panned[:, 1] = audio[:, 1] * np.sqrt(np.maximum(0.0, 1.0 + pan))
    return panned


def _apply_one_effect(audio: np.ndarray, effect: dict[str, Any], sr: int) -> np.ndarray:
    kind = str(effect.get("type", ""))
    amount = _automation_curve(_amount_points(effect), audio.shape[0], sr, default=1.0)
    amount = np.clip(amount, 0.0, 1.0)[:, None]

    if kind == "gain":
        gain_db = float(effect.get("gain_db", 0.0))
        return audio * np.power(_db_to_gain(gain_db), amount)

    if kind == "delay":
        seconds = max(0.001, float(effect.get("time", 0.25)))
        feedback = min(0.9, max(0.0, float(effect.get("feedback", 0.25))))
        mix = min(1.0, max(0.0, float(effect.get("mix", 0.3))))
        delay_frames = max(1, int(round(seconds * sr)))
        wet = np.zeros_like(audio)
        scale = 1.0
        for tap in range(1, 5):
            start = delay_frames * tap
            if start >= audio.shape[0]:
                break
            scale *= feedback
            wet[start:] += audio[:-start] * scale
        return audio + wet * (mix * amount)

    if kind == "lowpass":
        cutoff_hz = min(float(sr) * 0.49, max(20.0, float(effect.get("cutoff_hz", 1000.0))))
        alpha = float(np.exp(-2.0 * np.pi * cutoff_hz / sr))
        wet = np.zeros_like(audio)
        previous = np.zeros(audio.shape[1], dtype=np.float64)
        for frame in range(audio.shape[0]):
            previous = (1.0 - alpha) * audio[frame] + alpha * previous
            wet[frame] = previous
        return audio * (1.0 - amount) + wet * amount

    raise RenderError(f"未知效果器类型: {kind}")


def _apply_track_effects(audio: np.ndarray, track: Track, sr: int) -> np.ndarray:
    result = audio.astype(np.float64, copy=True)
    for effect in track.effects:
        result = _apply_one_effect(result, effect, sr)
    return result


def _over_ranges(flag: np.ndarray, sr: int, max_gap_samples: int = 64) -> tuple[list[OverRange], int]:
    idx = np.flatnonzero(flag)
    if idx.size == 0:
        return [], 0
    ranges: list[tuple[int, int]] = []
    run_start = int(idx[0])
    previous = int(idx[0])
    for value in idx[1:]:
        value = int(value)
        if value - previous > max_gap_samples:
            ranges.append((run_start, previous))
            run_start = value
        previous = value
    ranges.append((run_start, previous))
    return [OverRange(start=a / sr, end=(b + 1) / sr, peak=0.0) for a, b in ranges], int(idx.size)


def render(
    project: Project,
    load_source: SourceLoader,
    duration: float | None = None,
    should_cancel: CancelFn | None = None,
) -> RenderResult:
    sr = project.sample_rate
    channels = project.channels
    render_duration = project.timeline_length() if duration is None else max(0.0, float(duration))
    total_frames = int(round(render_duration * sr))
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
        if should_cancel and should_cancel():
            from .audio import ExportCancelled
            raise ExportCancelled()

        track_bus = np.zeros_like(bus)
        for clip in track.clips:
            source = get_source(clip.source)
            segment = _clip_segment(source, clip, sr).astype(np.float64, copy=False)
            if segment.shape[0] == 0:
                continue

            volume = _automation_curve(clip.volume_points, segment.shape[0], sr)
            if np.any(volume < 0.0):
                raise RenderError(f"素材 {clip.id} 的音量不能为负")
            segment = segment * (volume * _db_to_gain(clip.gain_db))[:, None]
            segment = _apply_pan(
                segment,
                np.clip(_automation_curve(clip.pan_points, segment.shape[0], sr, default=0.0), -1.0, 1.0),
            )

            start_frame = int(round(clip.start * sr))
            end_frame = start_frame + segment.shape[0]
            if end_frame <= 0 or start_frame >= total_frames:
                continue
            src_start = max(0, -start_frame)
            dst_start = max(0, start_frame)
            dst_end = min(total_frames, end_frame)
            track_bus[dst_start:dst_end] += segment[src_start:src_start + (dst_end - dst_start)]

        track_bus = _apply_track_effects(track_bus, track, sr)
        track_volume = _automation_curve(track.volume_points, total_frames, sr)
        if np.any(track_volume < 0.0):
            raise RenderError(f"轨道 {track.name} 的音量不能为负")
        track_bus *= track_volume[:, None]
        track_bus = _apply_pan(
            track_bus,
            np.clip(_automation_curve(track.pan_points, total_frames, sr, default=0.0), -1.0, 1.0),
        )
        track_bus *= _db_to_gain(track.gain_db)
        bus += track_bus

    peak_before_master = float(np.max(np.abs(bus))) if bus.size else 0.0
    over_flags = np.max(np.abs(bus), axis=1) > 1.0 if bus.size else np.zeros(0, dtype=bool)
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

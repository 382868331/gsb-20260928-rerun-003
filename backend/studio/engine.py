"""混音引擎：清晰的 素材 → 轨道 → 输出 处理链。

处理顺序（每个阶段独立，便于以后插入效果器）：

  1. Clip 阶段  : 从素材缓存按 start/duration 裁切 → clip gain
  2. Track 阶段 : 同轨片段叠加 → 静音/独听判定 → track gain
                  （效果器以后插在这里，位于 track gain 前后可扩展）
  3. Master 阶段: 各轨道求和 → 峰值/削波检测 → 限幅写文件

轨道数为 1/2 声道混用时统一按立体声总线处理（单声道放到左右相同）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf

from .assets import AssetLibrary
from .models import Clip, Project, Track

CancelCheck = Callable[[], None]
ProgressCb = Callable[[float], None]

EPS = 1e-9
CLIP_WARN = 1.0


class RenderCancelled(Exception):
    """渲染被用户取消。"""


def _stereo(audio: np.ndarray) -> np.ndarray:
    if audio.shape[1] == 1:
        return np.repeat(audio, 2, axis=1)
    return audio[:, :2]


def _clip_segment(library: AssetLibrary, clip: Clip, sr: int) -> np.ndarray:
    """阶段1：非破坏性裁切。"""
    audio = library.load_audio(clip.asset_id)
    start = int(round(clip.start * sr))
    length = int(round(clip.duration * sr))
    start = max(0, min(start, audio.shape[0]))
    available = max(0, audio.shape[0] - start)
    length = max(0, min(length, available))
    seg = audio[start : start + length]
    if clip.gain != 1.0:
        seg = seg * clip.gain
    wanted = int(round(clip.duration * sr))
    if wanted > seg.shape[0]:
        # 工程引用超出素材实际长度的尾部按静音补齐
        seg = np.pad(seg, ((0, wanted - seg.shape[0]), (0, 0)))
    return seg.astype(np.float32, copy=False)


def _track_bus(
    track: Track,
    library: AssetLibrary,
    total: int,
    sr: int,
) -> np.ndarray:
    """阶段2（叠加部分）：一条轨道的片段放到总线位置。"""
    bus = np.zeros((total, 2), dtype=np.float32)
    for clip in track.clips:
        seg = _stereo(_clip_segment(library, clip, sr))
        pos = int(round(clip.offset * sr))
        end = min(total, pos + seg.shape[0])
        if pos >= total or end <= pos:
            continue
        bus[pos:end] += seg[: end - pos]
    return bus


def _timeline_length(project: Project, sr: int) -> int:
    end = 0.0
    for track in project.tracks:
        for clip in track.clips:
            end = max(end, clip.offset + clip.duration)
    return int(round(end * sr))


def render(
    project: Project,
    library: AssetLibrary,
    output_path: str | Path,
    *,
    cancel_check: CancelCheck | None = None,
    progress: ProgressCb | None = None,
) -> dict:
    """渲染工程到 output_path（float32 WAV）。

    返回 {duration, peak, clipped, channels, warnings}。
    取消时抛 RenderCancelled，且不会留下成品文件。
    """
    sr = project.sample_rate
    total = _timeline_length(project, sr)
    if total <= 0:
        raise ValueError("工程为空，没有可导出的内容")

    def check() -> None:
        if cancel_check is not None:
            cancel_check()

    # ---- 阶段2：轨道总线 + 静音/独听 ----
    any_solo = any(t.solo for t in project.tracks)
    master = np.zeros((total, 2), dtype=np.float32)
    warnings: list[str] = []
    tracks = project.tracks
    for i, track in enumerate(tracks):
        check()
        if track.muted or (any_solo and not track.solo):
            continue
        bus = _track_bus(track, library, total, sr)
        if track.gain != 1.0:
            bus *= track.gain
        master += bus
        if progress is not None:
            progress(0.1 + 0.7 * (i + 1) / max(1, len(tracks)))

    check()
    # ---- 阶段3：主总线检测 ----
    peak = float(np.max(np.abs(master))) if master.size else 0.0
    clipped = peak > CLIP_WARN + EPS
    if clipped:
        warnings.append(
            f"叠加后峰值 {peak:.3f} 超过输出范围 [-1, 1]，已削波；"
            "请降低相关轨道/素材音量或错开叠加时间。"
        )

    # 原子写入：先写临时文件，完成后再改名，取消不会留下成品
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(output_path.name + ".part")
    try:
        sf.write(tmp_path, np.clip(master, -1.0, 1.0), sr, format="WAV", subtype="FLOAT")
        if progress is not None:
            progress(0.95)
        check()
        tmp_path.replace(output_path)
    except RenderCancelled:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    if progress is not None:
        progress(1.0)
    return {
        "duration": total / sr,
        "peak": peak,
        "clipped": clipped,
        "channels": 2,
        "warnings": warnings,
    }


def render_to_array(project: Project, library: AssetLibrary) -> tuple[np.ndarray, dict]:
    """只渲染到内存数组（测试用），处理链与 render 相同。"""
    sr = project.sample_rate
    total = _timeline_length(project, sr)
    any_solo = any(t.solo for t in project.tracks)
    master = np.zeros((total, 2), dtype=np.float32)
    warnings: list[str] = []
    for track in project.tracks:
        if track.muted or (any_solo and not track.solo):
            continue
        bus = _track_bus(track, library, total, sr)
        if track.gain != 1.0:
            bus *= track.gain
        master += bus
    peak = float(np.max(np.abs(master))) if master.size else 0.0
    clipped = peak > CLIP_WARN + EPS
    if clipped:
        warnings.append(f"叠加后峰值 {peak:.3f} 超过输出范围 [-1, 1]，已削波")
    info = {
        "duration": total / sr,
        "peak": peak,
        "clipped": clipped,
        "channels": 2,
        "warnings": warnings,
    }
    return master, info

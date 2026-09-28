"""音频文件读写与采样率统一。

所有进入混音引擎的素材都会被重采样到工程采样率并统一成相同声道数；
原始文件从不被修改。
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import Callable

import numpy as np
import soundfile as sf


@dataclass
class AudioInfo:
    sample_rate: int
    channels: int
    frames: int
    duration: float
    subtype: str


def probe(path: str) -> AudioInfo:
    info = sf.info(path)
    return AudioInfo(
        sample_rate=info.samplerate,
        channels=info.channels,
        frames=info.frames,
        duration=float(info.frames) / info.samplerate,
        subtype=info.subtype,
    )


def load_mono_or_stereo(path: str) -> tuple[np.ndarray, int]:
    """读取音频，返回 (二维 float32 数组 [帧, 声道], 采样率)。"""
    data, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    return data, int(sample_rate)


def _resample(data: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    if src_sr == dst_sr:
        return data
    # 高质量重采样走 scipy 的 polyphase 滤波。
    from math import gcd

    from scipy.signal import resample_poly

    g = gcd(src_sr, dst_sr)
    up = dst_sr // g
    down = src_sr // g
    out = resample_poly(data, up, down, axis=0).astype(np.float32)
    return np.ascontiguousarray(out)


def _adapt_channels(data: np.ndarray, channels: int) -> np.ndarray:
    current = data.shape[1]
    if current == channels:
        return data
    if channels == 1:
        return data.mean(axis=1, keepdims=True).astype(np.float32)
    if channels == 2:
        if current == 1:
            return np.repeat(data, 2, axis=1)
        # 多声道（如 5.1）先混成单声道再展成立体声。
        mono = data.mean(axis=1, keepdims=True)
        return np.repeat(mono, 2, axis=1).astype(np.float32)
    raise ValueError(f"不支持的目标声道数: {channels}")


def load_for_project(path: str, project_sr: int, project_channels: int) -> tuple[np.ndarray, AudioInfo]:
    """读取素材并重采样/转成工程格式。返回 (处理后数据, 原始文件信息)。"""
    data, src_sr = load_mono_or_stereo(path)
    info = probe(path)
    data = _resample(data, src_sr, project_sr)
    data = _adapt_channels(data, project_channels)
    return np.ascontiguousarray(data, dtype=np.float32), info


CancelFn = Callable[[], bool]


class ExportCancelled(Exception):
    """用户取消导出。"""


def write_wav_atomic(
    path: str,
    data: np.ndarray,
    sample_rate: int,
    subtype: str = "PCM_16",
    should_cancel: CancelFn | None = None,
    progress: Callable[[float], None] | None = None,
) -> None:
    """原子写入 WAV：先写同目录临时文件，成功后再替换到目标路径。

    取消时删除临时文件，目标路径不会留下任何冒充成品的文件。
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".export-", suffix=".wav.part", dir=directory)
    os.close(fd)
    try:
        if should_cancel and should_cancel():
            raise ExportCancelled()

        # 分块写入以便取消与进度回调。
        block = max(1, sample_rate)  # 约 1 秒一块
        frames = data.shape[0]
        with sf.SoundFile(tmp_path, mode="w", samplerate=sample_rate,
                          channels=data.shape[1], subtype=subtype,
                          format="WAV") as out:
            for start in range(0, frames, block):
                if should_cancel and should_cancel():
                    raise ExportCancelled()
                out.write(data[start:start + block])
                if progress is not None:
                    progress(min(1.0, (start + block) / frames))
        if progress is not None:
            progress(1.0)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def waveform_peaks(data: np.ndarray, buckets: int = 1000) -> list[float]:
    """供前端画波形用的归一化峰值包络（取所有声道最大绝对值）。"""
    mono = np.max(np.abs(data), axis=1) if data.ndim == 2 else np.abs(data)
    frames = mono.shape[0]
    if frames == 0:
        return []
    buckets = max(1, min(buckets, frames))
    split = np.array_split(mono, buckets)
    return [float(np.max(chunk)) if chunk.size else 0.0 for chunk in split]

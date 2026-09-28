#!/usr/bin/env python3
"""生成几段可辨认的人工声音，用来核对时间位置与混音结果。

三段特征明确的提示音（靠听感与频谱即可分辨）：
  voice-a-low.wav    440Hz，反复播放摩斯 A（·-）节奏
  voice-b-high.wav   880Hz 连续音，22050Hz 采样率（验证采样率统一）
  voice-c-pulse.wav  1760Hz 急促脉冲（验证叠加段与超范围提示）
"""

from __future__ import annotations

import os
import sys

import numpy as np
import soundfile as sf


def envelope(sig: np.ndarray, sr: int, attack: float = 0.005, release: float = 0.02) -> np.ndarray:
    n = sig.shape[0]
    a = int(attack * sr)
    r = int(release * sr)
    env = np.ones(n, dtype=np.float32)
    if a > 0:
        env[:a] = np.linspace(0, 1, a)
    if r > 0:
        env[-r:] = np.linspace(1, 0, r)
    return sig * env


def tone(freq: float, duration: float, sr: int, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(duration * sr)) / sr
    return envelope((amp * np.sin(2 * np.pi * freq * t)).astype(np.float32), sr)


def morse_a(freq: float, sr: int, duration: float = 3.0) -> np.ndarray:
    """反复播放摩斯 A（短声 + 长声），每秒一组。"""
    out = np.zeros(int(duration * sr), dtype=np.float32)
    unit = 0.15
    pattern = [(0.0, unit), (2 * unit + 0.1, 3 * unit)]
    cursor = 0.0
    while cursor < duration:
        for offset, length in pattern:
            start = int((cursor + offset) * sr)
            burst = tone(freq, length, sr)
            end = min(out.shape[0], start + burst.shape[0])
            if start < out.shape[0]:
                out[start:end] += burst[:end - start]
        cursor += 1.0
    return out


def pulses(freq: float, sr: int, duration: float = 2.0, rate: float = 8.0) -> np.ndarray:
    out = np.zeros(int(duration * sr), dtype=np.float32)
    for i in range(int(duration * rate)):
        center = int((i + 0.5) / rate * sr)
        burst = tone(freq, 0.05, sr, amp=0.8)
        start = max(0, center - burst.shape[0] // 2)
        end = min(out.shape[0], start + burst.shape[0])
        out[start:end] += burst[:end - start]
    return out


def main(out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    sf.write(os.path.join(out_dir, "voice-a-low.wav"), morse_a(440, 44100)[:, None], 44100, subtype="PCM_16")
    sf.write(os.path.join(out_dir, "voice-b-high.wav"), tone(880, 2.0, 22050)[:, None], 22050, subtype="PCM_16")
    sf.write(os.path.join(out_dir, "voice-c-pulse.wav"), pulses(1760, 44100)[:, None], 44100, subtype="PCM_16")
    print(f"已在 {out_dir} 生成 3 段人工核对素材")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "samples"))

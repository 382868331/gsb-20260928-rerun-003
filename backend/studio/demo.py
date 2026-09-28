"""生成几段可辨认的人工声音素材（不同频率/节奏型），用于核对时间位置与混音。

用法：python -m studio.demo <输出目录>
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import soundfile as sf


def tone(freq: float, beats: list[float], sr: int, beat_dur: float = 0.25, amp: float = 0.5) -> np.ndarray:
    """按节拍序列发音：beats 中 1 为响、0 为静音。"""
    out: list[np.ndarray] = []
    for b in beats:
        n = int(sr * beat_dur)
        t = np.arange(n) / sr
        seg = amp * np.sin(2 * np.pi * freq * t) if b else np.zeros(n, np.float32)
        fade = int(0.01 * sr)
        if b and fade:
            env = np.ones(n, np.float32)
            env[:fade] = np.linspace(0, 1, fade)
            env[-fade:] = np.linspace(1, 0, fade)
            seg = seg.astype(np.float32) * env
        out.append(seg.astype(np.float32))
    return np.concatenate(out)


def main(out_dir: str) -> None:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    # 低频“鼓声”型、不同采样率；中频“旋律”；高频“铃声”
    specs = [
        ("demo_low_220.wav", 220.0, [1, 0, 1, 0, 1, 0, 1, 0], 44100),
        ("demo_mid_440.wav", 440.0, [1, 1, 0, 1, 1, 0, 1, 1], 48000),
        ("demo_high_880.wav", 880.0, [0, 0, 1, 0, 0, 0, 1, 0], 48000),
    ]
    for name, freq, beats, sr in specs:
        sf.write(d / name, tone(freq, beats, sr), sr, subtype="FLOAT")
        print("wrote", d / name, f"({sr} Hz)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "demo_assets")

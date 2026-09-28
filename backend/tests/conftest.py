import numpy as np
import pytest
import soundfile as sf

from studio.assets import AssetLibrary


def make_tone(freq: float, sr: int, duration: float = 1.0, amp: float = 0.5) -> np.ndarray:
    """生成可辨认的人工声音：带淡入淡出的纯音。"""
    t = np.arange(int(sr * duration)) / sr
    audio = amp * np.sin(2 * np.pi * freq * t)
    fade = int(0.01 * sr)
    if fade > 0:
        env = np.ones_like(audio)
        env[:fade] = np.linspace(0, 1, fade)
        env[-fade:] = np.linspace(1, 0, fade)
        audio *= env
    return audio.astype(np.float32)


@pytest.fixture
def library(tmp_path):
    return AssetLibrary(tmp_path / "data")


@pytest.fixture
def tone_assets(library, tmp_path):
    """三段不同频率、其中一段不同采样率的素材。"""
    specs = [(220.0, 48000, "low.wav"), (440.0, 44100, "mid.wav"), (880.0, 48000, "high.wav")]
    metas = []
    for freq, sr, name in specs:
        p = tmp_path / name
        sf.write(p, make_tone(freq, sr), sr, subtype="FLOAT")
        metas.append(library.import_file(p, project_sr=48000))
    return metas


def dominant_freq(audio: np.ndarray, sr: int, fmin: float = 50.0) -> float:
    mono = audio.mean(axis=1) if audio.ndim == 2 else audio
    spec = np.abs(np.fft.rfft(mono))
    freqs = np.fft.rfftfreq(mono.shape[0], 1 / sr)
    mask = freqs >= fmin
    return float(freqs[mask][np.argmax(spec[mask])])


def energy(audio: np.ndarray) -> float:
    return float(np.sum(audio**2))

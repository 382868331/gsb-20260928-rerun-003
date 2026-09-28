"""素材库：统一采样率、缓存解码数据、波形峰值。

导入时把任意采样率的素材重采样到工程采样率并保存为无损缓存。
原始文件保留不动，裁切/移动都只是引用参数。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from .models import AssetMeta

PEAK_BINS = 256


def _to_float(audio: np.ndarray) -> np.ndarray:
    return audio.astype(np.float32, copy=False)


def _downmix_shape(audio: np.ndarray) -> np.ndarray:
    """统一形状为 (samples, channels)。"""
    if audio.ndim == 1:
        return audio[:, None]
    return audio


def _resample(audio: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    if src_sr == dst_sr:
        return audio.astype(np.float32, copy=False)
    from math import gcd

    g = gcd(src_sr, dst_sr)
    up = dst_sr // g
    down = src_sr // g
    return resample_poly(audio, up, down, axis=0).astype(np.float32)


def compute_peaks(audio: np.ndarray, bins: int = PEAK_BINS) -> list[float]:
    mono = np.max(np.abs(audio), axis=1)
    n = mono.shape[0]
    if n == 0:
        return []
    edges = np.linspace(0, n, bins + 1, dtype=np.int64)
    out: list[float] = []
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        if hi > lo:
            out.append(float(np.max(mono[lo:hi])))
        else:
            out.append(0.0)
    return out


class AssetLibrary:
    """管理某个数据目录下的素材。线程安全。"""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.assets_dir = self.root / "assets"
        self.cache_dir = self.root / "cache"
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._meta_cache: dict[str, AssetMeta] = {}

    def _meta_path(self, asset_id: str) -> Path:
        return self.assets_dir / f"{asset_id}.json"

    def _data_path(self, asset_id: str) -> Path:
        return self.cache_dir / f"{asset_id}.npy"

    def import_file(self, path: str | Path, project_sr: int, asset_id: str | None = None) -> AssetMeta:
        path = Path(path)
        audio, src_sr = sf.read(path, always_2d=False, dtype="float32")
        audio = _to_float(audio)
        audio = _downmix_shape(audio)
        audio = _resample(audio, src_sr, project_sr)

        if asset_id is None:
            from .models import new_id

            asset_id = new_id()
        with self._lock:
            np.save(self._data_path(asset_id), audio)
            meta = AssetMeta(
                id=asset_id,
                original_name=path.name,
                sample_rate=project_sr,
                source_sample_rate=int(src_sr),
                channels=audio.shape[1],
                duration=audio.shape[0] / project_sr,
                peaks=compute_peaks(audio),
            )
            self._meta_path(asset_id).write_text(meta.model_dump_json(), encoding="utf-8")
            self._meta_cache[asset_id] = meta
        return meta

    def save_meta(self, meta: AssetMeta) -> None:
        with self._lock:
            self._meta_path(meta.id).write_text(meta.model_dump_json(), encoding="utf-8")
            self._meta_cache[meta.id] = meta

    def list_assets(self) -> list[AssetMeta]:
        metas = []
        for p in sorted(self.assets_dir.glob("*.json")):
            try:
                metas.append(AssetMeta(**json.loads(p.read_text(encoding="utf-8"))))
            except Exception:
                continue
        return metas

    def get_meta(self, asset_id: str) -> AssetMeta:
        with self._lock:
            cached = self._meta_cache.get(asset_id)
            if cached is not None:
                return cached
        p = self._meta_path(asset_id)
        if not p.exists():
            raise KeyError(f"素材不存在: {asset_id}")
        meta = AssetMeta(**json.loads(p.read_text(encoding="utf-8")))
        with self._lock:
            self._meta_cache[asset_id] = meta
        return meta

    def load_audio(self, asset_id: str) -> np.ndarray:
        """返回 (samples, channels) float32，采样率为工程采样率。"""
        self.get_meta(asset_id)  # 存在性检查
        p = self._data_path(asset_id)
        if not p.exists():
            raise KeyError(f"素材缓存丢失: {asset_id}")
        return np.load(p, mmap_mode=None)

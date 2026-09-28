"""工程数据模型：轨道、素材（片段）、工程。

所有时间单位均为秒（浮点）。素材的移动与裁切只记录偏移量，
不改动源音频文件，因此工程保存后可以无损还原同样的声音。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


def _db_to_gain(db: float) -> float:
    if db is None or math.isnan(db):
        return 1.0
    return float(10.0 ** (db / 20.0))


@dataclass
class Clip:
    """时间线上的一段素材（对源文件的非破坏引用）。

    source: 源文件标识（相对媒体目录的路径，由保存层负责解析）。
    start:  素材起点在时间线上的位置（秒）。
    in_point / out_point: 使用源文件的 [in_point, out_point) 区间。
    gain_db: 素材推子前增益（分贝）。
    """

    id: str
    source: str
    start: float = 0.0
    in_point: float = 0.0
    out_point: float = 0.0
    gain_db: float = 0.0

    @property
    def duration(self) -> float:
        return max(0.0, self.out_point - self.in_point)

    @property
    def end(self) -> float:
        return self.start + self.duration

    def trim(self, in_point: float | None = None, out_point: float | None = None,
             source_duration: float | None = None) -> None:
        """裁切：只改引用区间，不触碰源文件。

        传入 source_duration 时把区间约束在源文件范围内。
        """
        if in_point is not None:
            self.in_point = max(0.0, float(in_point))
        if out_point is not None:
            self.out_point = max(self.in_point, float(out_point))
        if source_duration is not None:
            self.in_point = min(self.in_point, float(source_duration))
            self.out_point = min(self.out_point, float(source_duration))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "start": round(self.start, 6),
            "in_point": round(self.in_point, 6),
            "out_point": round(self.out_point, 6),
            "gain_db": self.gain_db,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Clip":
        clip = cls(
            id=str(data["id"]),
            source=str(data["source"]),
            start=float(data.get("start", 0.0)),
            in_point=float(data.get("in_point", 0.0)),
            out_point=float(data.get("out_point", 0.0)),
            gain_db=float(data.get("gain_db", 0.0)),
        )
        if clip.out_point < clip.in_point:
            raise ValueError(f"素材 {clip.id} 的 out_point 早于 in_point")
        return clip


@dataclass
class Track:
    """一条轨道：素材列表 + 静音/独听 + 推子增益 + 预留效果器链。"""

    id: str
    name: str = "轨道"
    clips: list[Clip] = field(default_factory=list)
    muted: bool = False
    solo: bool = False
    gain_db: float = 0.0
    # 效果器以后再加：这里先固定处理顺序的位置（参数化效果器节点列表）。
    effects: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "muted": self.muted,
            "solo": self.solo,
            "gain_db": self.gain_db,
            "effects": list(self.effects),
            "clips": [c.to_dict() for c in self.clips],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Track":
        return cls(
            id=str(data["id"]),
            name=str(data.get("name", "轨道")),
            clips=[Clip.from_dict(c) for c in data.get("clips", [])],
            muted=bool(data.get("muted", False)),
            solo=bool(data.get("solo", False)),
            gain_db=float(data.get("gain_db", 0.0)),
            effects=list(data.get("effects", [])),
        )


@dataclass
class Project:
    """整个混音工程。sample_rate 为统一输出采样率。"""

    name: str = "未命名工程"
    sample_rate: int = 44100
    channels: int = 2
    master_gain_db: float = 0.0
    tracks: list[Track] = field(default_factory=list)
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "audio-studio-project",
            "version": self.version,
            "name": self.name,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "master_gain_db": self.master_gain_db,
            "tracks": [t.to_dict() for t in self.tracks],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Project":
        if data.get("format") != "audio-studio-project":
            raise ValueError("不是本工作室的工程文件")
        project = cls(
            name=str(data.get("name", "未命名工程")),
            sample_rate=int(data.get("sample_rate", 44100)),
            channels=int(data.get("channels", 2)),
            master_gain_db=float(data.get("master_gain_db", 0.0)),
            tracks=[Track.from_dict(t) for t in data.get("tracks", [])],
            version=int(data.get("version", 1)),
        )
        if project.sample_rate <= 0:
            raise ValueError("工程采样率非法")
        if project.channels not in (1, 2):
            raise ValueError("目前只支持单声道/立体声输出")
        return project

    def active_tracks(self) -> list[Track]:
        """独听/静音裁决：存在独听轨时只听独听轨；被静音的轨始终排除。"""
        any_solo = any(t.solo for t in self.tracks)
        return [
            t for t in self.tracks
            if not t.muted and (not any_solo or t.solo)
        ]

    def timeline_length(self) -> float:
        end = 0.0
        for track in self.active_tracks():
            for clip in track.clips:
                end = max(end, clip.end)
        return end

    def master_gain(self) -> float:
        return _db_to_gain(self.master_gain_db)

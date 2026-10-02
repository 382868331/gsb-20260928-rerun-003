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
class AutomationPoint:
    """一个自动化断点。

    时间含义由所属列表决定：
      - 素材（clip）上：相对素材起点的时间（秒），随素材一起移动；
      - 轨道（track）上：工程绝对时间（秒），素材移动不会改变它。

    各数值字段都是可选的：None 表示该断点不自动化此参数
    （相邻断点之间按线性插值，超出端点范围时保持端点值）。

    gain_db: 音量（分贝），素材与轨道自动化均可使用。
    pan:     声像，-1（全左）.. 0（中）.. 1（全右）。
    amount:  轨道效果处理强度 0..1（湿声/效果比例）。
    """

    time: float
    gain_db: float | None = None
    pan: float | None = None
    amount: float | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"time": round(self.time, 6)}
        if self.gain_db is not None:
            data["gain_db"] = self.gain_db
        if self.pan is not None:
            data["pan"] = self.pan
        if self.amount is not None:
            data["amount"] = self.amount
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AutomationPoint":
        def maybe_float(key: str) -> float | None:
            value = data.get(key)
            return None if value is None else float(value)

        point = cls(
            time=float(data["time"]),
            gain_db=maybe_float("gain_db"),
            pan=maybe_float("pan"),
            amount=maybe_float("amount"),
        )
        if point.pan is not None:
            point.pan = min(1.0, max(-1.0, point.pan))
        if point.amount is not None:
            point.amount = min(1.0, max(0.0, point.amount))
        return point


def _automation_to_dict(points: list[AutomationPoint]) -> list[dict[str, Any]]:
    return [p.to_dict() for p in points]


def _automation_from_dict(data: Any) -> list[AutomationPoint]:
    points = [AutomationPoint.from_dict(item) for item in (data or [])]
    points.sort(key=lambda p: p.time)
    return points


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
    # 注意：新增字段一律放在 gain_db 之后，保持旧位置参数顺序兼容。

    # 素材内部的自动化：时间相对素材起点（随素材移动）。
    # 例如起点 -inf dB、稍后 0 dB 即素材淡入；也可以放任意多段音量/声像断点。
    automation: list[AutomationPoint] = field(default_factory=list)

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

    def set_fades(self, fade_in: float = 0.0, fade_out: float = 0.0) -> None:
        """便捷地在素材首尾安排淡入/淡出（单位秒，相对素材起点）。

        只调整首尾两个音量断点，不影响中间已有的分段音量/声像断点。
        """
        fade_in = max(0.0, float(fade_in))
        fade_out = max(0.0, float(fade_out))
        self.automation = [p for p in self.automation if p.time not in (0.0, self.duration)]
        points: list[AutomationPoint] = []
        if fade_in > 0:
            points.append(AutomationPoint(0.0, gain_db=-120.0))
            points.append(AutomationPoint(min(fade_in, self.duration), gain_db=0.0))
        if fade_out > 0:
            points.append(AutomationPoint(max(0.0, self.duration - fade_out), gain_db=0.0))
            points.append(AutomationPoint(self.duration, gain_db=-120.0))
        self.automation.extend(points)
        self.automation.sort(key=lambda p: p.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "start": round(self.start, 6),
            "in_point": round(self.in_point, 6),
            "out_point": round(self.out_point, 6),
            "gain_db": self.gain_db,
            "automation": _automation_to_dict(self.automation),
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
            automation=_automation_from_dict(data.get("automation", [])),
        )
        if clip.out_point < clip.in_point:
            raise ValueError(f"素材 {clip.id} 的 out_point 早于 in_point")
        return clip


@dataclass
class Track:
    """一条轨道：素材列表 + 静音/独听 + 推子增益/声像 + 效果器链。

    automation 使用工程绝对时间：移动素材不会影响这些断点，
    因此可以让整条轨道在指定时刻改变音量/声像/效果处理强度。
    """

    id: str
    name: str = "轨道"
    clips: list[Clip] = field(default_factory=list)
    muted: bool = False
    solo: bool = False
    gain_db: float = 0.0
    pan: float = 0.0
    # 参数化效果器节点列表，渲染时按顺序执行（见 engine._run_effect）。
    effects: list[dict[str, Any]] = field(default_factory=list)
    # 轨道级自动化：工程绝对时间（不随素材移动）。
    automation: list[AutomationPoint] = field(default_factory=list)
    # normal=普通；control=闪避控制（静音也检测）；music=被压低的背景音乐。
    ducking_role: str = "normal"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "muted": self.muted,
            "solo": self.solo,
            "gain_db": self.gain_db,
            "pan": self.pan,
            "effects": list(self.effects),
            "automation": _automation_to_dict(self.automation),
            "ducking_role": self.ducking_role,
            "clips": [c.to_dict() for c in self.clips],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Track":
        track = cls(
            id=str(data["id"]),
            name=str(data.get("name", "轨道")),
            clips=[Clip.from_dict(c) for c in data.get("clips", [])],
            muted=bool(data.get("muted", False)),
            solo=bool(data.get("solo", False)),
            gain_db=float(data.get("gain_db", 0.0)),
            pan=min(1.0, max(-1.0, float(data.get("pan", 0.0)))),
            effects=list(data.get("effects", [])),
            automation=_automation_from_dict(data.get("automation", [])),
            ducking_role=str(data.get("ducking_role", "normal")),
        )
        for effect in track.effects:
            if not isinstance(effect, dict) or "type" not in effect:
                raise ValueError("效果器节点必须是带 type 的对象")
        if track.ducking_role not in ("normal", "control", "music"):
            raise ValueError("闪避轨道角色必须是 normal、control 或 music")
        return track


DEFAULT_DUCKING_PROFILES: dict[str, dict[str, float]] = {
    "A": {
        "threshold_db": -30.0,
        "reduction_db": 12.0,
        "attack_sec": 0.08,
        "hold_sec": 0.18,
        "release_sec": 0.65,
    },
    "B": {
        "threshold_db": -24.0,
        "reduction_db": 18.0,
        "attack_sec": 0.04,
        "hold_sec": 0.30,
        "release_sec": 1.20,
    },
}


@dataclass
class Project:
    """整个混音工程。sample_rate 为统一输出采样率。"""

    name: str = "未命名工程"
    sample_rate: int = 44100
    channels: int = 2
    master_gain_db: float = 0.0
    tracks: list[Track] = field(default_factory=list)
    version: int = 1
    # none=保留原混音；A/B=使用两套闪避设置，便于保存后继续比较。
    active_ducking: str = "none"
    ducking_profiles: dict[str, dict[str, float]] = field(
        default_factory=lambda: {
            name: dict(values) for name, values in DEFAULT_DUCKING_PROFILES.items()
        }
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "audio-studio-project",
            "version": self.version,
            "name": self.name,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "master_gain_db": self.master_gain_db,
            "active_ducking": self.active_ducking,
            "ducking_profiles": self.ducking_profiles,
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
            active_ducking=str(data.get("active_ducking", "none")),
            ducking_profiles=_ducking_profiles_from_dict(data.get("ducking_profiles")),
        )
        if project.sample_rate <= 0:
            raise ValueError("工程采样率非法")
        if project.channels not in (1, 2):
            raise ValueError("目前只支持单声道/立体声输出")
        if project.active_ducking not in ("none", "A", "B"):
            raise ValueError("闪避设置必须是 none、A 或 B")
        _validate_ducking_profiles(project.ducking_profiles)
        return project

    def active_tracks(self) -> list[Track]:
        """独听/静音裁决：存在独听轨时只听独听轨；被静音的轨始终排除。"""
        any_solo = any(t.solo for t in self.tracks)
        return [
            t for t in self.tracks
            if not t.muted and (not any_solo or t.solo)
        ]

    def timeline_length(self) -> float:
        """工程时间长度由所有素材决定。

        独听/静音只改变能否听见，绝不缩短导出长度或改变其他轨时间位置，
        因此这里遍历全部轨道（含静音、未独听的轨道）。
        """
        end = 0.0
        for track in self.tracks:
            for clip in track.clips:
                end = max(end, clip.end)
        return end

    def master_gain(self) -> float:
        return _db_to_gain(self.master_gain_db)

    def ducking_profile(self) -> dict[str, float] | None:
        if self.active_ducking == "none":
            return None
        return dict(self.ducking_profiles[self.active_ducking])


def _validate_ducking_profiles(profiles: dict[str, dict[str, float]]) -> None:
    required = {"threshold_db", "reduction_db", "attack_sec", "hold_sec", "release_sec"}
    for name in ("A", "B"):
        profile = profiles.get(name)
        if not isinstance(profile, dict) or not required.issubset(profile):
            raise ValueError(f"闪避设置 {name} 参数不完整")
        for key in ("attack_sec", "hold_sec", "release_sec"):
            if float(profile[key]) < 0.0:
                raise ValueError("闪避时间参数不能为负")
        if float(profile["reduction_db"]) < 0.0:
            raise ValueError("闪低压低量不能为负")


def _ducking_profiles_from_dict(data: Any) -> dict[str, dict[str, float]]:
    profiles = {
        name: dict(values) for name, values in DEFAULT_DUCKING_PROFILES.items()
    }
    if isinstance(data, dict):
        for name in profiles:
            incoming = data.get(name)
            if isinstance(incoming, dict):
                profiles[name].update({k: float(v) for k, v in incoming.items()})
    return profiles

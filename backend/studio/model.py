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
        )
        for effect in track.effects:
            if not isinstance(effect, dict) or "type" not in effect:
                raise ValueError("效果器节点必须是带 type 的对象")
        return track


@dataclass
class Project:
    """整个混音工程。sample_rate 为统一输出采样率。"""

    name: str = "未命名工程"
    sample_rate: int = 44100
    channels: int = 2
    master_gain_db: float = 0.0
    tracks: list[Track] = field(default_factory=list)
    version: int = 1
    # 侧链闪避配置。active_preset_id="off" 时完全保留原始混音。
    ducking: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "audio-studio-project",
            "version": self.version,
            "name": self.name,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "master_gain_db": self.master_gain_db,
            "tracks": [t.to_dict() for t in self.tracks],
            "ducking": self.ducking,
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
            ducking=_ducking_from_dict(data.get("ducking")),
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

    def active_ducking_preset(self) -> dict[str, Any] | None:
        """返回当前启用的闪避参数；原始混音模式返回 None。"""
        if not self.ducking:
            return None
        active_id = self.ducking.get("active_preset_id", "off")
        if active_id == "off":
            return None
        for preset in self.ducking.get("presets", []):
            if preset.get("id") == active_id:
                return preset
        return None


def default_ducking() -> dict[str, Any]:
    return {
        "control_track_id": None,
        "target_track_ids": [],
        "active_preset_id": "off",
        "presets": [
            {
                "id": "a",
                "name": "设置 A",
                "threshold_db": -25.0,
                "depth_db": -12.0,
                "attack_sec": 0.05,
                "hold_sec": 0.15,
                "release_sec": 0.60,
            },
            {
                "id": "b",
                "name": "设置 B",
                "threshold_db": -30.0,
                "depth_db": -15.0,
                "attack_sec": 0.08,
                "hold_sec": 0.25,
                "release_sec": 1.20,
            },
        ],
    }


def _ducking_from_dict(data: Any) -> dict[str, Any] | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError("侧链闪避配置必须是对象")
    if int(data.get("version", 1)) == 2 or "plans" in data:
        return _ducking_v2_from_dict(data)
    config = default_ducking()
    config.update(data)
    control = config.get("control_track_id")
    config["control_track_id"] = None if control is None else str(control)
    targets = config.get("target_track_ids", [])
    if not isinstance(targets, list):
        raise ValueError("闪避目标轨道必须是列表")
    config["target_track_ids"] = [str(item) for item in targets]
    if config["active_preset_id"] not in ("off", "a", "b"):
        raise ValueError("未知闪避参数组")

    defaults = {item["id"]: item for item in default_ducking()["presets"]}
    normalized = []
    for item in config.get("presets", []):
        if not isinstance(item, dict) or item.get("id") not in defaults:
            continue
        preset = dict(defaults[item["id"]])
        preset.update(item)
        preset["threshold_db"] = float(preset["threshold_db"])
        preset["depth_db"] = min(-0.1, max(-60.0, float(preset["depth_db"])))
        preset["attack_sec"] = min(10.0, max(0.0, float(preset["attack_sec"])))
        preset["hold_sec"] = min(10.0, max(0.0, float(preset["hold_sec"])))
        preset["release_sec"] = min(30.0, max(0.0, float(preset["release_sec"])))
        normalized.append(preset)
    config["presets"] = normalized
    return config


# ------------------------------------------------------------------
# 多控制源（多角色）闪避方案 v2
# ------------------------------------------------------------------

_ROLE_PARAM_LIMITS = {
    "threshold_db": (-60.0, 0.0),
    "depth_db": (-60.0, -0.1),
    "attack_sec": (0.0, 10.0),
    "hold_sec": (0.0, 10.0),
    "release_sec": (0.0, 30.0),
}


def default_role(role_id: str = "host", name: str = "主持人",
                 depth_db: float = -12.0) -> dict[str, Any]:
    return {
        "id": role_id,
        "name": name,
        # 数值越大优先级越高；同强度时优先级高的角色被记为控制来源。
        "priority": 10,
        # 同一角色可以由多条控制音轨共同触发；重复 id 只算一次。
        "control_track_ids": [],
        "target_track_ids": [],
        # pre = 用该轨被闪避前的声音做控制；post = 用被其他角色压低后的声音。
        "source": "pre",
        "threshold_db": -25.0,
        "depth_db": depth_db,
        "attack_sec": 0.05,
        "hold_sec": 0.15,
        "release_sec": 0.60,
    }


def default_ducking_v2() -> dict[str, Any]:
    plan = {
        "id": "plan1",
        "name": "方案 1",
        # strongest = 同一时刻由压得更深（更强）的角色主导，交接时逐采样取更弱者；
        # multiply = 各角色增益按规则相乘（dB 深度相加）共同作用。
        "combine": "strongest",
        "roles": [
            default_role("host", "主持人", -12.0),
            default_role("guest", "远端嘉宾", -9.0),
            default_role("beep", "提示音", -15.0),
        ],
    }
    plan["roles"][1]["priority"] = 8
    plan["roles"][2]["priority"] = 6
    return {
        "version": 2,
        "active_plan_id": "plan1",
        "plans": [plan],
    }


def _normalize_role(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or not data.get("id"):
        raise ValueError("闪避角色必须是带 id 的对象")
    role_id = str(data["id"])
    role = default_role(role_id)
    allowed = set(role.keys()) | {"id", "name", "priority"}
    for key, value in data.items():
        if key in allowed:
            role[key] = value
    role["id"] = role_id
    role["name"] = str(role["name"])
    role["priority"] = int(role["priority"])
    if role["source"] not in ("pre", "post"):
        raise ValueError(f"角色 {role_id} 的控制信号来源必须是 pre(处理前) 或 post(处理后)")
    for key, value in list(role.items()):
        if key in _ROLE_PARAM_LIMITS:
            low, high = _ROLE_PARAM_LIMITS[key]
            role[key] = min(high, max(low, float(value)))
    controls = role.get("control_track_ids", [])
    targets = role.get("target_track_ids", [])
    if not isinstance(controls, list) or not isinstance(targets, list):
        raise ValueError(f"角色 {role_id} 的控制轨/受控轨必须是列表")
    # 同一角色内重复归入同一控制轨只保留一次，绝不因重复归组多压一次。
    role["control_track_ids"] = list(dict.fromkeys(str(item) for item in controls))
    role["target_track_ids"] = list(dict.fromkeys(str(item) for item in targets))
    return role


def validate_ducking_plan(plan: dict[str, Any],
                          track_ids: set[str] | None = None) -> None:
    """校验多角色方案内部关系是否成立。

    无法成立的关系直接报错，而不是悄悄只计算其中一部分：
      1. 同一控制轨在两个角色中重复出现且这两个角色压同一受控轨——
         multiply 下会被多压一次，strongest 下来源也无法唯一解释，故拒绝；
      2. 角色使用“处理后(post)”信号时，沿 post 依赖形成环（含自身
         既控制又受控的相互控制）无法解释求值顺序，拒绝；
      3. 引用不存在的轨道时拒绝。
    """
    roles = plan.get("roles", [])
    role_ids = [r["id"] for r in roles]
    if len(role_ids) != len(set(role_ids)):
        raise ValueError("闪避角色 id 重复")
    if plan.get("combine") not in ("strongest", "multiply"):
        raise ValueError("角色合成方式必须是 strongest 或 multiply")

    def known(track_id: str) -> None:
        if track_ids is not None and track_id not in track_ids:
            raise ValueError(f"闪避关系引用了不存在的轨道 {track_id}")

    # 控制轨 -> 作用到它身上的角色（该角色的目标里含此轨）。
    controllers_of: dict[str, list[str]] = {}
    control_owner: dict[str, str] = {}
    for role in roles:
        for target in role["target_track_ids"]:
            known(target)
            controllers_of.setdefault(target, []).append(role["id"])
        for control in role["control_track_ids"]:
            known(control)
            if control in control_owner:
                other = control_owner[control]
                shared = set(role["target_track_ids"]) & set(
                    next(r for r in roles if r["id"] == other)["target_track_ids"])
                if shared:
                    raise ValueError(
                        f"控制轨 {control} 同时归入角色 {other} 与 {role['id']} "
                        f"并共同压低 {sorted(shared)}，同一来源会被重复计算")
            else:
                control_owner[control] = role["id"]

    # post 依赖边：角色 A 用处理后信号听轨 t，而角色 B 压 t，则 B 必须先求值。
    by_id = {r["id"]: r for r in roles}
    deps: dict[str, set[str]] = {r["id"]: set() for r in roles}
    for rid in role_ids:
        role = by_id[rid]
        if role["source"] != "post":
            continue
        for control in role["control_track_ids"]:
            for other in controllers_of.get(control, []):
                deps[rid].add(other)
    # Kahn 拓扑排序：无法排完即存在相互控制环。
    # deps[x] = x 求值前必须先完成的角色；从没有依赖的角色开始。
    dependents: dict[str, set[str]] = {rid: set() for rid in deps}
    indegree = {rid: len(prereq) for rid, prereq in deps.items()}
    for rid, prereq in deps.items():
        for prereq_id in prereq:
            dependents.setdefault(prereq_id, set()).add(rid)
    queue = sorted(rid for rid, degree in indegree.items() if degree == 0)
    ordered: list[str] = []
    while queue:
        rid = queue.pop(0)
        ordered.append(rid)
        for other in sorted(dependents.get(rid, ())):
            indegree[other] -= 1
            if indegree[other] == 0:
                queue.append(other)
    if len(ordered) != len(deps):
        cycle = sorted(set(deps) - set(ordered))
        raise ValueError(
            "处理后(post)控制关系形成无法解释的相互控制环: "
            + " -> ".join(cycle) + "（请把环上至少一条关系改为处理前 pre）")
    # 供引擎按拓扑顺序求值。
    plan["_role_order"] = ordered


def _ducking_v2_from_dict(data: dict[str, Any]) -> dict[str, Any]:
    default = default_ducking_v2()
    plans_raw = data.get("plans")
    if not isinstance(plans_raw, list) or not plans_raw:
        raise ValueError("多角色闪避至少要有一个方案")
    plans: list[dict[str, Any]] = []
    for item in plans_raw:
        if not isinstance(item, dict) or not item.get("id"):
            raise ValueError("闪避方案必须是带 id 的对象")
        plan = {
            "id": str(item["id"]),
            "name": str(item.get("name", item["id"])),
            "combine": item.get("combine", "strongest"),
            "roles": [_normalize_role(r) for r in item.get("roles", [])],
        }
        validate_ducking_plan(plan)
        plan.pop("_role_order", None)
        plans.append(plan)
    plan_ids = [p["id"] for p in plans]
    if len(plan_ids) != len(set(plan_ids)):
        raise ValueError("闪避方案 id 重复")
    active_id = str(data.get("active_plan_id", plans[0]["id"]))
    if active_id not in plan_ids:
        raise ValueError("当前闪避方案不存在")
    config = {
        "version": 2,
        "active_plan_id": active_id,
        "plans": plans,
    }
    return config


def active_ducking_plan_v2(ducking: dict[str, Any]) -> dict[str, Any] | None:
    """返回当前启用的多角色方案；未启用/旧版配置返回 None。"""
    if not ducking or int(ducking.get("version", 1)) != 2:
        return None
    active_id = ducking.get("active_plan_id")
    for plan in ducking.get("plans", []):
        if plan.get("id") == active_id:
            return plan
    return None

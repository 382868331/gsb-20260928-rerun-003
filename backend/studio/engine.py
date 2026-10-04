"""混音引擎：轨道 -> 输出的处理顺序在这里固定下来。

对每条被听见的轨道：
    源音频 -> 按素材 in/out 裁切 -> 素材增益与素材内自动化（随素材移动）
    -> 声像 -> 叠加到轨道缓冲
轨道缓冲：
    轨道效果器链（增益/延迟等，强度可被工程绝对时间自动化）
    -> 轨道推子增益/声像/轨道自动化（留在工程时间上）
    -> 叠加到母线
母线：各轨叠加 -> 主控增益 -> 输出范围检测 -> 导出

支持只渲染 [start, end) 区间，用于分块导出；区间渲染是纯计算，
不会修改工程或素材缓存，因此重复导出不会叠加上一次的效果。

区间渲染与整段渲染的含义统一：延迟/回声类效果器在区间起点之前
的状态通过"预卷"（从起点向前多渲染一段效果尾长再裁掉）保留，
因此把整段切成若干相邻区间分别渲染再拼接，与整段渲染逐采样一致；
音量/声像自动化按工程绝对时间逐采样插值，区间边界处取值连续。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .model import (
    AutomationPoint, Clip, Project, Track, _db_to_gain, active_ducking_plan_v2,
    validate_ducking_plan,
)


SourceLoader = Callable[[str], np.ndarray]
CancelFn = Callable[[], bool]


@dataclass
class OverRange:
    start: float
    end: float
    peak: float


@dataclass
class RenderResult:
    mix: np.ndarray                 # float32, [帧, 声道]，应用主控增益后
    sample_rate: int
    channels: int
    peak_before_master: float       # 叠加后、主控推子前峰值
    peak: float                     # 最终输出峰值
    over_range: bool
    over_count: int                 # 超范围采样点数
    over_ranges: list[OverRange] = field(default_factory=list)
    ducking_ranges: list[OverRange] = field(default_factory=list)
    duration: float = 0.0
    start_time: float = 0.0
    # 内容（片段/所选区间）本身的结束时间，与效果尾音的结束时间区分开。
    content_end_time: float = 0.0   # 请求区间的结束（片段本身到这里为止）
    tail_end_time: float = 0.0      # 实际渲染结束（含自然衰减尾音）
    # 多角色闪避核对报告：每个受控轨的实际峰值、压低时段与当时的控制角色。
    ducking_report: dict | None = None


class RenderError(Exception):
    pass


def _effect_tail_seconds(effect: dict) -> float:
    """单个效果器把声音往后拖多长（秒）。增益类为 0。"""
    kind = effect.get("type")
    if kind in ("delay", "echo"):
        delay_sec = max(0.0, float(effect.get("delay_sec", 0.25)))
        feedback = min(0.9, max(0.0, float(effect.get("feedback", 0.3))))
        if delay_sec <= 0:
            return 0.0
        if feedback > 0:
            taps = int(np.ceil(np.log(max(1e-4, 1.0 - feedback + 1e-9)) /
                               np.log(max(feedback, 1e-9))))
            taps = min(max(taps, 1), 32)
        else:
            taps = 1
        return delay_sec * taps
    if kind == "gain":
        return 0.0
    raise RenderError(f"未知效果器类型: {kind!r}")


def effect_tail_seconds(project: Project) -> float:
    """整条效果器链的自然衰减尾长（各效果器尾长相加），取所有轨道的最大值。"""
    tail = 0.0
    for track in project.tracks:
        track_tail = sum(_effect_tail_seconds(e) for e in track.effects)
        tail = max(tail, track_tail)
    return tail


def _clip_segment(source: np.ndarray, clip: Clip, sr: int) -> np.ndarray:
    """按素材 in/out 取源区间（非破坏：只切视图副本）。"""
    first = int(round(clip.in_point * sr))
    last = int(round(clip.out_point * sr))
    first = max(0, min(first, source.shape[0]))
    last = max(first, min(last, source.shape[0]))
    return source[first:last]


def _envelope(
    points: Sequence[AutomationPoint],
    key: str,
    times: np.ndarray,
    default: float,
) -> np.ndarray:
    """按断点线性插值出逐采样参数；区间外保持端点值，无断点时为默认值。"""
    values = [(p.time, getattr(p, key)) for p in points if getattr(p, key) is not None]
    if not values:
        return np.full(times.shape[0], default, dtype=np.float64)
    pt = np.array([v[0] for v in values], dtype=np.float64)
    pv = np.array([v[1] for v in values], dtype=np.float64)
    return np.interp(times, pt, pv, left=pv[0], right=pv[-1]).astype(np.float64)


def _db_to_gain_array(db: np.ndarray) -> np.ndarray:
    return np.power(10.0, db / 20.0)


def _pan_gains(pan: np.ndarray | float) -> tuple[np.ndarray | float, np.ndarray | float]:
    """线性恒功率近似声像：-1 全左、0 居中、1 全右。"""
    left = np.clip(1.0 - pan, 0.0, 1.0) * (1.0 / np.sqrt(2.0))
    right = np.clip(1.0 + pan, 0.0, 1.0) * (1.0 / np.sqrt(2.0))
    return left, right


def _apply_pan(audio: np.ndarray, pan_env: np.ndarray | float) -> np.ndarray:
    """把（可能单声道的）音频按逐采样声像摆位成立体声 [帧, 2]。"""
    if audio.shape[1] == 1:
        mono = audio[:, 0]
        left_g, right_g = _pan_gains(pan_env)
        return np.stack([mono * left_g, mono * right_g], axis=1)
    # 立体声素材：按声像在两声道间平衡（不改变自身宽度感）。
    left_g, right_g = _pan_gains(pan_env)
    out = audio.copy()
    out[:, 0] *= left_g
    out[:, 1] *= right_g
    return out


def _run_effect(
    audio: np.ndarray, effect: dict, sr: int, amount_env: np.ndarray | float,
) -> np.ndarray:
    """执行单个效果器。amount_env 为逐采样处理强度（0=干声，1=全效果）。"""
    kind = effect.get("type")
    amount = float(effect.get("amount", 1.0))
    wet_env = np.clip(amount_env * amount, 0.0, 1.0)

    if kind == "gain":
        gain_db = float(effect.get("gain_db", 0.0))
        scale = np.power(10.0, gain_db * wet_env / 20.0)
        return audio * scale[:, None]

    if kind in ("delay", "echo"):
        delay_sec = max(0.0, float(effect.get("delay_sec", 0.25)))
        feedback = min(0.9, max(0.0, float(effect.get("feedback", 0.3))))
        wet_level = min(1.0, max(0.0, float(effect.get("wet", 0.4))))
        frames = audio.shape[0]
        delay = int(round(delay_sec * sr))
        # 有限抽头近似；延迟时间为 0 或超出缓冲时不加回声。
        if delay <= 0 or delay >= frames:
            return audio
        wet = np.zeros_like(audio)
        scale = 1.0
        tap_delay = delay
        taps = max(1, int(np.ceil(np.log(max(1e-4, 1.0 - feedback + 1e-9)) /
                                  np.log(max(feedback, 1e-9))))) if feedback > 0 else 1
        taps = min(taps, 32)
        for _ in range(taps):
            if tap_delay >= frames:
                break
            wet[tap_delay:] += audio[:frames - tap_delay] * scale * wet_level
            scale *= feedback
            tap_delay += delay
        if isinstance(wet_env, np.ndarray) and wet_env.ndim == 1:
            wet = wet * wet_env[:, None]
        else:
            wet = wet * float(wet_env)
        return audio + wet

    raise RenderError(f"未知效果器类型: {kind!r}")


def _over_ranges(flag: np.ndarray, sr: int, max_gap_samples: int = 64) -> tuple[list[OverRange], int]:
    """把超范围采样点聚成时间区间（短间隙合并，避免报告过于碎）。"""
    idx = np.flatnonzero(flag)
    if idx.size == 0:
        return [], 0
    ranges: list[OverRange] = []
    run_start = int(idx[0])
    prev = int(idx[0])
    for value in idx[1:]:
        value = int(value)
        if value - prev > max_gap_samples:
            ranges.append((run_start, prev))
            run_start = value
        prev = value
    ranges.append((run_start, prev))
    return [
        OverRange(start=a / sr, end=(b + 1) / sr, peak=0.0)
        for a, b in ranges
    ], int(idx.size)


def _render_track_bus(
    project: Project,
    track: Track,
    get_source: Callable[[str], np.ndarray],
    render_start_frame: int,
    render_frames: int,
    sr: int,
    pre_frames: int,
    output_frames: int,
) -> np.ndarray:
    """渲染一条轨道在指定绝对工程区间内的立体声轨道输出。"""
    track_bus = np.zeros((render_frames, 2), dtype=np.float64)
    for clip in track.clips:
        source = get_source(clip.source)
        segment = _clip_segment(source, clip, sr)
        clip_first = int(round(clip.start * sr))
        clip_last = clip_first + segment.shape[0]
        if clip_last <= render_start_frame or clip_first >= render_start_frame + render_frames:
            continue
        local_first = max(clip_first, render_start_frame) - render_start_frame
        local_last = min(clip_last, render_start_frame + render_frames) - render_start_frame
        src_first = max(clip_first, render_start_frame) - clip_first
        part = segment[src_first:src_first + (local_last - local_first)].astype(np.float64)

        frames_n = local_last - local_first
        local_times = (np.arange(frames_n) + render_start_frame + local_first) / sr
        rel_times = local_times - clip.start
        gain_env = _db_to_gain_array(_envelope(clip.automation, "gain_db", rel_times, 0.0))
        pan_env = _envelope(clip.automation, "pan", rel_times, 0.0)
        part = part * (gain_env[:, None] * _db_to_gain(clip.gain_db))
        if part.shape[1] == 1:
            part = np.repeat(part, 2, axis=1)
        if any(p.pan is not None for p in clip.automation):
            part = _apply_pan(part, pan_env)
        track_bus[local_first:local_last] += part

    abs_times = (np.arange(render_frames) + render_start_frame) / sr
    amount_env = _envelope(track.automation, "amount", abs_times, 1.0)
    for effect in track.effects:
        track_bus = _run_effect(track_bus, effect, sr, amount_env)

    track_bus = track_bus[pre_frames:pre_frames + output_frames]
    abs_times = (np.arange(output_frames) + render_start_frame + pre_frames) / sr
    track_gain_env = _db_to_gain_array(
        _envelope(track.automation, "gain_db", abs_times, track.gain_db)
    )
    track_pan_env = _envelope(track.automation, "pan", abs_times, track.pan)
    return _apply_pan(track_bus, track_pan_env) * track_gain_env[:, None]


def _ducking_gain_envelope(control: np.ndarray, preset: dict, sr: int) -> np.ndarray:
    """从控制轨电平计算攻击/保持/释放后的背景音乐增益。"""
    if control.size == 0:
        return np.ones(0, dtype=np.float64)
    level = np.max(np.abs(control), axis=1)
    threshold = _db_to_gain(float(preset["threshold_db"]))
    frames = level.shape[0]
    attack_frames = max(1, int(round(float(preset["attack_sec"]) * sr)))
    hold_frames = max(0, int(round(float(preset["hold_sec"]) * sr)))
    release_frames = max(1, int(round(float(preset["release_sec"]) * sr)))
    depth_gain = _db_to_gain(float(preset["depth_db"]))

    # trigger 用保持时间扩展；相连语音片段自然合并，短脉冲也能覆盖保持期。
    trigger = np.zeros(frames, dtype=bool)
    active_until = -1
    for i in range(frames):
        if level[i] >= threshold:
            active_until = i + hold_frames
        trigger[i] = i <= active_until

    gain = np.ones(frames, dtype=np.float64)
    current = 1.0
    for i, active in enumerate(trigger):
        if active:
            current -= (1.0 - depth_gain) / attack_frames
            current = max(current, depth_gain)
        else:
            current += (1.0 - depth_gain) / release_frames
            current = min(current, 1.0)
        gain[i] = current
    return gain


def render(
    project: Project,
    load_source: SourceLoader,
    duration: float | None = None,
    start_time: float = 0.0,
    end_time: float | None = None,
    include_tail: bool = False,
    should_cancel: CancelFn | None = None,
) -> RenderResult:
    """渲染工程（或其 [start_time, end_time) 区间），返回混合结果。

    本函数对输入工程与素材缓存都是只读的：重复调用结果相同，
    不会把上一次渲染的效果叠加进来。

    include_tail=True 时在 end_time 之后继续渲染效果器的自然衰减尾音，
    此时 content_end_time 是所选区间本身的结束，tail_end_time 是尾音
    真正衰减完的结束；两者相等表示没有尾音或未请求尾音。
    """
    sr = project.sample_rate
    channels = project.channels

    # 先确定渲染区间。默认渲染整段（长度由所有素材决定，含静音/未独听轨）。
    timeline_end = project.timeline_length()
    preset = project.active_ducking_preset()
    plan_v2 = active_ducking_plan_v2(project.ducking) if project.ducking else None
    duck_tail = 0.0
    if preset:
        duck_tail = max(0.0, float(preset.get("hold_sec", 0.0)) +
                        float(preset.get("release_sec", 0.0)))
    if plan_v2:
        for role in plan_v2["roles"]:
            duck_tail = max(duck_tail, float(role["hold_sec"]) + float(role["release_sec"]))
    if end_time is None:
        default_end = timeline_end + duck_tail if (preset or plan_v2) else timeline_end
        end_time = default_end if duration is None else start_time + duration
    start_time = max(0.0, float(start_time))
    end_time = max(start_time, float(end_time))
    content_end_time = end_time
    effect_tail = effect_tail_seconds(project)
    tail = effect_tail
    if include_tail:
        # 允许导出所选片段及其自然衰减的尾音：区间结束不等于声音结束。
        end_time = end_time + tail
    start_frame = int(round(start_time * sr))
    total_frames = int(round(end_time * sr)) - start_frame
    total_frames = max(0, total_frames)

    # 预卷：效果器（如延迟/回声）在区间起点之前就有状态。从起点向前
    # 多渲染一个尾长再裁掉，使分段渲染与整段渲染在衔接处逐采样一致，
    # 分段起点不会丢失前面的效果状态。
    pre_frames = min(start_frame, int(round(tail * sr))) if start_frame > 0 else 0
    render_start_frame = start_frame - pre_frames
    render_frames = total_frames + pre_frames
    bus = np.zeros((total_frames, channels), dtype=np.float64)

    cache: dict[str, np.ndarray] = {}

    def get_source(key: str) -> np.ndarray:
        if key not in cache:
            data = load_source(key)
            if data.ndim != 2:
                raise RenderError(f"素材 {key} 未统一到工程格式")
            cache[key] = data
        return cache[key]

    duck_envelopes: dict[str, np.ndarray] = {}
    ducking_report: dict | None = None
    if plan_v2:
        track_ids = {track.id for track in project.tracks}
        plan_copy = {"id": plan_v2["id"], "name": plan_v2["name"],
                     "combine": plan_v2["combine"],
                     "roles": [dict(role) for role in plan_v2["roles"]]}
        # 渲染前再次校验：无法成立（相互控制环/重复归组/引用缺失）的关系
        # 直接失败，绝不只悄悄计算其中一部分。
        validate_ducking_plan(plan_copy, track_ids)
        role_order = plan_copy["_role_order"]
        roles_by_id = {role["id"]: role for role in plan_copy["roles"]}

        envelope_end = max(end_time, timeline_end + duck_tail)
        envelope_frames = int(round(envelope_end * sr))

        # 每条轨道“处理前”的声音：素材 -> 效果链 -> 推子/声像，但不含任何闪避。
        # 控制轨静音时仍然按位置/裁切/增益参与电平分析（沿用既有约定）。
        raw_buses: dict[str, np.ndarray] = {}
        needed = set()
        for role in plan_copy["roles"]:
            needed.update(role["control_track_ids"])
            needed.update(role["target_track_ids"])
        for track_id in needed:
            track = next((item for item in project.tracks if item.id == track_id), None)
            if track is not None:
                raw_buses[track_id] = _render_track_bus(
                    project, track, get_source, 0, envelope_frames, sr, 0, envelope_frames,
                )

        combine = plan_copy["combine"]
        # 已作用到各受控轨上的累计增益，与每个角色自己的包络；
        # post 角色听到的是 raw * 此前累计增益，pre 角色只听 raw。
        combined: dict[str, np.ndarray] = {}
        role_envelopes: dict[str, np.ndarray] = {}
        priorities = {rid: int(roles_by_id[rid]["priority"]) for rid in role_order}
        for rid in role_order:
            role = roles_by_id[rid]
            # 同一角色的多条控制轨取电平最大值；轨 id 已去重，不会多压一次。
            levels = []
            for cid in role["control_track_ids"]:
                if cid not in raw_buses:
                    continue
                raw = raw_buses[cid]
                if role["source"] == "post" and cid in combined:
                    # 明确使用“处理后”的声音：乘上此前已作用到该轨的闪避增益。
                    raw = raw * combined[cid][:, None]
                levels.append(np.max(np.abs(raw), axis=1))
            if levels:
                control_level = np.maximum.reduce(levels)
                control_bus = control_level[:, None] * np.ones((1, channels))
            else:
                control_bus = np.zeros((envelope_frames, channels))
            envelope = _ducking_gain_envelope(control_bus, role, sr)
            role_envelopes[rid] = envelope
            for target_id in role["target_track_ids"]:
                if target_id not in raw_buses:
                    continue
                current = combined.get(target_id)
                if current is None:
                    combined[target_id] = envelope.copy()
                elif combine == "multiply":
                    # 明确的共同作用规则：线性增益相乘（dB 压低量相加）。
                    combined[target_id] = current * envelope
                else:
                    # 由较强（压得更深）的一方逐采样主导；交接因此连续。
                    combined[target_id] = np.minimum(current, envelope)
        duck_envelopes = {tid: gain for tid, gain in combined.items()}

        # 每个受控轨在每个采样点的“当前控制者”编号，用于来源分段报告。
        owner_ids: dict[str, np.ndarray] = {}
        target_roles: dict[str, list[str]] = {}
        for rid in role_order:
            for tid in roles_by_id[rid]["target_track_ids"]:
                target_roles.setdefault(tid, []).append(rid)
        for tid, rids in target_roles.items():
            rids = sorted(rids, key=lambda r: (-priorities[r], r))
            stack = np.stack([role_envelopes[rid] for rid in rids])
            # 与压低时段报告口径一致：只有真正压低（>0.01 增益差）才算在作用，
            # 避免恢复尾端的浮点残留产生空标签分段。
            active = stack < 0.99
            if combine == "multiply":
                # 共同作用：用位掩码编码所有正在作用的角色，报告里再解码。
                bit = {rid: i for i, rid in enumerate(rids)}
                codes = np.zeros(stack.shape[1], dtype=np.int64)
                for rid in rids:
                    codes += active[bit[rid]].astype(np.int64) << bit[rid]
                owner_ids[tid] = codes
            else:
                # 最强主导：逐采样取增益最小（压得最深）的角色；rids 已按
                # 优先级排序，并列时 argmin 取到优先级更高者，交接连续。
                masked = np.where(active, stack, np.inf)
                best = np.argmin(masked, axis=0)
                any_active = np.any(active, axis=0)
                owner_ids[tid] = np.where(any_active, best, -1).astype(np.int64)

        # 每个受控轨的实际峰值（应用闪避后、主控前）与来源分段。
        # 来源标签使用当前方案快照里的角色名（人类可读），而不是内部 id。
        # 名称取自正在渲染的这份方案：重命名立即生效，另存副本后各方案
        # 互不影响，已删除的角色不会出现在方案里也就不会被引用。
        role_names = {rid: str(roles_by_id[rid]["name"]) for rid in roles_by_id}
        targets_report = []
        audible_ids = {track.id for track in project.active_tracks()}
        for tid in sorted(duck_envelopes):
            if tid not in raw_buses:
                continue
            env = duck_envelopes[tid]
            seg = slice(start_frame, start_frame + total_frames)
            ducked = raw_buses[tid][seg] * env[seg, None]
            peak = float(np.max(np.abs(ducked))) if ducked.size else 0.0
            rids = sorted(target_roles.get(tid, []), key=lambda r: (-priorities[r], r))
            if combine == "multiply":
                role_lookup = {i: "+".join(role_names[rid] for bit_i, rid in enumerate(rids)
                                           if code & (1 << bit_i))
                               for i, code in enumerate(range(1 << len(rids)))}
            else:
                role_lookup = {i: role_names[rids[i]] for i in range(len(rids))}
            owner_seq = owner_ids.get(tid, np.full(total_frames, -1, dtype=np.int64))[
                start_frame:start_frame + total_frames]
            env_seg = env[start_frame:start_frame + total_frames]

            def _range_entry(run_begin: int, run_end: int, run_owner: int) -> dict:
                # 每段都带角色 id 列表与实测压低量：同名角色靠前端的 id 区分，
                # 压低量是该段内实际增益的最低点（dB），供筛选与 CSV 导出。
                if combine == "multiply":
                    role_ids = [rid for bit_i, rid in enumerate(rids)
                                if run_owner & (1 << bit_i)]
                else:
                    role_ids = [rids[run_owner]]
                seg_gain = env_seg[run_begin:run_end]
                min_gain = float(np.min(seg_gain)) if seg_gain.size else 1.0
                return {
                    "start": (run_begin + start_frame) / sr,
                    "end": (run_end + start_frame) / sr,
                    "control": role_lookup[run_owner],
                    "control_role_ids": role_ids,
                    "depth_db": 20.0 * np.log10(max(min_gain, 1e-9)),
                }

            owned_ranges = []
            active = np.flatnonzero(owner_seq >= 0)
            if active.size:
                gap = max(64, sr // 20)
                run_begin = int(active[0])
                run_owner = int(owner_seq[run_begin])
                prev = int(active[0])
                for idx in active[1:]:
                    idx = int(idx)
                    owner_val = int(owner_seq[idx])
                    if idx - prev > gap or owner_val != run_owner:
                        owned_ranges.append(_range_entry(run_begin, prev + 1, run_owner))
                        run_begin = idx
                        run_owner = owner_val
                    prev = idx
                owned_ranges.append(_range_entry(run_begin, prev + 1, run_owner))
            targets_report.append({
                "track_id": tid,
                "audible": tid in audible_ids,
                "peak": peak,
                "min_gain": float(np.min(env[seg])) if total_frames else 1.0,
                "ranges": owned_ranges,
            })
        ducking_report = {
            "plan_id": plan_copy["id"],
            "combine": combine,
            "roles": [{"id": role["id"], "name": role["name"],
                       "priority": role["priority"],
                       "depth_db": role["depth_db"],
                       "source": role["source"],
                       "control_track_ids": role["control_track_ids"],
                       "target_track_ids": role["target_track_ids"]}
                      for role in plan_copy["roles"]],
            "targets": targets_report,
        }
    elif preset:
        control_id = project.ducking.get("control_track_id") if project.ducking else None
        target_ids = set(project.ducking.get("target_track_ids", [])) if project.ducking else set()
        control_track = next((item for item in project.tracks if item.id == control_id), None)
        if control_track is not None and target_ids:
            # 控制包络始终从工程 0 点计算；区间导出只切片，不从未触发状态重启。
            envelope_end = max(end_time, timeline_end + duck_tail)
            envelope_frames = int(round(envelope_end * sr))
            control_bus = _render_track_bus(
                project, control_track, get_source, 0, envelope_frames, sr, 0, envelope_frames
            )
            full_duck_gain = _ducking_gain_envelope(control_bus, preset, sr)
            for target_id in target_ids:
                duck_envelopes[target_id] = full_duck_gain

    audible_tracks = project.active_tracks()
    for track in audible_tracks:
        if should_cancel and should_cancel():
            from .audio import ExportCancelled
            raise ExportCancelled()

        track_bus = _render_track_bus(
            project, track, get_source, render_start_frame, render_frames,
            sr, pre_frames, total_frames,
        )
        if should_cancel and should_cancel():
            from .audio import ExportCancelled
            raise ExportCancelled()
        if track.id in duck_envelopes:
            envelope = duck_envelopes[track.id]
            track_bus *= envelope[start_frame:start_frame + total_frames, None]
        if channels == 1:
            # 工程为单声道时，把摆位后的立体声等比例混合回单声道。
            bus[:, 0] += track_bus.mean(axis=1)
        else:
            bus[:, :] += track_bus

    peak_before_master = float(np.max(np.abs(bus))) if bus.size else 0.0

    # 叠加超出输出范围（满刻度 1.0）时必须明确提示：在主控推子前检测。
    over_flags = np.max(np.abs(bus), axis=1) > 1.0
    ranges, over_count = _over_ranges(over_flags, sr)
    for item in ranges:
        item.start += start_time
        item.end += start_time

    ducking_ranges: list[OverRange] = []
    if plan_v2:
        duck_flags = np.zeros(total_frames, dtype=bool)
        for envelope in duck_envelopes.values():
            duck_flags |= envelope[start_frame:start_frame + total_frames] < 0.99
        ducking_ranges, _ = _over_ranges(duck_flags, sr, max_gap_samples=max(64, sr // 20))
        for item in ducking_ranges:
            item.start += start_time
            item.end += start_time
    elif duck_envelopes:
        duck_flags = np.zeros(total_frames, dtype=bool)
        for envelope in duck_envelopes.values():
            duck_flags |= envelope[start_frame:start_frame + total_frames] < 0.999
        ducking_ranges, _ = _over_ranges(duck_flags, sr, max_gap_samples=max(64, sr // 20))
        for item in ducking_ranges:
            item.start += start_time
            item.end += start_time

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
        ducking_ranges=ducking_ranges,
        duration=total_frames / sr,
        start_time=start_frame / sr,
        content_end_time=content_end_time,
        tail_end_time=start_frame / sr + total_frames / sr,
        ducking_report=ducking_report,
    )

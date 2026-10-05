"""交错控制角色下的混音压低方案规划。

用户为角色设置可听度下限（floor_db）、优先级（priority）与允许的恢复时长
（max_recovery_sec）后，规划器根据角色包络的重叠交接区间生成若干**可比较**
的候选包络方案（deep_first / priority_first / floor_limited / recovery_fit），
每个候选都给出实际峰值、最深增益与恢复时刻，便于对照选择。

原则：
- 同一受控轨在同一时刻只由一条控制关系主导，绝不把多条关系相乘叠加
  （避免交错角色把同一音轨重复压低）；
- 环形控制（post 依赖环）与互相冲突的可听度要求只明确指出，不任意选择
  一条关系——冲突区间在所有候选里都保持当前方案不变；
- 试听候选不改动工程；批准按时间段进行，段边界做短交叉淡化保持连续，
  未批准的时间段保持当前方案逐采样不变。
"""

from __future__ import annotations

from typing import Any, Callable

import numpy as np

from .engine import (
    V2EnvelopeContext, _ducking_gain_from_level, compute_v2_envelopes,
)
from .model import CANDIDATE_IDS, Project, _db_to_gain

# 与报告口径一致：增益低于 0.99 视为“正在压低”，恢复到 0.999 视为“已恢复”。
_ACTIVE_GAIN = 0.99
_RECOVERED_GAIN = 0.999
# 批准段边界的交叉淡化时长（秒）：保证候选与原包络在边界逐采样连续。
_CROSSFADE_SEC = 0.005

CANDIDATE_NAMES = {
    "deep_first": "深度优先（交叠时压得更深的一方主导）",
    "priority_first": "优先级优先（交叠时优先级高的一方主导）",
    "floor_limited": "可听度约束（深度优先且不突破可听度下限）",
    "recovery_fit": "限时恢复（恢复时长压进角色允许值）",
}


def _active_mask(ctx: V2EnvelopeContext, target_id: str) -> dict[str, np.ndarray]:
    """每个作用到 target 的角色逐采样是否在压低。"""
    masks: dict[str, np.ndarray] = {}
    for rid, role in ctx.roles_by_id.items():
        if target_id in role["target_track_ids"] and rid in ctx.role_envelopes:
            masks[rid] = ctx.role_envelopes[rid] < _ACTIVE_GAIN
    return masks


def handover_segments(ctx: V2EnvelopeContext, target_id: str) -> list[dict[str, Any]]:
    """按“哪些角色正在压低”把受控轨时间轴切成交接区间。

    每个区间内的活动角色集合恒定；区间边界即交接点。
    """
    masks = _active_mask(ctx, target_id)
    if not masks:
        return []
    frames = ctx.envelope_frames
    rids = sorted(masks)
    stacked = np.stack([masks[rid] for rid in rids])
    codes = np.zeros(frames, dtype=np.int64)
    for i in range(len(rids)):
        codes += stacked[i].astype(np.int64) << i
    segments: list[dict[str, Any]] = []
    start = 0
    for i in range(1, frames + 1):
        if i == frames or codes[i] != codes[start]:
            code = int(codes[start])
            if code:
                active = [rids[bit] for bit in range(len(rids)) if code & (1 << bit)]
                segments.append({
                    "target_id": target_id,
                    "start": start / ctx.sr,
                    "end": i / ctx.sr,
                    "active_roles": active,
                    "overlap": len(active) > 1,
                })
            start = i
    return segments


def _role_floor_gain(role: dict) -> float | None:
    floor_db = role.get("floor_db")
    return None if floor_db is None else _db_to_gain(float(floor_db))


def find_audibility_conflicts(
    ctx: V2EnvelopeContext, segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """互相冲突的可听度要求：同一区间内某角色要求的压低深度低于
    另一角色（或自身）声明的可听度下限——无法同时满足，明确指出，
    绝不任意选择其中一条关系。"""
    conflicts: list[dict[str, Any]] = []
    for seg in segments:
        roles = [ctx.roles_by_id[rid] for rid in seg["active_roles"]]
        floors = [(r["id"], _role_floor_gain(r)) for r in roles]
        floors = [(rid, gain) for rid, gain in floors if gain is not None]
        if not floors:
            continue
        strictest_floor = max(gain for _rid, gain in floors)
        deepest = min(_db_to_gain(float(r["depth_db"])) for r in roles)
        if deepest < strictest_floor - 1e-9:
            floor_names = [ctx.roles_by_id[rid]["name"] for rid, gain in floors
                           if abs(gain - strictest_floor) < 1e-12]
            deep_names = [r["name"] for r in roles
                          if _db_to_gain(float(r["depth_db"])) < strictest_floor - 1e-9]
            conflicts.append({
                "type": "audibility",
                "target_id": seg["target_id"],
                "start": seg["start"],
                "end": seg["end"],
                "roles": list(seg["active_roles"]),
                "message": (
                    f"可听度要求互相冲突：{'、'.join(floor_names)} 要求受控轨不低于 "
                    f"{20.0 * np.log10(strictest_floor):.1f} dB，而 "
                    f"{'、'.join(deep_names)} 要求压得更深；该区间未自动选择任何一方"
                ),
            })
    return conflicts


def compute_conflicts(ctx: V2EnvelopeContext) -> list[dict[str, Any]]:
    """当前包络上下文里的全部冲突（渲染试听/批准时与规划口径一致）。"""
    segments: list[dict[str, Any]] = []
    for target_id in sorted(ctx.combined.keys()):
        segments.extend(handover_segments(ctx, target_id))
    return find_audibility_conflicts(ctx, segments)


def _conflict_mask(conflicts: list[dict[str, Any]], target_id: str,
                   frames: int, sr: int) -> np.ndarray:
    mask = np.zeros(frames, dtype=bool)
    for conflict in conflicts:
        if conflict.get("target_id") != target_id:
            continue
        a = int(round(conflict["start"] * sr))
        b = int(round(conflict["end"] * sr))
        mask[max(0, a):min(frames, b)] = True
    return mask


def _adjusted_role_envelopes(ctx: V2EnvelopeContext) -> dict[str, np.ndarray]:
    """recovery_fit 候选用：把声明了允许恢复时长的角色的恢复时间
    压进允许值，再重新求包络（其余参数不变）。"""
    envelopes: dict[str, np.ndarray] = {}
    for rid in ctx.role_order:
        role = ctx.roles_by_id[rid]
        limit = role.get("max_recovery_sec")
        if limit is None or float(role["release_sec"]) <= float(limit):
            envelopes[rid] = ctx.role_envelopes[rid]
        else:
            adjusted = dict(role)
            adjusted["release_sec"] = float(limit)
            envelopes[rid] = _ducking_gain_from_level(
                ctx.role_levels[rid], adjusted, ctx.sr)
    return envelopes


def candidate_envelopes(
    candidate_id: str,
    ctx: V2EnvelopeContext,
    base: dict[str, np.ndarray] | None = None,
    conflicts: list[dict[str, Any]] | None = None,
) -> dict[str, np.ndarray]:
    """生成某个候选方案下每个受控轨的完整增益包络。

    所有候选在同一时刻最多由一条控制关系主导（取某个角色的包络或
    其可听度钳制），绝不把多条关系相乘，因此不会重复压低。
    冲突区间（conflicts）保持 base（当前方案）包络，不任意选择关系。
    """
    if candidate_id not in CANDIDATE_IDS:
        raise ValueError(f"未知候选包络方案: {candidate_id!r}")
    frames = ctx.envelope_frames
    source_envs = (_adjusted_role_envelopes(ctx)
                   if candidate_id == "recovery_fit" else ctx.role_envelopes)
    priorities = {rid: int(ctx.roles_by_id[rid]["priority"]) for rid in ctx.role_order}
    result: dict[str, np.ndarray] = {}
    target_ids = sorted({tid for role in ctx.roles_by_id.values()
                         for tid in role["target_track_ids"] if tid in ctx.raw_buses})
    for target_id in target_ids:
        masks = _active_mask(ctx, target_id)
        env = np.ones(frames, dtype=np.float64)
        if masks:
            rids = sorted(masks)
            stack = np.stack([source_envs[rid] for rid in rids])
            active = np.stack([masks[rid] for rid in rids])
            if candidate_id == "priority_first":
                # 逐采样选出优先级最高（并列时压得更深）的活动角色。
                order = sorted(range(len(rids)),
                               key=lambda i: (-priorities[rids[i]], rids[i]))
                chosen = np.full(frames, np.inf)
                for i in order:
                    pick = active[i] & (chosen == np.inf)
                    chosen = np.where(pick, stack[i], chosen)
                env = np.where(np.isfinite(chosen), chosen, 1.0)
            else:
                # 深度优先：逐采样取活动角色中压得更深者（交接连续）。
                masked = np.where(active, stack, 1.0)
                env = np.min(masked, axis=0)
                if candidate_id == "floor_limited":
                    # 不突破任何活动角色声明的可听度下限（取最严格者）。
                    floor = np.zeros(frames, dtype=np.float64)
                    for i, rid in enumerate(rids):
                        gain = _role_floor_gain(ctx.roles_by_id[rid])
                        if gain is not None:
                            floor = np.where(active[i],
                                             np.maximum(floor, gain), floor)
                    env = np.maximum(env, floor)
        if conflicts:
            # 冲突区间不任意选择关系：保持当前方案（base）的包络。
            keep = _conflict_mask(conflicts, target_id, frames, ctx.sr)
            if np.any(keep) and base is not None and target_id in base:
                env = np.where(keep, base[target_id], env)
        result[target_id] = env
    return result


def apply_approvals(
    approvals: list[dict[str, Any]],
    active_plan_id: str,
    ctx: V2EnvelopeContext,
    base: dict[str, np.ndarray],
    conflicts: list[dict[str, Any]] | None = None,
) -> dict[str, np.ndarray]:
    """把已批准时间段的候选包络拼进当前方案包络。

    只改批准段；段边界用短交叉淡化保证逐采样连续；未批准段与
    未启用方案（plan_id 不匹配）的条目完全不影响结果。
    """
    relevant = [item for item in approvals
                if item.get("plan_id") == active_plan_id
                and item.get("target_id") in base]
    if not relevant:
        return base
    combined = {tid: env.copy() for tid, env in base.items()}
    cache: dict[str, dict[str, np.ndarray]] = {}
    for item in relevant:
        candidate = item["candidate"]
        if candidate not in cache:
            cache[candidate] = candidate_envelopes(
                candidate, ctx, base=base, conflicts=conflicts)
        candidate_env = cache[candidate].get(item["target_id"])
        if candidate_env is None:
            continue
        target_env = combined[item["target_id"]]
        start = int(round(float(item["start"]) * ctx.sr))
        end = int(round(float(item["end"]) * ctx.sr))
        start = max(0, min(start, ctx.envelope_frames))
        end = max(start, min(end, ctx.envelope_frames))
        if end <= start:
            continue
        fade = min(int(round(_CROSSFADE_SEC * ctx.sr)), (end - start) // 2)
        target_env[start:end] = candidate_env[start:end]
        if fade > 0:
            # 边界交叉淡化：从当前方案包络平滑过渡到候选包络（再过渡回），
            # 无论边界取在哪里都不会出现增益跳变。
            ramp = np.linspace(0.0, 1.0, fade, endpoint=False)
            head = slice(start, start + fade)
            tail = slice(end - fade, end)
            prev_head = base[item["target_id"]][head]
            prev_tail = base[item["target_id"]][tail]
            target_env[head] = prev_head * (1.0 - ramp) + candidate_env[head] * ramp
            target_env[tail] = candidate_env[tail] * (1.0 - ramp) + prev_tail * ramp
    return combined


def _recovery_report(env: np.ndarray, sr: int) -> list[dict[str, Any]]:
    """每段完整恢复的起止：包络从最低点持续回升到满电平的区间。

    只统计真正回升到 >=0.999 的释放段；被新一次压低打断的回升不算。
    """
    frames = env.shape[0]
    recoveries: list[dict[str, Any]] = []
    if frames < 2:
        return recoveries
    rising = np.concatenate(([False], env[1:] > env[:-1] + 1e-12))
    starts = np.flatnonzero(rising & ~np.concatenate(([False], rising[:-1])))
    for start in starts:
        end = int(start)
        while end + 1 < frames and rising[end + 1]:
            end += 1
        # 恢复终点是回升段最后一个采样；须从压低状态回到满电平。
        if env[start] >= _ACTIVE_GAIN or env[end] < _RECOVERED_GAIN:
            continue
        recoveries.append({
            "after": int(start) / sr,
            "recovered_at": (end + 1) / sr if end + 1 < frames else frames / sr,
            "duration": (end - int(start)) / sr,
        })
    return recoveries


def candidate_metrics(
    ctx: V2EnvelopeContext, env: np.ndarray, target_id: str,
) -> dict[str, Any]:
    """候选方案在某受控轨上的可比较指标：实际峰值、最深增益、恢复时刻。"""
    raw = ctx.raw_buses.get(target_id)
    ducked = raw * env[:, None] if raw is not None else env[:, None]
    peak = float(np.max(np.abs(ducked))) if ducked.size else 0.0
    min_gain = float(np.min(env)) if env.size else 1.0
    recoveries = _recovery_report(env, ctx.sr)
    # 允许恢复时长：取作用到该轨的角色里声明过的最严值。
    limits = [float(role["max_recovery_sec"])
              for role in ctx.roles_by_id.values()
              if target_id in role["target_track_ids"]
              and role.get("max_recovery_sec") is not None]
    limit = min(limits) if limits else None
    return {
        "target_id": target_id,
        "peak": peak,
        "min_gain_db": 20.0 * float(np.log10(max(min_gain, 1e-9))),
        "recoveries": recoveries,
        "max_recovery_sec": limit,
        "recovery_ok": None if limit is None else all(
            item["duration"] <= limit + 1e-6 for item in recoveries),
    }


def generate_plan(
    project: Project,
    load_source: Callable[[str], np.ndarray],
) -> dict[str, Any]:
    """根据当前启用的多角色方案生成可比较的候选包络方案。

    返回交接区间、候选方案（含逐受控轨指标）与冲突列表。
    环形控制或互相冲突的可听度要求只明确指出，不任意选择关系。
    本函数对工程与素材只读。
    """
    from .model import active_ducking_plan_v2
    plan_v2 = active_ducking_plan_v2(project.ducking) if project.ducking else None
    if plan_v2 is None:
        raise ValueError("工程没有启用的多角色闪避方案")
    sr = project.sample_rate
    duck_tail = max(
        [0.0] + [float(role["hold_sec"]) + float(role["release_sec"])
                 for role in plan_v2["roles"]])
    envelope_end = project.timeline_length() + duck_tail

    conflicts: list[dict[str, Any]] = []
    try:
        ctx = compute_v2_envelopes(project, load_source, sr, envelope_end)
    except ValueError as exc:
        # 环形控制等无法成立的关系：明确指出，不生成任何候选。
        return {
            "plan_id": plan_v2["id"],
            "segments": [],
            "candidates": [],
            "conflicts": [{"type": "ring", "message": str(exc)}],
        }

    target_ids = sorted(ctx.combined.keys())
    segments: list[dict[str, Any]] = []
    for target_id in target_ids:
        segments.extend(handover_segments(ctx, target_id))
    segments.sort(key=lambda seg: (seg["start"], seg["target_id"]))
    conflicts.extend(find_audibility_conflicts(ctx, segments))

    candidates: list[dict[str, Any]] = []
    for candidate_id in CANDIDATE_IDS:
        envs = candidate_envelopes(candidate_id, ctx, base=ctx.combined,
                                   conflicts=conflicts)
        candidates.append({
            "id": candidate_id,
            "name": CANDIDATE_NAMES[candidate_id],
            "targets": [candidate_metrics(ctx, envs[tid], tid)
                        for tid in target_ids],
        })
    return {
        "plan_id": plan_v2["id"],
        "segments": segments,
        "candidates": candidates,
        "conflicts": conflicts,
    }

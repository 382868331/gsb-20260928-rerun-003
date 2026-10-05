"""核对：交错控制角色下的混音压低方案规划。

场景：三个交错角色（主持人/嘉宾交替 + 短暂插话提示音）压同一音乐轨；
覆盖候选包络生成、试听保留原混音、分时间段批准、边界连续、
未批准段不变、冲突/环形控制明示、删除控制关系后的重算。
"""

from __future__ import annotations

import os
import sys

import numpy as np
import soundfile as sf
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from studio.engine import render
from studio.model import Clip, Project, Track, default_ducking_v2
from studio.planner import generate_plan
from studio.store import Session

SR = 44100
DURATION = 2.0


def _write(path: str, arr: np.ndarray) -> None:
    sf.write(path, arr.astype(np.float32)[:, None], SR, subtype="PCM_16")


def _interleaved_project(sess: Session, tmp_path, *, combine="strongest",
                         host_priority=6, guest_priority=10):
    """主持人/嘉宾交错并有交叠，提示音在嘉宾讲话中短暂插话。"""
    n = int(DURATION * SR)
    music = np.full(n, 0.4, dtype=np.float32)
    host = np.zeros(n, dtype=np.float32)
    guest = np.zeros(n, dtype=np.float32)
    beep = np.zeros(n, dtype=np.float32)
    host[int(0.2 * SR):int(0.5 * SR)] = 0.8    # 与嘉宾在 0.4-0.5 交叠
    host[int(1.0 * SR):int(1.3 * SR)] = 0.8
    guest[int(0.4 * SR):int(0.7 * SR)] = 0.8   # 与主持人在 0.4-0.5 交叠
    guest[int(1.2 * SR):int(1.5 * SR)] = 0.8
    beep[int(0.60 * SR):int(0.64 * SR)] = 0.9  # 短暂插话（嘉宾讲话中）
    srcs = {}
    for name, arr in [("music", music), ("host", host), ("guest", guest), ("beep", beep)]:
        path = str(tmp_path / f"{name}.wav")
        _write(path, arr)
        srcs[name] = sess.import_file(path, f"{name}.wav").relpath

    cfg = default_ducking_v2()
    plan = cfg["plans"][0]
    plan["combine"] = combine
    specs = [("host", -12.0, host_priority), ("guest", -6.0, guest_priority),
             ("beep", -15.0, 4)]
    for i, (rid, depth, prio) in enumerate(specs):
        role = plan["roles"][i]
        role.update(control_track_ids=[rid], target_track_ids=["music"],
                    threshold_db=-20.0, depth_db=depth, priority=prio,
                    attack_sec=0.005, hold_sec=0.0, release_sec=0.05)
    project = sess.project
    project.ducking = cfg
    project.tracks = [
        Track("music", "背景音乐", clips=[Clip("cm", srcs["music"], 0, 0, DURATION)]),
        Track("host", "主持人", muted=True, clips=[Clip("ch", srcs["host"], 0, 0, DURATION)]),
        Track("guest", "远端嘉宾", muted=True, clips=[Clip("cg", srcs["guest"], 0, 0, DURATION)]),
        Track("beep", "提示音", muted=True, clips=[Clip("cb", srcs["beep"], 0, 0, DURATION)]),
    ]
    return project


def _music_level(mix: np.ndarray, start: float, end: float) -> float:
    return float(np.max(np.abs(mix[int(start * SR):int(end * SR), 0])))


def _candidate(result: dict, candidate_id: str) -> dict:
    return next(c for c in result["candidates"] if c["id"] == candidate_id)


def test_planner_segments_and_candidates_no_double_ducking(tmp_path):
    """交错角色生成交接区间与可比较候选；交叠不叠加（不重复压低）。"""
    sess = Session.create(str(tmp_path / "plan"), sample_rate=SR)
    # 即使工程级合成是 multiply，候选方案也绝不把多条关系相乘。
    project = _interleaved_project(sess, tmp_path, combine="multiply")
    result = generate_plan(project, sess.load_source)

    assert result["conflicts"] == []
    assert {c["id"] for c in result["candidates"]} == {
        "deep_first", "priority_first", "floor_limited", "recovery_fit"}

    # 交接区间：主持人+嘉宾交叠段、嘉宾+提示音交叠段都被列出。
    overlaps = [seg for seg in result["segments"] if seg["overlap"]]
    assert any(set(seg["active_roles"]) == {"host", "guest"} for seg in overlaps)
    assert any(set(seg["active_roles"]) == {"guest", "beep"} for seg in overlaps)
    for seg in result["segments"]:
        assert seg["start"] < seg["end"]

    # 深度优先候选：最深增益等于最深的单条关系（-15dB），而不是 dB 相加。
    deep = _candidate(result, "deep_first")
    metrics = next(t for t in deep["targets"] if t["target_id"] == "music")
    assert metrics["min_gain_db"] == pytest.approx(-15.0, abs=0.3)
    assert metrics["peak"] > 0
    assert metrics["recoveries"]

    # 实际输出包络核对：交叠区由压得更深的主持人主导（-12dB），
    # 不是 multiply 的 -18dB。
    mix = render(project, sess.load_source, candidate_override="deep_first").mix
    assert _music_level(mix, 0.43, 0.48) == pytest.approx(
        0.4 * 10 ** (-12 / 20) / np.sqrt(2), abs=0.01)
    # 短暂插话期间压到 -15dB。
    assert _music_level(mix, 0.605, 0.635) == pytest.approx(
        0.4 * 10 ** (-15 / 20) / np.sqrt(2), abs=0.01)


def test_planner_audition_keeps_original_and_approval_is_continuous(tmp_path):
    """试听候选保留原混音；分时间段批准后边界连续、未批准段不变。"""
    sess = Session.create(str(tmp_path / "audition"), sample_rate=SR)
    # 嘉宾优先级高但压得浅：strongest 与 priority_first 在交叠区结果不同。
    project = _interleaved_project(sess, tmp_path)
    base = render(project, sess.load_source).mix

    # 试听：候选预览不同，但工程未被修改，原混音逐采样保留。
    audition = render(project, sess.load_source, candidate_override="priority_first").mix
    assert not np.array_equal(audition, base)
    again = render(project, sess.load_source).mix
    assert np.array_equal(again, base)
    # 交叠区（0.42-0.48）：基础方案由较深的主持人主导，候选由优先级高的嘉宾主导。
    assert _music_level(base, 0.42, 0.48) == pytest.approx(
        0.4 * 10 ** (-12 / 20) / np.sqrt(2), abs=0.01)
    assert _music_level(audition, 0.42, 0.48) == pytest.approx(
        0.4 * 10 ** (-6 / 20) / np.sqrt(2), abs=0.01)

    # 只批准交叠段 [0.4, 0.5] 使用候选包络。
    project.ducking["approvals"] = [{
        "plan_id": "plan1", "candidate": "priority_first",
        "target_id": "music", "start": 0.4, "end": 0.5,
    }]
    approved = render(project, sess.load_source).mix
    # 批准段内与候选一致。
    assert _music_level(approved, 0.42, 0.48) == pytest.approx(
        0.4 * 10 ** (-6 / 20) / np.sqrt(2), abs=0.01)
    # 未批准段与原混音逐采样一致（边界交叉淡化区除外）。
    outside = np.ones(approved.shape[0], dtype=bool)
    outside[int(0.39 * SR):int(0.51 * SR)] = False
    assert np.allclose(approved[outside], base[outside], atol=1e-6)
    # 边界连续：批准段两侧相邻采样没有增益跳变。
    for boundary in (0.4, 0.5):
        window = approved[int((boundary - 0.01) * SR):int((boundary + 0.01) * SR), 0]
        assert np.max(np.abs(np.diff(window))) < 0.02

    # 保存重开后批准结果逐采样复现。
    sess.save()
    reopened = Session.open(sess.root)
    assert np.allclose(render(reopened.project, reopened.load_source).mix,
                       approved, atol=1e-6)


def test_planner_recovery_fit_respects_allowed_recovery(tmp_path):
    """允许的恢复时长：recovery_fit 候选把恢复压进允许值并报告恢复时刻。"""
    sess = Session.create(str(tmp_path / "recovery"), sample_rate=SR)
    project = _interleaved_project(sess, tmp_path)
    roles = project.ducking["plans"][0]["roles"]
    for role in roles:
        role["release_sec"] = 0.5
        role["max_recovery_sec"] = 0.08
    result = generate_plan(project, sess.load_source)

    fit = _candidate(result, "recovery_fit")
    fit_metrics = next(t for t in fit["targets"] if t["target_id"] == "music")
    assert fit_metrics["max_recovery_sec"] == pytest.approx(0.08)
    assert fit_metrics["recovery_ok"]
    for item in fit_metrics["recoveries"]:
        assert item["duration"] <= 0.08 + 1e-6
        assert item["recovered_at"] is not None

    # 不限时的候选恢复明显更慢，可对照。
    deep = _candidate(result, "deep_first")
    deep_metrics = next(t for t in deep["targets"] if t["target_id"] == "music")
    assert not deep_metrics["recovery_ok"]
    assert max(item["duration"] for item in deep_metrics["recoveries"]) > 0.3

    # 实际输出核对恢复时刻：插话结束后按允许时长恢复满电平。
    mix = render(project, sess.load_source, candidate_override="recovery_fit").mix
    beep_end = 0.64
    assert _music_level(mix, beep_end + 0.15, beep_end + 0.2) == pytest.approx(
        0.4 / np.sqrt(2), abs=0.02)


def test_planner_conflicting_audibility_and_ring_are_explicit(tmp_path):
    """互相冲突的可听度要求与环形控制：明确指出，不任意选择关系。"""
    sess = Session.create(str(tmp_path / "conflict"), sample_rate=SR)
    project = _interleaved_project(sess, tmp_path)
    roles = project.ducking["plans"][0]["roles"]
    # 嘉宾要求音乐不低于 -3dB，而主持人交叠时要压到 -12dB：冲突。
    roles[1]["floor_db"] = -3.0
    result = generate_plan(project, sess.load_source)
    audibility = [c for c in result["conflicts"] if c["type"] == "audibility"]
    assert audibility
    conflict = audibility[0]
    assert conflict["target_id"] == "music"
    assert set(conflict["roles"]) == {"host", "guest"}
    assert conflict["start"] == pytest.approx(0.4, abs=0.01)

    # 冲突区间在任何候选里都保持当前方案，不任意选择一条关系。
    base = render(project, sess.load_source).mix
    for candidate_id in ("deep_first", "priority_first", "floor_limited"):
        mix = render(project, sess.load_source, candidate_override=candidate_id).mix
        seg = slice(int(0.42 * SR), int(0.48 * SR))
        assert np.allclose(mix[seg], base[seg], atol=1e-6)

    # 环形控制（post 相互控制）：规划器明确报告，不生成候选。
    roles[0].update(control_track_ids=["music"], source="post")
    roles[1].update(control_track_ids=["music"], source="post",
                    target_track_ids=["host"])
    ring = generate_plan(project, sess.load_source)
    assert ring["candidates"] == []
    assert ring["conflicts"] and ring["conflicts"][0]["type"] == "ring"
    assert "相互控制环" in ring["conflicts"][0]["message"]


def test_planner_delete_relation_recomputes_segments_and_output(tmp_path):
    """删除一个控制关系后：交接区间、包络、峰值与恢复时刻随之重算。"""
    sess = Session.create(str(tmp_path / "delete"), sample_rate=SR)
    project = _interleaved_project(sess, tmp_path)
    before = generate_plan(project, sess.load_source)
    assert any("beep" in seg["active_roles"] for seg in before["segments"])
    assert any(seg["overlap"] for seg in before["segments"])
    mix_before = render(project, sess.load_source).mix
    assert _music_level(mix_before, 0.605, 0.635) < 0.4 * 0.25  # 插话压得很深

    # 删除提示音 -> 音乐的控制关系：插话时段不再压低，区间随之消失。
    roles = project.ducking["plans"][0]["roles"]
    roles[2]["target_track_ids"] = []
    after = generate_plan(project, sess.load_source)
    assert all("beep" not in seg["active_roles"] for seg in after["segments"])
    mix_after = render(project, sess.load_source).mix
    # 插话时段只剩嘉宾在控制（-6dB），不再被提示音压到 -15dB。
    assert _music_level(mix_after, 0.605, 0.635) == pytest.approx(
        0.4 * 10 ** (-6 / 20) / np.sqrt(2), abs=0.01)

    # 再删除嘉宾 -> 音乐的关系：交叠段消失，主持人单独控制。
    roles[1]["target_track_ids"] = []
    solo = generate_plan(project, sess.load_source)
    assert all(not seg["overlap"] for seg in solo["segments"])
    assert {rid for seg in solo["segments"] for rid in seg["active_roles"]} == {"host"}
    mix_solo = render(project, sess.load_source).mix
    assert _music_level(mix_solo, 0.605, 0.635) == pytest.approx(
        0.4 / np.sqrt(2), abs=0.01)  # 无人控制，音乐恢复满电平


def test_approvals_roundtrip_and_validation(tmp_path):
    """批准时间段随工程保存/重开；非法候选与非法区间直接报错。"""
    sess = Session.create(str(tmp_path / "roundtrip"), sample_rate=SR)
    project = _interleaved_project(sess, tmp_path)
    project.ducking["approvals"] = [{
        "plan_id": "plan1", "candidate": "deep_first",
        "target_id": "music", "start": 0.2, "end": 0.4,
    }]
    restored = Project.from_dict(project.to_dict())
    assert restored.ducking["approvals"][0]["candidate"] == "deep_first"

    bad = project.to_dict()
    bad["ducking"]["approvals"] = [{
        "plan_id": "plan1", "candidate": "nope",
        "target_id": "music", "start": 0.0, "end": 1.0,
    }]
    with pytest.raises(ValueError, match="未知候选"):
        Project.from_dict(bad)

    bad2 = project.to_dict()
    bad2["ducking"]["approvals"] = [{
        "plan_id": "plan1", "candidate": "deep_first",
        "target_id": "music", "start": 0.5, "end": 0.4,
    }]
    with pytest.raises(ValueError, match="起止时间非法"):
        Project.from_dict(bad2)

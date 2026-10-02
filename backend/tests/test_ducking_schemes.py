"""多控制源闪避编排核对：交叠讲话、紧接短提示音、静音控制轨、角色合成、
链式取数、相互控制环、方案快照隔离、区间导出与整段一致、保存重开一致。"""

from __future__ import annotations

import os
import sys

import numpy as np
import soundfile as sf
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from studio.engine import RenderError, analyze_ducking, render
from studio.model import Clip, Track, default_ducking, default_role, default_scheme
from studio.store import Session

SR = 44100
FULL = 0.4 / np.sqrt(2.0)  # 音乐 0.4 居中摆位后每声道电平


def _burst(spans, total=1.2, level=0.8):
    values = np.zeros(int(total * SR), dtype=np.float32)
    for start, end in spans:
        values[int(start * SR):int(end * SR)] = level
    return values


def _make_session(tmp_path, name, sources):
    sess = Session.create(str(tmp_path / name), sample_rate=SR)
    keys = {}
    for key, values in sources.items():
        path = tmp_path / f"{key}.wav"
        sf.write(str(path), values.astype(np.float32)[:, None], SR, subtype="PCM_16")
        keys[key] = sess.import_file(str(path), f"{key}.wav").relpath
    return sess, keys


def _role(role_id, controls, targets, depth, priority=0, tap="pre", name=None):
    role = default_role(role_id, name or role_id)
    role.update(
        control_track_ids=list(controls),
        target_track_ids=list(targets),
        depth_db=depth,
        priority=priority,
        tap=tap,
    )
    return role


def _scheme(scheme_id, roles, mode="strongest"):
    scheme = default_scheme(scheme_id, scheme_id)
    scheme.update(
        combine_mode=mode,
        threshold_db=-20.0,
        attack_sec=0.01,
        hold_sec=0.05,
        release_sec=0.10,
        handover_sec=0.02,
        roles=roles,
    )
    return scheme


def _project(sess, keys, roles, mode="strongest", extra_tracks=None, scheme_id="s1"):
    project = sess.project
    config = default_ducking()
    config["schemes"] = [_scheme(scheme_id, roles, mode)]
    config["active_scheme_id"] = scheme_id
    project.ducking = config
    tracks = [
        Track("music", "音乐", clips=[Clip("m", keys["music"], 0.0, 0.0, 1.2)]),
        Track("host", "主持人", muted=True, clips=[Clip("h", keys["host"], 0.0, 0.0, 1.2)]),
        Track("guest", "嘉宾", muted=True, clips=[Clip("g", keys["guest"], 0.0, 0.0, 1.2)]),
    ]
    tracks.extend(extra_tracks or [])
    project.tracks = tracks
    return project


def _overlap_sources():
    return {
        "music": np.full(int(1.2 * SR), 0.4, dtype=np.float32),
        "host": _burst([(0.1, 0.5)]),
        "guest": _burst([(0.3, 0.7)]),
    }


def _level(mix, start, end):
    return float(np.max(np.abs(mix[int(start * SR):int(end * SR), 0])))


def test_strongest_mode_overlap_and_continuous_handover(tmp_path):
    sess, keys = _make_session(tmp_path, "overlap", _overlap_sources())
    roles = [
        _role("r-host", ["host"], ["music"], -6.0, priority=5, name="主持人"),
        _role("r-guest", ["guest"], ["music"], -18.0, priority=1, name="嘉宾"),
    ]
    project = _project(sess, keys, roles, mode="strongest")
    result = render(project, sess.load_source)

    # 只有主持人时约 -6 dB；交叠时较强的嘉宾（-18 dB）主导。
    assert _level(result.mix, 0.15, 0.25) == pytest.approx(FULL * 10 ** (-6 / 20), abs=0.01)
    assert _level(result.mix, 0.35, 0.45) == pytest.approx(FULL * 10 ** (-18 / 20), abs=0.005)
    # 主持人 0.5s 停下后嘉宾仍在讲话：不能回升，也不能被忽略。
    assert _level(result.mix, 0.55, 0.65) == pytest.approx(FULL * 10 ** (-18 / 20), abs=0.005)
    # 交接全过程逐采样连续（无跳变）。
    diffs = np.abs(np.diff(result.mix[:, 0]))
    assert np.max(diffs) < FULL * 0.02
    # 当前由谁控制：先主持人，交叠后嘉宾接管。
    controllers = [(seg.role_name, seg.start, seg.end) for seg in result.ducking_segments]
    assert controllers[0][0] == "主持人"
    assert controllers[0][1] == pytest.approx(0.1, abs=2e-3)
    assert controllers[-1][0] == "嘉宾"
    assert controllers[-1][2] == pytest.approx(0.85, abs=0.05)


def test_same_source_grouped_twice_does_not_double_duck(tmp_path):
    sess, keys = _make_session(tmp_path, "dedupe", _overlap_sources())
    once = _project(sess, keys, [_role("r", ["host"], ["music"], -12.0)])
    twice = _project(sess, keys, [_role("r", ["host", "host"], ["music"], -12.0)])
    first = render(once, sess.load_source).mix
    second = render(twice, sess.load_source).mix
    assert np.allclose(first, second, atol=0.0)


def test_tight_short_cues_from_silent_control_track(tmp_path):
    sources = {
        "music": np.full(int(1.2 * SR), 0.4, dtype=np.float32),
        "host": _burst([(0.2, 0.22), (0.24, 0.26), (0.28, 0.30)]),  # 紧接的短提示音
        "guest": np.zeros(int(1.2 * SR), dtype=np.float32),
    }
    sess, keys = _make_session(tmp_path, "cues", sources)
    roles = [_role("r-cue", ["host"], ["music"], -12.0, name="提示音")]
    project = _project(sess, keys, roles)
    result = render(project, sess.load_source)

    # 三个紧接短脉冲在保持期内连成一段，中间不掉回原始电平。
    assert _level(result.mix, 0.21, 0.34) < FULL * 0.35
    assert result.ducking_ranges
    first = result.ducking_ranges[0]
    assert first.start == pytest.approx(0.2, abs=2e-3)
    assert first.end == pytest.approx(0.30 + 0.05 + 0.10, abs=0.05)
    # 恢复完成后最终峰值回到原始电平。
    assert result.peak == pytest.approx(FULL, abs=1e-3)


def test_multiply_mode_combines_roles(tmp_path):
    sess, keys = _make_session(tmp_path, "multiply", _overlap_sources())
    roles = [
        _role("r-host", ["host"], ["music"], -6.0),
        _role("r-guest", ["guest"], ["music"], -6.0),
    ]
    project = _project(sess, keys, roles, mode="multiply")
    result = render(project, sess.load_source)
    # 交叠区两个 -6 dB 共同作用 = -12 dB。
    assert _level(result.mix, 0.35, 0.45) == pytest.approx(FULL * 10 ** (-12 / 20), abs=0.01)
    assert _level(result.mix, 0.15, 0.25) == pytest.approx(FULL * 10 ** (-6 / 20), abs=0.01)


def test_priority_mode_dominates_and_hands_over_continuously(tmp_path):
    sources = {
        "music": np.full(int(1.2 * SR), 0.4, dtype=np.float32),
        "host": _burst([(0.1, 0.6)]),
        "guest": _burst([(0.3, 0.8)]),
    }
    sess, keys = _make_session(tmp_path, "priority", sources)
    roles = [
        _role("r-host", ["host"], ["music"], -6.0, priority=5, name="主持人"),
        _role("r-guest", ["guest"], ["music"], -24.0, priority=1, name="嘉宾"),
    ]
    project = _project(sess, keys, roles, mode="priority")
    result = render(project, sess.load_source)

    # 交叠期优先级高的主持人主导，尽管嘉宾压得更深。
    assert _level(result.mix, 0.35, 0.45) == pytest.approx(FULL * 10 ** (-6 / 20), abs=0.01)
    # 主持人停下后嘉宾仍在讲话：平滑交给嘉宾的深度，不回升。
    assert _level(result.mix, 0.68, 0.75) == pytest.approx(FULL * 10 ** (-24 / 20), abs=0.005)
    diffs = np.abs(np.diff(result.mix[:, 0]))
    assert np.max(diffs) < FULL * 0.02
    names = [seg.role_name for seg in result.ducking_segments]
    assert names == ["主持人", "嘉宾"]


def _chained_session(tmp_path, name):
    sources = {
        "music": np.full(int(1.2 * SR), 0.4, dtype=np.float32),
        "host": _burst([(0.1, 0.9)]),
        "guest": np.zeros(int(1.2 * SR), dtype=np.float32),
        "bed": np.full(int(1.2 * SR), 0.4, dtype=np.float32),
    }
    return _make_session(tmp_path, name, sources)


def _chained_project(sess, keys, tap):
    roles = [
        _role("r-host", ["host"], ["music"], -24.0, name="主持人"),
        _role("r-chain", ["music"], ["bed"], -12.0, tap=tap, name="二级"),
    ]
    return _project(sess, keys, roles, extra_tracks=[
        Track("bed", "垫底", clips=[Clip("b", keys["bed"], 0.0, 0.0, 1.2)]),
    ])


def test_post_tap_reads_ducked_signal_and_pre_tap_reads_original(tmp_path):
    sess, keys = _chained_session(tmp_path, "chain")
    # 处理后取数：音乐已被压到阈值以下，二级角色不触发，垫底轨保持原样。
    post = render(_chained_project(sess, keys, "post"), sess.load_source)
    assert _level(post.mix, 0.3, 0.5) == pytest.approx(
        FULL + FULL * 10 ** (-24 / 20), abs=0.01)

    # 处理前取数：二级角色听到原始音乐电平，正常触发并压低垫底轨。
    pre = render(_chained_project(sess, keys, "pre"), sess.load_source)
    assert _level(pre.mix, 0.3, 0.5) < _level(post.mix, 0.3, 0.5) * 0.8

    # 确认前可看到取数点与依赖关系。
    report = analyze_ducking(_chained_project(sess, keys, "post"))
    scheme = report["schemes"][0]
    assert scheme["ok"]
    chain = next(r for r in scheme["roles"] if r["id"] == "r-chain")
    assert chain["tap_label"].startswith("处理后")
    assert [d["name"] for d in chain["depends_on"]] == ["主持人"]


def test_circular_control_is_rejected_not_partially_computed(tmp_path):
    sess, keys = _chained_session(tmp_path, "cycle")
    roles = [
        _role("r-a", ["music"], ["bed"], -12.0, tap="post", name="甲"),
        _role("r-b", ["bed"], ["music"], -12.0, tap="post", name="乙"),
    ]
    project = _project(sess, keys, roles, extra_tracks=[
        Track("bed", "垫底", clips=[Clip("b", keys["bed"], 0.0, 0.0, 1.2)]),
    ])
    with pytest.raises(RenderError, match="相互控制"):
        render(project, sess.load_source)
    report = analyze_ducking(project)
    scheme = report["schemes"][0]
    assert not scheme["ok"]
    assert set(scheme["cycle"]) == {"甲", "乙"}


def test_self_referential_post_tap_is_rejected(tmp_path):
    sess, keys = _chained_session(tmp_path, "self-cycle")
    # 处理后取数又压低自己的控制轨：无法解释，整体拒绝。
    roles = [_role("r-self", ["music"], ["music"], -12.0, tap="post", name="自引")]
    project = _project(sess, keys, roles)
    with pytest.raises(RenderError, match="相互控制"):
        render(project, sess.load_source)
    report = analyze_ducking(project)
    assert not report["schemes"][0]["ok"]


def test_slice_export_across_handover_matches_full_render(tmp_path):
    sess, keys = _make_session(tmp_path, "slice", _overlap_sources())
    roles = [
        _role("r-host", ["host"], ["music"], -6.0, priority=5),
        _role("r-guest", ["guest"], ["music"], -18.0, priority=1),
    ]
    project = _project(sess, keys, roles, mode="priority")
    full = render(project, sess.load_source).mix
    # 区间横跨 0.3s（嘉宾进入）与 0.5s（主持人停顿）两个交接点。
    part = render(project, sess.load_source, start_time=0.25, end_time=0.65).mix
    expected = full[int(0.25 * SR):int(0.65 * SR)]
    assert part.shape == expected.shape
    assert np.allclose(part, expected, atol=1e-6)


def test_schemes_are_isolated_snapshots_and_survive_reopen(tmp_path):
    sess, keys = _make_session(tmp_path, "snapshot", _overlap_sources())
    project = _project(sess, keys, [_role("r-host", ["host"], ["music"], -9.0)],
                       scheme_id="s1")
    project.ducking["schemes"].append(
        _scheme("s2", [_role("r-guest", ["guest"], ["music"], -18.0)])
    )
    before = render(project, sess.load_source).mix

    # 修改另一方案的角色与关系，已保存方案的声音不变。
    project.ducking["schemes"][1]["roles"][0]["depth_db"] = -3.0
    project.ducking["schemes"][1]["roles"].append(
        _role("r-extra", ["host", "guest"], ["music"], -30.0, tap="post")
    )
    sess.save()
    reopened = Session.open(sess.root)
    reopened.project.ducking["active_scheme_id"] = "s1"
    after = render(reopened.project, reopened.load_source).mix
    assert np.allclose(before, after, atol=1e-6)

    # 重开后继续编辑关系并重复渲染，结果逐采样一致。
    again = render(reopened.project, reopened.load_source).mix
    assert np.allclose(after, again, atol=0.0)
    reopened.project.ducking["active_scheme_id"] = "s2"
    variant = render(reopened.project, reopened.load_source).mix
    assert not np.allclose(variant, after, atol=1e-3)

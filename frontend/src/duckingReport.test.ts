import { describe, expect, it } from "vitest";
import type { DuckingReport } from "./types";
import {
  type DuckingSnapshot, emptyFilters, flattenRanges, filterRanges,
  roleOptions, roleLabel, deletedRoles, rangesToCsv,
} from "./duckingReport";

function range(start: number, end: number, roleIds: string[], name: string,
  depth: number) {
  return { start, end, control: name, control_role_ids: roleIds, max_reduction_db: depth };
}

function makeReport(): DuckingReport {
  return {
    plan_id: "p1",
    combine: "strongest",
    roles: [
      { id: "host", name: "主持人", priority: 10, depth_db: -12, source: "pre",
        control_track_ids: ["h"], target_track_ids: ["music", "bed"] },
      { id: "guest", name: "远端嘉宾", priority: 8, depth_db: -6, source: "pre",
        control_track_ids: ["g"], target_track_ids: ["music"] },
    ],
    targets: [
      {
        track_id: "music", track_name: "背景音乐", audible: false,
        peak: 0.1, min_gain: 0.25,
        ranges: [
          range(0.10, 0.40, ["host"], "主持人", -12.0),
          range(0.40, 0.50, ["host", "guest"], "主持人+远端嘉宾", -12.0),
          range(0.50, 0.90, ["guest"], "远端嘉宾", -6.0),
          range(2.00, 2.30, ["host"], "主持人", -11.5),
        ],
      },
      {
        track_id: "bed", track_name: "床声", audible: true,
        peak: 0.2, min_gain: 0.25,
        ranges: [range(1.00, 1.20, ["host"], "主持人", -12.0)],
      },
    ],
  };
}

function snapshot(report = makeReport()): DuckingSnapshot {
  return {
    id: "p1-now", detected_at: "2026-10-05 10:00", plan_id: "p1",
    plan_name: "方案 1", combine: "strongest", report,
  };
}

describe("flattenRanges", () => {
  it("跨受控轨打平并按时间排序，且不修改输入快照", () => {
    const snap = snapshot();
    const before = JSON.stringify(snap);
    const rows = flattenRanges(snap);
    expect(rows.map((r) => r.start)).toEqual([0.1, 0.4, 0.5, 1.0, 2.0]);
    expect(rows[0].trackName).toBe("背景音乐");
    expect(rows[0].audible).toBe(false);
    expect(JSON.stringify(snap)).toBe(before);
  });
});

describe("filterRanges", () => {
  const rows = () => flattenRanges(snapshot());

  it("不过滤时返回全部区间（超过首屏数量）", () => {
    expect(filterRanges(rows(), emptyFilters)).toHaveLength(5);
  });

  it("按控制角色筛选，共同作用区间对双方都命中", () => {
    expect(filterRanges(rows(), { ...emptyFilters, roleId: "guest" })
      .map((r) => r.start)).toEqual([0.4, 0.5]);
    expect(filterRanges(rows(), { ...emptyFilters, roleId: "host" })
      .map((r) => r.start)).toEqual([0.1, 0.4, 1.0, 2.0]);
  });

  it("按受控音轨筛选", () => {
    expect(filterRanges(rows(), { ...emptyFilters, targetTrackId: "bed" })
      .map((r) => r.start)).toEqual([1.0]);
  });

  it("按时间范围筛选（相交即命中，支持单边）", () => {
    expect(filterRanges(rows(), { ...emptyFilters, startTime: "0.45", endTime: "1.5" })
      .map((r) => r.start)).toEqual([0.4, 0.5, 1.0]);
    expect(filterRanges(rows(), { ...emptyFilters, startTime: "1.0" })
      .map((r) => r.start)).toEqual([1.0, 2.0]);
    expect(filterRanges(rows(), { ...emptyFilters, endTime: "0.45" })
      .map((r) => r.start)).toEqual([0.1, 0.4]);
  });

  it("组合筛选", () => {
    const got = filterRanges(rows(), {
      roleId: "host", targetTrackId: "music", startTime: "0.4", endTime: "1.5",
    });
    expect(got.map((r) => r.start)).toEqual([0.4]);
  });
});

describe("同名与已删除角色", () => {
  it("同名角色在选项中标记 duplicate 并附加 id 消歧", () => {
    const report = makeReport();
    report.roles[1].name = "主持人";
    const options = roleOptions(snapshot(report));
    expect(options.every((o) => o.duplicate)).toBe(true);
    const labels = options.map(roleLabel);
    expect(labels).toContain("主持人（host）");
    expect(labels).toContain("主持人（guest）");
  });

  it("删除角色后的旧快照仍可按当时区间筛选到该角色", () => {
    const report = makeReport();
    // 旧快照：beep 角色已不在角色表，但 music 上仍有它的交接区间。
    report.targets[0].ranges.push(range(2.6, 2.7, ["beep"], "提示音", -15.0));
    const snap = snapshot(report);
    expect(deletedRoles(snap).map((r) => r.id)).toEqual(["beep"]);
    const rows = flattenRanges(snap);
    expect(filterRanges(rows, { ...emptyFilters, roleId: "beep" })).toHaveLength(1);
  });
});

describe("rangesToCsv", () => {
  it("包含起止时间、可读名称与压低量，并正确转义", () => {
    const report = makeReport();
    report.targets[0].track_name = "背景,音乐\"新";
    const csv = rangesToCsv(filterRanges(flattenRanges(snapshot(report)),
      { ...emptyFilters, roleId: "host", targetTrackId: "music" }));
    expect(csv.startsWith("\ufeff")).toBe(true);
    const lines = csv.replace("\ufeff", "").split("\r\n").filter(Boolean);
    expect(lines[0]).toBe(
      "start_sec,end_time,controlled_track,control_role,reduction_db,audible");
    expect(lines).toHaveLength(4);
    expect(lines[1]).toBe('0.100,0.400,"背景,音乐\"\"新",主持人,-12.00,no');
    expect(lines[2]).toContain("0.400,0.500");
    expect(lines[2]).toContain("主持人+远端嘉宾");
  });

  it("改名后用新名称导出，旧快照保持旧名称", () => {
    const oldSnap = snapshot();
    const newReport = JSON.parse(JSON.stringify(makeReport())) as DuckingReport;
    newReport.roles[0].name = "主播";
    newReport.targets[0].track_name = "片花音乐";
    for (const target of newReport.targets) {
      for (const r of target.ranges) {
        if (r.control) r.control = r.control.replace("主持人", "主播");
      }
    }
    const newSnap = snapshot(newReport);
    expect(rangesToCsv(flattenRanges(oldSnap))).toContain("背景音乐");
    expect(rangesToCsv(flattenRanges(oldSnap))).toContain("主持人");
    expect(rangesToCsv(flattenRanges(newSnap))).toContain("片花音乐");
    expect(rangesToCsv(flattenRanges(newSnap))).toContain("主播");
  });
});

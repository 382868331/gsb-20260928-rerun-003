import type { DuckingReport, DuckingHandoverRange } from "./types";

/** 一份“检测混音”结果快照：名称定格在检测时刻，之后改名/删除角色不回改旧快照。 */
export interface DuckingSnapshot {
  id: string;
  detected_at: string;       // 检测时刻（本地可读时间）
  plan_id: string;
  plan_name: string;
  combine: "strongest" | "multiply";
  report: DuckingReport;
}

export interface RangeFilters {
  roleId: string;            // "" = 全部控制角色
  targetTrackId: string;     // "" = 全部受控音轨
  startTime: string;         // "" = 不限
  endTime: string;           // "" = 不限
}

export const emptyFilters: RangeFilters = {
  roleId: "",
  targetTrackId: "",
  startTime: "",
  endTime: "",
};

/** 跨目标打平后的一行交接区间，附检测时刻定格的名称。 */
export interface FlatRange {
  trackId: string;
  trackName: string;
  start: number;
  end: number;
  control: string;
  controlRoleIds: string[];
  maxReductionDb: number | null;
  audible: boolean;
}

export function flattenRanges(snapshot: DuckingSnapshot): FlatRange[] {
  const rows: FlatRange[] = [];
  for (const target of snapshot.report.targets) {
    for (const range of target.ranges) {
      rows.push({
        trackId: target.track_id,
        trackName: target.track_name ?? target.track_id,
        start: range.start,
        end: range.end,
        control: range.control ?? "—",
        controlRoleIds: range.control_role_ids ?? [],
        maxReductionDb: range.max_reduction_db ?? null,
        audible: target.audible,
      });
    }
  }
  rows.sort((a, b) => a.start - b.start || a.trackId.localeCompare(b.trackId));
  return rows;
}

/** 时间区间相交：[aStart,aEnd) 与 [bStart,bEnd) 有重叠才算命中。 */
function overlaps(start: number, end: number, lo: number, hi: number): boolean {
  return end > lo && start < hi;
}

/**
 * 按控制角色、受控音轨与时间范围筛选交接区间。
 * 纯函数：不修改快照，也绝不触碰工程与音频数据。
 */
export function filterRanges(rows: FlatRange[], filters: RangeFilters): FlatRange[] {
  const lo = filters.startTime.trim() === "" ? null : Number(filters.startTime);
  const hi = filters.endTime.trim() === "" ? null : Number(filters.endTime);
  return rows.filter((row) => {
    if (filters.roleId && !row.controlRoleIds.includes(filters.roleId)) return false;
    if (filters.targetTrackId && row.trackId !== filters.targetTrackId) return false;
    if (lo !== null && !Number.isNaN(lo) && hi !== null && !Number.isNaN(hi)) {
      if (!overlaps(row.start, row.end, lo, hi)) return false;
    } else if (lo !== null && !Number.isNaN(lo)) {
      if (row.end <= lo) return false;
    } else if (hi !== null && !Number.isNaN(hi)) {
      if (row.start >= hi) return false;
    }
    return true;
  });
}

export interface RoleOption {
  id: string;
  name: string;
  duplicate: boolean;
}

/** 快照里的全部控制角色（定格名称）；同名角色标记 duplicate 供界面加 id 区分。 */
export function roleOptions(snapshot: DuckingSnapshot): RoleOption[] {
  const counts = new Map<string, number>();
  for (const role of snapshot.report.roles) {
    counts.set(role.name, (counts.get(role.name) ?? 0) + 1);
  }
  return snapshot.report.roles.map((role) => ({
    id: role.id,
    name: role.name,
    duplicate: (counts.get(role.name) ?? 0) > 1,
  }));
}

export function roleLabel(option: RoleOption): string {
  return option.duplicate ? `${option.name}（${option.id}）` : option.name;
}

/** 已删除角色：出现在交接区间里、但已不在快照角色表中（旧快照仍保留当时名称）。 */
export function deletedRoles(snapshot: DuckingSnapshot): RoleOption[] {
  const known = new Map(snapshot.report.roles.map((role) => [role.id, role.name]));
  const found = new Map<string, string>();
  for (const row of flattenRanges(snapshot)) {
    for (const id of row.controlRoleIds) {
      if (!known.has(id)) found.set(id, id);
    }
  }
  return [...found].map(([id]) => ({ id, name: id, duplicate: false }));
}

function csvCell(value: string | number): string {
  const text = String(value);
  if (/[",\n\r]/.test(text)) {
    return `"${text.replace(/"/g, '""')}"`;
  }
  return text;
}

/**
 * 导出交接区间 CSV：起止时间、可读角色名/受控轨名与压低量。
 * 加 UTF-8 BOM，便于 Excel 直接正确显示中文。
 */
export function rangesToCsv(rows: FlatRange[]): string {
  const header = [
    "start_sec", "end_time", "controlled_track", "control_role",
    "reduction_db", "audible",
  ];
  const lines = [header.map(csvCell).join(",")];
  for (const row of rows) {
    lines.push([
      row.start.toFixed(3),
      row.end.toFixed(3),
      row.trackName,
      row.control,
      row.maxReductionDb === null ? "" : row.maxReductionDb.toFixed(2),
      row.audible ? "yes" : "no",
    ].map(csvCell).join(","));
  }
  return "\ufeff" + lines.join("\r\n") + "\r\n";
}

export function downloadCsv(filename: string, content: string): void {
  const blob = new Blob([content], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/** 区间上的压低量（旧快照后端未返回该字段时为 null，界面显示 —）。 */
export function reductionText(range: DuckingHandoverRange): string {
  return range.max_reduction_db === undefined ? "—" : `${range.max_reduction_db.toFixed(1)} dB`;
}

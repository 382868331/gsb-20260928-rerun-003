import type { DuckingConfig, DuckingConfigV2, MediaInfo, PreviewReport, ProjectData } from "./types";

async function request(path: string, options?: RequestInit): Promise<unknown> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw Object.assign(new Error(body.error ?? `请求失败 ${response.status}`), { body, status: response.status });
  }
  return body;
}

export const api = {
  loadProject: () => request("/api/project") as Promise<ProjectData>,
  saveProject: (project: ProjectData) =>
    request("/api/project", { method: "POST", body: JSON.stringify(project) }),
  persist: () => request("/api/project/save", { method: "POST" }),
  importMedia: (path: string, name?: string) =>
    request("/api/media/import", {
      method: "POST",
      body: JSON.stringify({ path, name }),
    }) as Promise<MediaInfo>,
  mediaInfo: (source: string) =>
    request(`/api/media/info?source=${encodeURIComponent(source)}`) as Promise<{
      source: string; sample_rate: number; channels: number; frames: number; duration: number;
    }>,

  preview: (project: ProjectData) =>
    request("/api/render/preview", {
      method: "POST",
      body: JSON.stringify(project),
    }) as Promise<PreviewReport>,
  startExport: (outputPath: string, allowOverRange: boolean,
    range?: { start_time?: number; end_time?: number; include_tail?: boolean }) =>
    request("/api/export", {
      method: "POST",
      body: JSON.stringify({ output_path: outputPath, allow_over_range: allowOverRange, ...(range ?? {}) }),
    }) as Promise<{ job_id: string; output_path: string }>,
  exportStatus: (jobId: string) =>
    request(`/api/export/${jobId}`) as Promise<ExportStatus>,
  cancelExport: (jobId: string) =>
    request(`/api/export/${jobId}/cancel`, { method: "POST" }),
};

export interface ExportStatus {
  id: string;
  status: "running" | "done" | "cancelled" | "error";
  progress: number;
  message: string;
  peak: number;
  peak_before_master: number;
  over_range: boolean;
  over_count: number;
  over_ranges: { start: number; end: number }[];
  ducking_ranges?: { start: number; end: number }[];
  output_path: string | null;
}

export function defaultDucking(): DuckingConfig {
  return {
    control_track_id: null,
    target_track_ids: [],
    active_preset_id: "off",
    presets: [
      { id: "a", name: "设置 A", threshold_db: -25, depth_db: -12, attack_sec: 0.05, hold_sec: 0.15, release_sec: 0.6 },
      { id: "b", name: "设置 B", threshold_db: -30, depth_db: -15, attack_sec: 0.08, hold_sec: 0.25, release_sec: 1.2 },
    ],
  };
}

export function defaultDuckingV2(): DuckingConfigV2 {
  return {
    version: 2,
    active_plan_id: "plan1",
    plans: [{
      id: "plan1",
      name: "方案 1",
      combine: "strongest",
      roles: [
        { id: "host", name: "主持人", priority: 10, control_track_ids: [], target_track_ids: [],
          source: "pre", threshold_db: -25, depth_db: -12, attack_sec: 0.05, hold_sec: 0.15, release_sec: 0.6 },
        { id: "guest", name: "远端嘉宾", priority: 8, control_track_ids: [], target_track_ids: [],
          source: "pre", threshold_db: -25, depth_db: -9, attack_sec: 0.05, hold_sec: 0.15, release_sec: 0.6 },
        { id: "beep", name: "提示音", priority: 6, control_track_ids: [], target_track_ids: [],
          source: "pre", threshold_db: -25, depth_db: -15, attack_sec: 0.02, hold_sec: 0.08, release_sec: 0.4 },
      ],
    }],
  };
}

/** 把旧的单控制轨 A/B 配置升级为多角色方案（保留旧配置在返回值外不变）。 */
export function migrateDuckingToV2(old: DuckingConfig): DuckingConfigV2 {
  const preset = old.presets.find((item) => item.id === old.active_preset_id) ?? old.presets[0];
  const role = {
    id: "host",
    name: preset?.name ?? "主持人",
    priority: 10,
    control_track_ids: old.control_track_id ? [old.control_track_id] : [],
    target_track_ids: [...old.target_track_ids],
    source: "pre" as const,
    threshold_db: preset?.threshold_db ?? -25,
    depth_db: preset?.depth_db ?? -12,
    attack_sec: preset?.attack_sec ?? 0.05,
    hold_sec: preset?.hold_sec ?? 0.15,
    release_sec: preset?.release_sec ?? 0.6,
  };
  return {
    version: 2,
    active_plan_id: "plan1",
    plans: [{ id: "plan1", name: "方案 1（由旧设置升级）", combine: "strongest", roles: [role] }],
  };
}

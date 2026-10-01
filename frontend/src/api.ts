import type { DuckingConfig, MediaInfo, PreviewReport, ProjectData } from "./types";

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

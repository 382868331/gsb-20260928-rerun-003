import type { AssetMeta, Project, RenderStatus } from "./types";

async function jsonOrThrow(res: Response) {
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status} ${detail}`);
  }
  return res.json();
}

export const api = {
  async loadProject(): Promise<Project> {
    return jsonOrThrow(await fetch("/api/project"));
  },
  async saveProject(project: Project): Promise<void> {
    await jsonOrThrow(await fetch("/api/project", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(project),
    }));
  },
  async listAssets(): Promise<AssetMeta[]> {
    return jsonOrThrow(await fetch("/api/assets"));
  },
  async uploadAsset(file: File, sampleRate: number): Promise<AssetMeta> {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`/api/assets?sample_rate=${sampleRate}`, { method: "POST", body: fd });
    return jsonOrThrow(res);
  },
  async startRender(project: Project): Promise<RenderStatus> {
    const res = await fetch("/api/render", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(project),
    });
    return jsonOrThrow(res);
  },
  async renderStatus(jobId: string): Promise<RenderStatus> {
    return jsonOrThrow(await fetch(`/api/render/${jobId}`));
  },
  async cancelRender(jobId: string): Promise<void> {
    await fetch(`/api/render/${jobId}/cancel`, { method: "POST" });
  },
  downloadUrl(jobId: string): string {
    return `/api/render/${jobId}/download`;
  },
};

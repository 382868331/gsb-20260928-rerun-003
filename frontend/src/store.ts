import { create } from "zustand";
import type { AssetMeta, Clip, Project, Track } from "./types";

export const PX_PER_SEC = 100;
export const GRID_SEC = 0.01;

const rid = () => Math.random().toString(16).slice(2, 10);

export function snap(t: number): number {
  return Math.max(0, Math.round(t / GRID_SEC) * GRID_SEC);
}

interface StudioState {
  project: Project;
  assets: Record<string, AssetMeta>;
  selectedAssetId: string | null;
  setProject: (p: Project) => void;
  patchProject: (patch: Partial<Project>) => void;
  addAsset: (a: AssetMeta) => void;
  selectAsset: (id: string | null) => void;
  addTrack: () => void;
  removeTrack: (trackId: string) => void;
  toggleMute: (trackId: string) => void;
  toggleSolo: (trackId: string) => void;
  setTrackGain: (trackId: string, gain: number) => void;
  renameTrack: (trackId: string, name: string) => void;
  addClip: (trackId: string, assetId: string) => void;
  removeClip: (trackId: string, clipId: string) => void;
  moveClip: (trackId: string, clipId: string, offset: number) => void;
  trimClip: (trackId: string, clipId: string, startDelta: number, endDelta: number) => void;
}

function updateTrack(project: Project, trackId: string, fn: (t: Track) => Track): Project {
  return {
    ...project,
    tracks: project.tracks.map((t) => (t.id === trackId ? fn(t) : t)),
  };
}

function updateClip(clip: Clip, startDelta: number, endDelta: number, assetDuration: number): Clip {
  const start = snap(Math.min(Math.max(0, clip.start + startDelta), assetDuration - GRID_SEC));
  const maxEnd = Math.min(assetDuration, clip.start + clip.duration + endDelta);
  const end = snap(Math.max(start + GRID_SEC, maxEnd));
  return { ...clip, start, duration: Math.max(GRID_SEC, end - start) };
}

export const useStudio = create<StudioState>((set) => ({
  project: {
    version: 1,
    id: rid(),
    name: "untitled",
    sample_rate: 48000,
    tracks: [],
    asset_ids: [],
  },
  assets: {},
  selectedAssetId: null,

  setProject: (p) => set({ project: p }),
  patchProject: (patch) => set((s) => ({ project: { ...s.project, ...patch } })),

  addAsset: (a) =>
    set((s) => ({
      assets: { ...s.assets, [a.id]: a },
      selectedAssetId: s.selectedAssetId ?? a.id,
      project: s.project.asset_ids.includes(a.id)
        ? s.project
        : { ...s.project, asset_ids: [...s.project.asset_ids, a.id] },
    })),
  selectAsset: (id) => set({ selectedAssetId: id }),

  addTrack: () =>
    set((s) => ({
      project: {
        ...s.project,
        tracks: [
          ...s.project.tracks,
          { id: rid(), name: `轨道 ${s.project.tracks.length + 1}`, clips: [], muted: false, solo: false, gain: 1 },
        ],
      },
    })),

  removeTrack: (trackId) =>
    set((s) => ({ project: { ...s.project, tracks: s.project.tracks.filter((t) => t.id !== trackId) } })),

  toggleMute: (trackId) =>
    set((s) => ({ project: updateTrack(s.project, trackId, (t) => ({ ...t, muted: !t.muted })) })),

  toggleSolo: (trackId) =>
    set((s) => ({ project: updateTrack(s.project, trackId, (t) => ({ ...t, solo: !t.solo })) })),

  setTrackGain: (trackId, gain) =>
    set((s) => ({ project: updateTrack(s.project, trackId, (t) => ({ ...t, gain })) })),

  renameTrack: (trackId, name) =>
    set((s) => ({ project: updateTrack(s.project, trackId, (t) => ({ ...t, name })) })),

  addClip: (trackId, assetId) =>
    set((s) => {
      const asset = s.assets[assetId];
      if (!asset) return s;
      return {
        project: updateTrack(s.project, trackId, (t) => {
          const end = t.clips.reduce((m, c) => Math.max(m, c.offset + c.duration), 0);
          const clip: Clip = {
            id: rid(),
            asset_id: assetId,
            offset: snap(end),
            start: 0,
            duration: Math.min(asset.duration, 1.0),
            gain: 1,
          };
          return { ...t, clips: [...t.clips, clip] };
        }),
      };
    }),

  removeClip: (trackId, clipId) =>
    set((s) => ({
      project: updateTrack(s.project, trackId, (t) => ({ ...t, clips: t.clips.filter((c) => c.id !== clipId) })),
    })),

  moveClip: (trackId, clipId, offset) =>
    set((s) => ({
      project: updateTrack(s.project, trackId, (t) => ({
        ...t,
        clips: t.clips.map((c) => (c.id === clipId ? { ...c, offset: snap(offset) } : c)),
      })),
    })),

  trimClip: (trackId, clipId, startDelta, endDelta) =>
    set((s) => ({
      project: updateTrack(s.project, trackId, (t) => ({
        ...t,
        clips: t.clips.map((c) => {
          if (c.id !== clipId) return c;
          const asset = s.assets[c.asset_id];
          return asset ? updateClip(c, startDelta, endDelta, asset.duration) : c;
        }),
      })),
    })),
}));

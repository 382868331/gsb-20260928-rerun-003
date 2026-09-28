import { describe, expect, it } from "vitest";
import { useStudio, snap, GRID_SEC } from "./store";
import type { AssetMeta, Project } from "./types";

const asset: AssetMeta = {
  id: "a1",
  original_name: "voice.wav",
  sample_rate: 48000,
  source_sample_rate: 48000,
  channels: 1,
  duration: 5,
  peaks: [0.5],
};

function reset(): Project {
  const s = useStudio.getState();
  const p: Project = {
    version: 1,
    id: "p1",
    name: "t",
    sample_rate: 48000,
    tracks: [],
    asset_ids: ["a1"],
  };
  s.setProject(p);
  s.addAsset(asset);
  s.addTrack();
  const trackId = useStudio.getState().project.tracks[0].id;
  s.addClip(trackId, "a1");
  return useStudio.getState().project;
}

describe("snap", () => {
  it("吸附到网格且不为负", () => {
    expect(snap(0.023)).toBeCloseTo(0.02, 10);
    expect(snap(-5)).toBe(0);
  });
});

describe("轨道编辑", () => {
  it("移动只改 offset，裁切参数不变", () => {
    const p0 = reset();
    const track = p0.tracks[0];
    const before = { ...track.clips[0] };
    useStudio.getState().moveClip(track.id, before.id, 2.345);
    const after = useStudio.getState().project.tracks[0].clips[0];
    expect(after.offset).toBeCloseTo(2.35, 5);
    expect(after.start).toBe(before.start);
    expect(after.duration).toBeCloseTo(before.duration);
    expect(after.asset_id).toBe(before.asset_id);
  });

  it("裁切只改 start/duration，不能越过素材边界或负长度", () => {
    const p0 = reset();
    const track = p0.tracks[0];
    const clip = track.clips[0];
    useStudio.getState().trimClip(track.id, clip.id, 100, 0); // 试图裁掉全部
    const after = useStudio.getState().project.tracks[0].clips[0];
    expect(after.start + after.duration).toBeLessThanOrEqual(asset.duration + 1e-9);
    expect(after.duration).toBeGreaterThanOrEqual(GRID_SEC - 1e-9);
    useStudio.getState().trimClip(track.id, clip.id, 0, 100);
    const after2 = useStudio.getState().project.tracks[0].clips[0];
    expect(after2.start + after2.duration).toBeLessThanOrEqual(asset.duration + 1e-9);
  });

  it("静音和独听状态切换", () => {
    const p0 = reset();
    const id = p0.tracks[0].id;
    useStudio.getState().toggleMute(id);
    expect(useStudio.getState().project.tracks[0].muted).toBe(true);
    useStudio.getState().toggleSolo(id);
    expect(useStudio.getState().project.tracks[0].solo).toBe(true);
  });
});

"""工程数据模型。

工程文件只保存“引用 + 参数”，不复制或改写素材本身：

  Clip.start / Clip.duration  表示对原始素材的裁切区间（秒）
  Clip.offset                 表示该片段在时间线上的起点（秒）

移动、裁切只修改这些数值，重新渲染即可得到同一素材的同一声音。
"""

from __future__ import annotations

import time
import uuid
from typing import Literal

from pydantic import BaseModel, Field


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class Clip(BaseModel):
    """时间线上的一个素材引用（非破坏性）。"""

    id: str = Field(default_factory=new_id)
    asset_id: str
    offset: float = Field(0.0, ge=0.0, description="时间线起点（秒）")
    start: float = Field(0.0, ge=0.0, description="素材内裁切起点（秒）")
    duration: float = Field(..., gt=0.0, description="裁切长度（秒）")
    gain: float = Field(1.0, ge=0.0)


class Track(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str = "Track"
    clips: list[Clip] = Field(default_factory=list)
    muted: bool = False
    solo: bool = False
    gain: float = Field(1.0, ge=0.0)


class Project(BaseModel):
    version: int = 1
    id: str = Field(default_factory=new_id)
    name: str = "untitled"
    sample_rate: int = 48000
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    tracks: list[Track] = Field(default_factory=list)
    asset_ids: list[str] = Field(default_factory=list)

    def clip_count(self) -> int:
        return sum(len(t.clips) for t in self.tracks)


class RenderResult(BaseModel):
    output_path: str
    sample_rate: int
    duration: float
    peak: float
    clipped: bool
    channels: int
    warnings: list[str] = Field(default_factory=list)


class AssetMeta(BaseModel):
    id: str
    original_name: str
    sample_rate: int = Field(description="导入时统一到的工程采样率")
    source_sample_rate: int
    channels: int
    duration: float
    peaks: list[float] = Field(default_factory=list)

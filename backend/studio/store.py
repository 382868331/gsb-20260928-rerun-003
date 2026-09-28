"""工程持久化与会话素材管理。

工程文件只存对素材文件的引用（相对 media 目录的路径）与裁切/摆放参数，
不复制音频数据；重新打开工程时按同样的引用区间渲染，声音与保存前一致。
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass

import numpy as np

from .audio import AudioInfo, load_for_project, probe
from .model import Project


PROJECT_FILE = "project.json"
MEDIA_DIRNAME = "media"


def save_project(project: Project, path: str) -> None:
    """原子写入工程 JSON。"""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    tmp_path = os.path.join(directory, ".project.json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(project.to_dict(), handle, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def load_project(path: str) -> Project:
    with open(path, "r", encoding="utf-8") as handle:
        return Project.from_dict(json.load(handle))


@dataclass
class ImportedMedia:
    media_id: str
    relpath: str
    original_name: str
    info: AudioInfo


class Session:
    """一个工作室会话：目录下含 project.json 与 media/。

    源文件保留原始采样率存放在 media/ 中；渲染时再统一重采样到工程采样率，
    保证 "不同采样率统一处理" 且素材本身不被修改。
    """

    def __init__(self, root: str, project: Project | None = None):
        self.root = os.path.abspath(root)
        self.media_dir = os.path.join(self.root, MEDIA_DIRNAME)
        os.makedirs(self.media_dir, exist_ok=True)
        self.project_path = os.path.join(self.root, PROJECT_FILE)
        self.project = project or Project()
        self._cache: dict[str, np.ndarray] = {}

    @classmethod
    def create(cls, root: str, sample_rate: int = 44100) -> "Session":
        return cls(root, Project(sample_rate=sample_rate))

    @classmethod
    def open(cls, root: str) -> "Session":
        path = os.path.join(os.path.abspath(root), PROJECT_FILE)
        project = load_project(path)
        return cls(root, project)

    def save(self) -> None:
        save_project(self.project, self.project_path)

    def import_file(self, src_path: str, original_name: str | None = None) -> ImportedMedia:
        """把外部音频复制进 media/（保持原样、不重编码）。"""
        info = probe(src_path)
        media_id = uuid.uuid4().hex[:12]
        ext = os.path.splitext(src_path)[1].lower() or ".wav"
        relpath = f"{media_id}{ext}"
        shutil.copyfile(src_path, os.path.join(self.media_dir, relpath))
        return ImportedMedia(
            media_id=media_id,
            relpath=relpath,
            original_name=original_name or os.path.basename(src_path),
            info=info,
        )

    def media_abspath(self, relpath: str) -> str:
        # 防止路径逃逸 media 目录。
        base = os.path.abspath(self.media_dir)
        candidate = os.path.abspath(os.path.join(base, relpath))
        if not candidate.startswith(base + os.sep) and candidate != base:
            raise ValueError("非法素材路径")
        return candidate

    def load_source(self, relpath: str) -> np.ndarray:
        if relpath not in self._cache:
            data, _info = load_for_project(
                self.media_abspath(relpath),
                self.project.sample_rate,
                self.project.channels,
            )
            self._cache[relpath] = data
        return self._cache[relpath]

    def clear_cache(self) -> None:
        self._cache.clear()

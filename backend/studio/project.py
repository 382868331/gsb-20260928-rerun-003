"""工程存取：JSON 只存引用和参数，重新打开可还原同样的声音。"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .models import Project


def save_project(project: Project, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    project.updated_at = time.time()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(project.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


def load_project(path: str | Path) -> Project:
    return Project(**json.loads(Path(path).read_text(encoding="utf-8")))

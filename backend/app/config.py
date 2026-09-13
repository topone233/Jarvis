from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from app.errors import ValidationError


def default_bootstrap_dir() -> Path:
    configured = os.environ.get("JARVIS_BOOTSTRAP_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    appdata = os.environ.get("APPDATA")
    if appdata:
        return (Path(appdata) / "Jarvis").resolve()
    return (Path.home() / ".jarvis").resolve()


@dataclass(frozen=True)
class BootstrapStore:
    root: Path

    @classmethod
    def create_default(cls) -> BootstrapStore:
        return cls(default_bootstrap_dir())

    @property
    def selection_path(self) -> Path:
        return self.root / "bootstrap.json"

    def get_data_directory(self) -> Path | None:
        if not self.selection_path.exists():
            return None
        payload = json.loads(self.selection_path.read_text(encoding="utf-8"))
        value = payload.get("data_directory")
        return Path(value).expanduser().resolve() if value else None

    def select_data_directory(self, directory: str) -> Path:
        """Records where the app should keep everything. The directory must exist.

        Creating it was the app answering a question that belongs to the person
        asking. A typo used to produce a new directory with nothing in it, and a
        correctly spelled path to a drive that was not mounted produced the same
        thing - both indistinguishable from a working setup whose data was gone.
        """
        target = Path(directory).expanduser().resolve()
        if not target.exists():
            raise ValidationError(f"目录不存在：{target}。请先创建它，或者换一个已有的目录。")
        if not target.is_dir():
            raise ValidationError(f"{target} 是一个文件，不是目录。")
        # This one is the app's own bookkeeping, not a directory anyone chose.
        self.root.mkdir(parents=True, exist_ok=True)
        temporary_path = self.selection_path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps({"data_directory": str(target)}, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary_path.replace(self.selection_path)
        return target

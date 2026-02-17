from __future__ import annotations

from pathlib import Path

from tools.build_site_content import build_site_content


def on_pre_build(config) -> None:
    project_root = Path(config.config_file_path).resolve().parent
    build_site_content(project_root=project_root)

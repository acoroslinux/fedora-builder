import os
from pathlib import Path

def get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent

def resolve_from_project(relative_path) -> Path:
    return get_project_root() / Path(relative_path)

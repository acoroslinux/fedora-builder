from pathlib import Path
from typing import Any, Dict, Optional

from fedora_builder.core.path_utils import resolve_from_project


def normalize_releasever(releasever: Any) -> str:
    release = str(releasever or "rawhide")
    return release.split("-")[-1] if release.startswith("fedora-") else release


def resolve_cache_root(config: Optional[Dict[str, Any]] = None) -> Path:
    system_config = (config or {}).get("system", {})
    cache_path = system_config.get("dnf_cache") or system_config.get("cache_dir") or "cache"
    root = Path(cache_path)
    if not root.is_absolute():
        root = resolve_from_project(root)
    return root


def package_cache_dir(config: Dict[str, Any], releasever: Any, basearch: str) -> Path:
    return resolve_cache_root(config) / "packages" / normalize_releasever(releasever) / basearch / "dnf"


def rootfs_seed_cache_path(config: Dict[str, Any], releasever: Any, basearch: str) -> Path:
    return resolve_cache_root(config) / "rootfs-seeds" / normalize_releasever(releasever) / basearch / "rootfs.tar.gz"


def toolchain_cache_dir(cache_root: Path, releasever: Any, target_arch: str) -> Path:
    return Path(cache_root) / "toolchain" / normalize_releasever(releasever) / target_arch

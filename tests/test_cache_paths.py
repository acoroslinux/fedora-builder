from fedora_builder.core.cache_paths import (
    package_cache_dir,
    resolve_cache_root,
    rootfs_seed_cache_path,
    toolchain_cache_dir,
)


def test_resolve_cache_root_uses_configured_root(tmp_path):
    config = {"system": {"dnf_cache": str(tmp_path / "builder-cache")}}

    assert resolve_cache_root(config) == tmp_path / "builder-cache"


def test_package_cache_is_split_by_release_and_arch(tmp_path):
    config = {"system": {"dnf_cache": str(tmp_path / "builder-cache")}}

    assert package_cache_dir(config, "45", "x86_64") == (
        tmp_path / "builder-cache" / "packages" / "45" / "x86_64" / "dnf"
    )


def test_release_names_are_normalized_for_cache_paths(tmp_path):
    config = {"system": {"dnf_cache": str(tmp_path / "builder-cache")}}

    assert rootfs_seed_cache_path(config, "fedora-45", "aarch64") == (
        tmp_path / "builder-cache" / "rootfs-seeds" / "45" / "aarch64" / "rootfs.tar.gz"
    )


def test_toolchain_cache_is_split_by_release_and_arch(tmp_path):
    cache_root = tmp_path / "builder-cache"

    assert toolchain_cache_dir(cache_root, "rawhide", "ppc64le") == (
        cache_root / "toolchain" / "rawhide" / "ppc64le"
    )

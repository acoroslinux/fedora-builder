"""
tests/test_config_loader.py — Tests for the ConfigLoader module.

All tests run in mock mode and require no root privileges or network access.
"""

import json
import platform
import tempfile
from pathlib import Path

import pytest

from fedora_builder.core.config_loader import ConfigLoader, ConfigLoaderError
from fedora_builder.core.path_utils import resolve_from_project


# ── Fixtures ────────────────────────────────────────────────────────────────────

@pytest.fixture
def config_root() -> Path:
    """Returns the real configs/ directory from the project root."""
    return resolve_from_project("configs")


@pytest.fixture
def loader(config_root) -> ConfigLoader:
    return ConfigLoader(config_root=config_root)


@pytest.fixture
def global_config_path(config_root) -> Path:
    return config_root / "global_build.json"


# ── test_load_json ───────────────────────────────────────────────────────────────

class TestLoadJson:
    def test_loads_valid_json_file(self, loader, global_config_path):
        data = loader.load_json(global_config_path)
        assert isinstance(data, dict)

    def test_raises_on_missing_file(self, loader, config_root):
        with pytest.raises(ConfigLoaderError, match="not found"):
            loader.load_json(config_root / "nonexistent_file.json")

    def test_raises_on_invalid_json(self, loader):
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            f.write("{ invalid json }")
            tmp_path = Path(f.name)
        with pytest.raises(ConfigLoaderError, match="Invalid JSON"):
            loader.load_json(tmp_path)
        tmp_path.unlink()


# ── test_load_profile ────────────────────────────────────────────────────────────

class TestLoadProfile:
    def test_loads_architecture_profile(self, loader):
        data = loader.load_profile("architectures", "x86_64")
        assert isinstance(data, dict)
        assert data.get("arch") == "x86_64" or "basearch" in data

    def test_loads_release_profile(self, loader):
        data = loader.load_profile("releases", "fedora-41")
        assert isinstance(data, dict)
        assert "releasever" in data

    def test_assemble_build_config_accepts_string_path(self, config_root):
        loader = ConfigLoader(config_root=config_root)
        config = loader.assemble_build_config(
            global_config_path=str(config_root / "global_build.json"),
            architecture="x86_64",
            release="fedora-41",
        )
        assert isinstance(config, dict)
        assert "packages" in config

    def test_loads_desktop_gnome(self, loader):
        data = loader.load_profile("desktops", "gnome")
        assert isinstance(data, dict)
        assert "packages" in data or "groups" in data

    def test_loads_desktop_kde(self, loader):
        data = loader.load_profile("desktops", "kde")
        assert isinstance(data, dict)

    def test_loads_desktop_xfce(self, loader):
        data = loader.load_profile("desktops", "xfce")
        assert isinstance(data, dict)

    def test_loads_base_packages(self, loader):
        data = loader.load_profile("packages", "base")
        assert isinstance(data, dict)

    def test_missing_profile_returns_empty_dict(self, loader):
        result = loader.load_profile("desktops", "nonexistent_de")
        assert result == {}

    def test_loads_repo_rpmfusion(self, loader):
        data = loader.load_profile("repos", "rpmfusion-free")
        assert isinstance(data, dict)

    def test_loads_variant_live(self, loader):
        data = loader.load_profile("variants", "live")
        assert isinstance(data, dict)

    def test_loads_service_base(self, loader):
        data = loader.load_profile("services", "base")
        assert isinstance(data, dict)


# ── test_merge_dicts ─────────────────────────────────────────────────────────────

class TestMergeDicts:
    def test_merge_simple_dicts(self, loader):
        base = {"a": 1, "b": 2}
        update = {"b": 99, "c": 3}
        result = loader._merge_dicts(base, update)
        assert result["a"] == 1
        assert result["b"] == 99
        assert result["c"] == 3

    def test_merge_lists_extends_without_duplicates(self, loader):
        base = {"packages": ["bash", "coreutils"]}
        update = {"packages": ["bash", "vim", "git"]}
        result = loader._merge_dicts(base, update)
        assert result["packages"].count("bash") == 1
        assert "vim" in result["packages"]
        assert "coreutils" in result["packages"]

    def test_merge_nested_dicts_recursive(self, loader):
        base = {"system": {"locale": "en_US.UTF-8", "timezone": "UTC"}}
        update = {"system": {"timezone": "Europe/Lisbon", "hostname": "myhost"}}
        result = loader._merge_dicts(base, update)
        assert result["system"]["locale"] == "en_US.UTF-8"
        assert result["system"]["timezone"] == "Europe/Lisbon"
        assert result["system"]["hostname"] == "myhost"

    def test_merge_does_not_mutate_base(self, loader):
        base = {"packages": ["bash"]}
        update = {"packages": ["vim"]}
        _ = loader._merge_dicts(base, update)
        assert base["packages"] == ["bash"]


# ── test_assemble_build_config ────────────────────────────────────────────────────

class TestAssembleBuildConfig:
    def test_basic_x86_64_f41_assembly(self, loader, global_config_path):
        config = loader.assemble_build_config(
            global_config_path=global_config_path,
            architecture="x86_64",
            release="fedora-41",
        )
        assert isinstance(config, dict)
        assert "packages" in config
        assert isinstance(config["packages"], list)

    def test_assembly_includes_desktop_gnome(self, loader, global_config_path):
        config = loader.assemble_build_config(
            global_config_path=global_config_path,
            architecture="x86_64",
            release="fedora-41",
            desktop="gnome",
        )
        assert isinstance(config, dict)
        assert "gdm" in config["services"]["enable"]

    def test_assembly_includes_desktop_kde(self, loader, global_config_path):
        config = loader.assemble_build_config(
            global_config_path=global_config_path,
            architecture="x86_64",
            release="fedora-41",
            desktop="kde",
        )
        assert isinstance(config, dict)

    def test_assembly_aarch64(self, loader, global_config_path):
        config = loader.assemble_build_config(
            global_config_path=global_config_path,
            architecture="aarch64",
            release="fedora-41",
        )
        assert isinstance(config, dict)

    def test_assembly_rawhide(self, loader, global_config_path):
        config = loader.assemble_build_config(
            global_config_path=global_config_path,
            architecture="x86_64",
            release="rawhide",
        )
        assert isinstance(config, dict)

    def test_assembly_with_package_profiles(self, loader, global_config_path):
        config = loader.assemble_build_config(
            global_config_path=global_config_path,
            architecture="x86_64",
            release="fedora-41",
            package_profiles=["audio", "bluetooth"],
        )
        assert isinstance(config["packages"], list)
        assert isinstance(config["copy_files"], list)

    def test_assembly_includes_base_copy_files(self, loader, global_config_path):
        config = loader.assemble_build_config(
            global_config_path=global_config_path,
            architecture="x86_64",
            release="fedora-41",
        )
        assert any(
            entry.get("destination") == "/boot/grub2/themes/fedora-modern"
            for entry in config["copy_files"]
            if isinstance(entry, dict)
        )

    def test_assembly_with_repo_profiles(self, loader, global_config_path):
        config = loader.assemble_build_config(
            global_config_path=global_config_path,
            architecture="x86_64",
            release="fedora-41",
            repo_profiles=["rpmfusion-free"],
        )
        assert isinstance(config, dict)

    def test_packages_are_deduplicated(self, loader, global_config_path):
        config = loader.assemble_build_config(
            global_config_path=global_config_path,
            architecture="x86_64",
            release="fedora-41",
            package_profiles=["base", "networking"],
        )
        pkgs = config.get("packages", [])
        assert len(pkgs) == len(set(pkgs)), "Packages list contains duplicates!"

    def test_assembly_with_variant_live(self, loader, global_config_path):
        config = loader.assemble_build_config(
            global_config_path=global_config_path,
            architecture="x86_64",
            release="fedora-41",
            variant="live",
        )
        assert isinstance(config, dict)

    def test_assembly_with_variant_minimal(self, loader, global_config_path):
        config = loader.assemble_build_config(
            global_config_path=global_config_path,
            architecture="x86_64",
            release="fedora-41",
            variant="minimal",
        )
        assert isinstance(config, dict)

    def test_all_desktops_load_without_error(self, loader, global_config_path):
        desktops = ["gnome", "kde", "xfce", "mate", "cinnamon", "lxqt", "i3", "sway", "hyprland"]
        for desktop in desktops:
            config = loader.assemble_build_config(
                global_config_path=global_config_path,
                architecture="x86_64",
                release="fedora-41",
                desktop=desktop,
            )
            assert isinstance(config, dict), f"Failed for desktop: {desktop}"

    def test_all_architectures_load_without_error(self, loader, global_config_path):
        for arch in ["x86_64", "aarch64", "ppc64le", "s390x"]:
            config = loader.assemble_build_config(
                global_config_path=global_config_path,
                architecture=arch,
                release="fedora-41",
            )
            assert isinstance(config, dict), f"Failed for arch: {arch}"

    def test_live_user_profile_admin(self, loader, global_config_path):
        config = loader.assemble_build_config(
            global_config_path=global_config_path,
            architecture="x86_64",
            release="fedora-41",
            live_profile="admin",
        )
        assert isinstance(config, dict)


# ── test_available_profiles ───────────────────────────────────────────────────────

class TestAvailableProfiles:
    @pytest.mark.parametrize("category,expected_min_count", [
        ("architectures", 4),
        ("desktops", 9),
        ("releases", 5),
        ("variants", 5),
        ("kernels", 3),
        ("bootloaders", 3),
        ("live-users", 2),
    ])
    def test_profile_counts(self, config_root, category, expected_min_count):
        profiles = sorted([p.stem for p in (config_root / category).glob("*.json")])
        assert len(profiles) >= expected_min_count, (
            f"Expected at least {expected_min_count} profiles in '{category}', got {len(profiles)}: {profiles}"
        )

    def test_package_profiles_exist(self, config_root):
        required = [
            "anaconda", "base", "audio", "bluetooth", "browsers", "chat", "cloud-tools",
            "desktop-apps", "dev-tools", "development", "filesystems", "gaming",
            "graphics", "ide", "multimedia", "multimedia-editing", "network-shares",
            "network-tools", "networking", "office", "printing", "productivity",
            "security", "system-utils", "virtualization", "wayland", "xorg"
        ]
        for name in required:
            path = config_root / "packages" / f"{name}.json"
            assert path.exists(), f"Missing package profile: {name}.json"

    def test_repo_profiles_exist(self, config_root):
        required = ["rpmfusion-free", "rpmfusion-nonfree"]
        for name in required:
            path = config_root / "repos" / f"{name}.json"
            assert path.exists(), f"Missing repo profile: {name}.json"

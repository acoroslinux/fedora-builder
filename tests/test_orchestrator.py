"""
tests/test_orchestrator.py — Integration tests for BuildOrchestrator.

Runs the full pipeline in mock mode across multiple configurations.
Requires no root privileges or network access.
"""

import tempfile
from pathlib import Path

import pytest

from fedora_builder.core.orchestrator import BuildOrchestrator, BuildOrchestratorError
from fedora_builder.core.path_utils import resolve_from_project


# ── Helpers ──────────────────────────────────────────────────────────────────────

def make_orchestrator(tmp_path=None, **kwargs) -> BuildOrchestrator:
    """Factory for BuildOrchestrator with sensible test defaults."""
    defaults = dict(
        arch="x86_64",
        config_path=str(resolve_from_project("configs/global_build.json")),
        mode="mock",
        clean=True,
        release="fedora-41",
        desktop=None,
        kernel="kernel",
        bootloader="grub2-hybrid",
        variant="live",
        package_profiles=[],
        service_profiles=[],
        repo_profiles=[],
        output_format="iso",
        compression="zstd",
        generate_manifest=True,
        generate_kickstart=False,
        with_calamares=False,
        force_isolated_toolchain=False,
        copr_repos=[],
    )
    defaults.update(kwargs)
    orch = BuildOrchestrator(**defaults)
    if tmp_path:
        orch.workdir = tmp_path / orch.arch
        orch.target_root = orch.workdir / "chroot"
    return orch


# ── test_orchestrator_construction ────────────────────────────────────────────────

class TestOrchestratorConstruction:
    def test_default_construction(self):
        orch = make_orchestrator()
        assert orch.arch == "x86_64"
        assert orch.mode == "mock"
        assert orch.release == "fedora-41"
        assert orch.output_format == "iso"

    def test_construction_with_desktop(self):
        orch = make_orchestrator(desktop="gnome")
        assert orch.desktop == "gnome"

    def test_construction_aarch64(self):
        orch = make_orchestrator(arch="aarch64")
        assert orch.arch == "aarch64"

    def test_construction_with_package_profiles(self):
        orch = make_orchestrator(package_profiles=["audio", "bluetooth"])
        assert "audio" in orch.package_profiles
        assert "bluetooth" in orch.package_profiles

    def test_construction_with_copr_repos(self):
        orch = make_orchestrator(copr_repos=["arivenitez/calamares"])
        assert "arivenitez/calamares" in orch.copr_repos


# ── test_validate ─────────────────────────────────────────────────────────────────

class TestValidate:
    def test_validate_returns_dict(self):
        orch = make_orchestrator()
        result = orch.validate()
        assert isinstance(result, dict)

    def test_validate_has_valid_key(self):
        orch = make_orchestrator()
        result = orch.validate()
        assert "valid" in result

    def test_validate_has_errors_key(self):
        orch = make_orchestrator()
        result = orch.validate()
        assert "errors" in result
        assert isinstance(result["errors"], list)

    def test_validate_basic_config_is_valid(self):
        orch = make_orchestrator(desktop="gnome")
        result = orch.validate()
        assert result.get("valid") is True


# ── test_build_mock_iso ───────────────────────────────────────────────────────────

class TestBuildMockISO:
    def test_mock_build_returns_path(self, tmp_path):
        orch = make_orchestrator(tmp_path=tmp_path)
        result = orch.build()
        assert isinstance(result, Path)

    def test_mock_build_gnome_iso(self, tmp_path):
        orch = make_orchestrator(tmp_path=tmp_path, desktop="gnome", output_format="iso")
        result = orch.build()
        assert isinstance(result, Path)

    def test_mock_build_kde_iso(self, tmp_path):
        orch = make_orchestrator(tmp_path=tmp_path, desktop="kde", output_format="iso")
        result = orch.build()
        assert isinstance(result, Path)

    def test_mock_build_xfce_iso(self, tmp_path):
        orch = make_orchestrator(tmp_path=tmp_path, desktop="xfce", output_format="iso")
        result = orch.build()
        assert isinstance(result, Path)

    def test_mock_build_no_desktop(self, tmp_path):
        orch = make_orchestrator(tmp_path=tmp_path, desktop=None, output_format="iso")
        result = orch.build()
        assert isinstance(result, Path)


# ── test_build_mock_formats ───────────────────────────────────────────────────────

class TestBuildMockFormats:
    def test_mock_build_img_format(self, tmp_path):
        orch = make_orchestrator(tmp_path=tmp_path, output_format="img")
        result = orch.build()
        assert isinstance(result, Path)

    def test_mock_build_tarball_format(self, tmp_path):
        orch = make_orchestrator(tmp_path=tmp_path, output_format="tarball")
        result = orch.build()
        assert isinstance(result, Path)

    def test_mock_build_container_format(self, tmp_path):
        orch = make_orchestrator(tmp_path=tmp_path, output_format="container")
        result = orch.build()
        assert isinstance(result, Path)


# ── test_build_mock_architectures ─────────────────────────────────────────────────

class TestBuildMockArchitectures:
    @pytest.mark.parametrize("arch", ["x86_64", "aarch64", "ppc64le", "s390x"])
    def test_mock_build_for_arch(self, tmp_path, arch):
        orch = make_orchestrator(tmp_path=tmp_path, arch=arch)
        result = orch.build()
        assert isinstance(result, Path)


# ── test_build_mock_releases ──────────────────────────────────────────────────────

class TestBuildMockReleases:
    @pytest.mark.parametrize("release", ["fedora-40", "fedora-41", "rawhide"])
    def test_mock_build_for_release(self, tmp_path, release):
        orch = make_orchestrator(tmp_path=tmp_path, release=release)
        result = orch.build()
        assert isinstance(result, Path)


# ── test_build_mock_variants ──────────────────────────────────────────────────────

class TestBuildMockVariants:
    @pytest.mark.parametrize("variant", ["live", "minimal", "server"])
    def test_mock_build_for_variant(self, tmp_path, variant):
        orch = make_orchestrator(tmp_path=tmp_path, variant=variant)
        result = orch.build()
        assert isinstance(result, Path)


# ── test_build_with_extras ────────────────────────────────────────────────────────

class TestBuildWithExtras:
    def test_mock_build_with_rpmfusion(self, tmp_path):
        orch = make_orchestrator(
            tmp_path=tmp_path,
            desktop="xfce",
            repo_profiles=["rpmfusion-free", "rpmfusion-nonfree"],
            package_profiles=["multimedia"],
        )
        result = orch.build()
        assert isinstance(result, Path)

    def test_mock_build_with_calamares(self, tmp_path):
        orch = make_orchestrator(
            tmp_path=tmp_path,
            desktop="gnome",
            with_calamares=True,
        )
        result = orch.build()
        assert isinstance(result, Path)

    def test_mock_build_generate_kickstart(self, tmp_path):
        orch = make_orchestrator(
            tmp_path=tmp_path,
            desktop="gnome",
            generate_kickstart=True,
        )
        result = orch.build()
        assert isinstance(result, Path)

    def test_mock_build_with_copr(self, tmp_path):
        orch = make_orchestrator(tmp_path=tmp_path, copr_repos=["arivenitez/calamares"])
        result = orch.build()
        assert isinstance(result, Path)

    def test_mock_build_no_manifest(self, tmp_path):
        orch = make_orchestrator(tmp_path=tmp_path, generate_manifest=False)
        result = orch.build()
        assert isinstance(result, Path)

    def test_mock_build_no_clean(self, tmp_path):
        orch = make_orchestrator(tmp_path=tmp_path, clean=False)
        result = orch.build()
        assert isinstance(result, Path)

    def test_mock_build_zstd_compression(self, tmp_path):
        orch = make_orchestrator(tmp_path=tmp_path, compression="zstd")
        result = orch.build()
        assert isinstance(result, Path)

    def test_mock_build_xz_compression(self, tmp_path):
        orch = make_orchestrator(tmp_path=tmp_path, compression="xz")
        result = orch.build()
        assert isinstance(result, Path)

    def test_mock_build_multimedia_codecs(self, tmp_path):
        orch = make_orchestrator(tmp_path=tmp_path, multimedia_codecs=True)
        assert "multimedia" in orch.package_profiles
        assert "rpmfusion-free" in orch.repo_profiles
        result = orch.build()
        assert isinstance(result, Path)

    def test_mock_build_flathub_and_zram(self, tmp_path):
        orch = make_orchestrator(tmp_path=tmp_path, with_flathub=True, with_zram=True)
        assert orch.config.get("with_flathub") is True
        assert orch.config.get("with_zram") is True
        result = orch.build()
        assert isinstance(result, Path)


# ── test_all_desktops ─────────────────────────────────────────────────────────────

class TestAllDesktops:
    @pytest.mark.parametrize("desktop", [
        "gnome", "kde", "xfce", "mate", "cinnamon", "lxqt", "i3", "sway", "hyprland"
    ])
    def test_mock_build_all_desktops(self, tmp_path, desktop):
        orch = make_orchestrator(tmp_path=tmp_path, desktop=desktop)
        result = orch.build()
        assert isinstance(result, Path)

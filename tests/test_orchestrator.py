"""
tests/test_orchestrator.py — Integration tests for BuildOrchestrator.

Runs the full pipeline in mock mode across multiple configurations.
Requires no root privileges or network access.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fedora_builder.core.orchestrator import BuildOrchestrator, BuildOrchestratorError
from fedora_builder.core.iso_engine import ISOEngine
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

    def test_construction_propagates_live_and_feature_overrides_into_config(self):
        orch = make_orchestrator(
            live_user="demo",
            live_groups=["wheel", "audio"],
            with_calamares=True,
            with_flathub=True,
            with_zram=False,
        )
        assert orch.config["live_user"] == "demo"
        assert orch.config["live_groups"] == ["wheel", "audio"]
        assert orch.config["with_calamares"] is True
        assert orch.config["with_flathub"] is True
        assert orch.config["with_zram"] is False

    def test_runtime_config_promotes_nested_system_defaults(self):
        orch = make_orchestrator()
        config = {
            "system": {"hostname": "fedora-live", "locale": "pt_PT.UTF-8"},
            "boot": {"timeout": 15},
            "live_user": {"name": "demo", "groups": ["wheel", "audio"]},
        }
        normalized = orch._normalize_runtime_config(config)
        assert normalized["hostname"] == "fedora-live"
        assert normalized["locale"] == "pt_PT.UTF-8"
        assert normalized["timeout"] == 15
        assert normalized["live_user"]["name"] == "demo"

    def test_server_variant_adds_anaconda_profile(self):
        orch = make_orchestrator(variant="server")
        assert "anaconda" in orch.package_profiles

    def test_iso_engine_respects_bios_only_bootloader_profile(self, tmp_path):
        rootfs = tmp_path / "rootfs"
        rootfs.mkdir()
        (rootfs / "boot").mkdir()
        (rootfs / "boot" / "vmlinuz").write_text("kernel")
        (rootfs / "boot" / "initramfs.img").write_text("initrd")

        iso_engine = ISOEngine(
            workdir=tmp_path,
            target_root=rootfs,
            output_name="test-iso",
            config={"bios_enabled": False, "uefi_enabled": True, "basearch": "x86_64", "boot": {"kernel_params": "quiet rhgb"}},
            mode="mock",
            toolchain=MagicMock(mode="mock"),
        )

        iso_path = iso_engine.build_iso()
        assert iso_path.exists()
        assert not (tmp_path / "iso_root" / "isolinux").exists()

    def test_grub_efiboot_skips_shim_when_secure_boot_disabled(self, tmp_path):
        rootfs = tmp_path / "rootfs"
        efi_dir = rootfs / "boot" / "efi" / "EFI"
        efi_dir.mkdir(parents=True)
        (efi_dir / "BOOT").mkdir()
        (efi_dir / "BOOT" / "grubx64.efi").write_bytes(b"grub")
        (efi_dir / "BOOT" / "shimx64.efi").write_bytes(b"shim")

        iso_root = tmp_path / "iso_root"
        iso_root.mkdir()

        grub = MagicMock()
        boot = __import__("fedora_builder.core.bootloaders.grub2", fromlist=["Grub2Bootloader"]).Grub2Bootloader
        cfg = {"secure_boot": False, "system": {"iso_label": "TESTISO"}, "boot": {"kernel_params": "quiet rhgb"}}
        loader = boot(cfg, "x86_64", toolchain=None)
        img = loader.generate_efiboot_img(iso_root, rootfs)
        assert img.exists()
        assert (iso_root / "EFI" / "BOOT" / "grubx64.efi").exists()
        assert (iso_root / "EFI" / "BOOT" / "BOOTX64.EFI").exists()
        assert (iso_root / "EFI" / "BOOT" / "BOOTX64.EFI").read_bytes() == b"grub"
        assert not (iso_root / "EFI" / "BOOT" / "BOOTX64.EFI").read_bytes() == b"shim"

    def test_iso_engine_retries_classic_xorriso_when_hybrid_layout_fails(self, tmp_path):
        rootfs = tmp_path / "rootfs"
        rootfs.mkdir()
        toolchain = MagicMock()
        toolchain.run_tool.side_effect = [Exception("hybrid failed"), None]
        iso_engine = ISOEngine(
            workdir=tmp_path,
            target_root=rootfs,
            output_name="test-iso",
            config={"bios_enabled": True, "uefi_enabled": True, "basearch": "x86_64", "boot": {"kernel_params": "quiet rhgb"}},
            mode="real",
            toolchain=toolchain,
        )

        iso_engine._run_xorriso(["--grub2-mbr"], ["-eltorito-boot", "isolinux/isolinux.bin"])

        assert toolchain.run_tool.call_count == 2
        assert toolchain.run_tool.call_args_list[1].args[1] == ["-eltorito-boot", "isolinux/isolinux.bin"]

    def test_live_variant_dracut_uses_live_modules(self):
        orch = make_orchestrator(variant="live")
        orch.config["live_media"] = True
        cmd = orch._build_dracut_command("1.2.3")
        assert "--add" in cmd
        assert "livenet" in cmd

    def test_server_variant_iso_keeps_live_modules_for_iso_boot(self):
        orch = make_orchestrator(variant="server")
        orch.config["live_media"] = False
        cmd = orch._build_dracut_command("1.2.3")
        assert "--add" in cmd
        assert "livenet" in cmd

    def test_server_variant_non_iso_can_skip_live_modules(self):
        orch = make_orchestrator(variant="server", output_format="img")
        orch.config["live_media"] = False
        cmd = orch._build_dracut_command("1.2.3")
        assert "livenet" not in cmd
        assert "--add" not in cmd

    def test_dracut_module_filter_skips_missing_modules(self, tmp_path):
        orch = make_orchestrator()
        modules_dir = tmp_path / "usr" / "lib" / "dracut" / "modules.d"
        (modules_dir / "90dmsquash-live").mkdir(parents=True)
        (modules_dir / "95qemu").mkdir(parents=True)
        selected = orch._filter_available_dracut_modules(
            ["livenet", "dmsquash-live", "qemu"],
            rootfs=tmp_path,
        )
        assert selected == ["dmsquash-live", "qemu"]


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

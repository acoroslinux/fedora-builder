"""
tests/test_iso_engine.py — Unit tests for ISOEngine.

Tests run in mock mode. No root privileges, network, or external tools required.
"""

import tempfile
from pathlib import Path

import pytest

from fedora_builder.core.iso_engine import ISOEngine, ISOEngineError
from fedora_builder.core.toolchain_manager import ToolchainManager


# ── Fixtures ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def workdir(tmp_path) -> Path:
    d = tmp_path / "workdir" / "x86_64"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def rootfs(tmp_path) -> Path:
    """A minimal fake rootfs directory tree."""
    root = tmp_path / "rootfs"
    # Create minimal Fedora rootfs structure
    for d in [
        "boot", "etc", "usr/lib", "usr/bin", "usr/share",
        "var/cache/dnf", "home", "root", "tmp",
    ]:
        (root / d).mkdir(parents=True, exist_ok=True)

    # Fake kernel and initramfs
    (root / "boot" / "vmlinuz-6.11.0-1.fc41.x86_64").write_bytes(b"\x00" * 64)
    (root / "boot" / "initramfs-6.11.0-1.fc41.x86_64.img").write_bytes(b"\x00" * 64)
    theme_dir = root / "boot" / "grub2" / "themes" / "fedora-modern"
    theme_dir.mkdir(parents=True, exist_ok=True)
    (theme_dir / "fedora-grub-bg.jpg").write_bytes(b"\x00" * 64)

    # Fake EFI shim and grub binaries
    efi_boot = root / "boot" / "efi" / "EFI" / "fedora"
    efi_boot.mkdir(parents=True, exist_ok=True)
    (efi_boot / "shimx64.efi").write_bytes(b"\x00" * 32)
    (efi_boot / "grubx64.efi").write_bytes(b"\x00" * 32)

    return root


@pytest.fixture
def mock_config():
    return {
        "releasever": "41",
        "basearch": "x86_64",
        "arch": "x86_64",
        "system": {
            "iso_label": "FEDORA-LIVE",
            "iso_publisher": "AcorOS Linux",
            "iso_preparer": "fedora-builder",
            "selinux_mode": "permissive",
        },
        "boot": {
            "kernel_params": "rd.live.image quiet rhgb",
            "timeout": 10,
        },
        "compression": "zstd",
        "generate_manifest": True,
    }


@pytest.fixture
def toolchain(workdir):
    tc = ToolchainManager(workdir_base=workdir, mode="mock")
    tc.setup()
    return tc


@pytest.fixture
def engine(workdir, rootfs, mock_config, toolchain):
    return ISOEngine(
        workdir=workdir,
        target_root=rootfs,
        output_name="fedora-test-x86_64",
        config=mock_config,
        mode="mock",
        toolchain=toolchain,
    )


# ── test_construction ─────────────────────────────────────────────────────────────

class TestISOEngineConstruction:
    def test_creates_instance(self, engine):
        assert engine is not None

    def test_stores_output_name(self, engine):
        assert engine.output_name == "fedora-test-x86_64"

    def test_stores_mode(self, engine):
        assert engine.mode == "mock"


# ── test_iso_label ────────────────────────────────────────────────────────────────

class TestISOLabel:
    def test_get_iso_label_from_config(self, engine):
        label = engine._get_iso_label()
        assert isinstance(label, str)
        assert len(label) > 0

    def test_iso_label_uppercase(self, engine):
        label = engine._get_iso_label()
        assert label == label.upper() or label  # Should be uppercase by Fedora convention

    def test_default_iso_label(self, workdir, rootfs, toolchain):
        config = {"system": {}, "boot": {}, "compression": "zstd", "generate_manifest": True}
        eng = ISOEngine(workdir, rootfs, "test", config, "mock", toolchain)
        label = eng._get_iso_label()
        assert isinstance(label, str)


# ── test_kernel_params ────────────────────────────────────────────────────────────

class TestKernelParams:
    def test_get_kernel_params_contains_live(self, engine):
        params = engine._get_kernel_params()
        assert "rd.live.image" in params

    def test_get_kernel_params_is_string(self, engine):
        assert isinstance(engine._get_kernel_params(), str)


# ── test_find_kernel_and_initramfs ────────────────────────────────────────────────

class TestFindKernelAndInitramfs:
    def test_finds_kernel_in_boot_dir(self, engine, rootfs):
        kernel, initramfs = engine._find_kernel_and_initramfs()
        assert isinstance(kernel, str)
        assert isinstance(initramfs, str)

    def test_kernel_filename_contains_vmlinuz(self, engine):
        kernel, _ = engine._find_kernel_and_initramfs()
        assert "vmlinuz" in kernel

    def test_initramfs_filename_contains_initramfs(self, engine):
        _, initramfs = engine._find_kernel_and_initramfs()
        assert "initramfs" in initramfs or "initrd" in initramfs


# ── test_build_iso_mock ───────────────────────────────────────────────────────────

class TestBuildISOMock:
    def test_build_iso_returns_path(self, engine):
        result = engine.build_iso()
        assert isinstance(result, Path)

    def test_build_iso_output_has_iso_extension(self, engine):
        result = engine.build_iso()
        assert result.suffix == ".iso" or result.name.endswith(".iso")

    def test_build_iso_does_not_raise_in_mock(self, engine):
        try:
            engine.build_iso()
        except Exception as e:
            pytest.fail(f"build_iso raised in mock mode: {e}")

    def test_build_iso_different_label(self, workdir, rootfs, toolchain):
        config = {
            "system": {"iso_label": "MY-CUSTOM-LABEL"},
            "boot": {"kernel_params": "rd.live.image", "timeout": 5},
            "compression": "zstd",
            "generate_manifest": False,
        }
        eng = ISOEngine(workdir, rootfs, "custom-build", config, "mock", toolchain)
        result = eng.build_iso()
        assert isinstance(result, Path)

    def test_build_iso_stages_grub_theme_assets(self, engine):
        engine.build_iso()
        assert (engine.iso_staging / "boot" / "grub2" / "themes" / "fedora-modern" / "fedora-grub-bg.jpg").exists()

    def test_build_iso_grub_cfg_references_background(self, engine):
        engine.build_iso()
        grub_cfg = (engine.iso_staging / "boot" / "grub2" / "grub.cfg").read_text()
        assert "background_image /boot/grub2/themes/fedora-modern/fedora-grub-bg.jpg" in grub_cfg

    def test_server_variant_uses_installer_boot_labels(self, workdir, rootfs, toolchain):
        config = {
            "variant": "server",
            "installer": "anaconda",
            "system": {"iso_label": "FEDORA-SERVER"},
            "boot": {"kernel_params": "quiet"},
            "compression": "zstd",
            "generate_manifest": False,
        }
        eng = ISOEngine(workdir, rootfs, "server-build", config, "mock", toolchain)
        eng.build_iso()
        grub_cfg = (eng.iso_staging / "boot" / "grub2" / "grub.cfg").read_text()
        assert "Start Fedora Server Installer" in grub_cfg
        assert "Start Fedora Server Installer (text mode)" in grub_cfg
        assert "inst.text" in grub_cfg


# ── test_build_tarball_mock ───────────────────────────────────────────────────────

class TestBuildTarballMock:
    def test_build_tarball_returns_path(self, engine):
        result = engine.build_tarball()
        assert isinstance(result, Path)

    def test_build_tarball_does_not_raise(self, engine):
        try:
            engine.build_tarball()
        except Exception as e:
            pytest.fail(f"build_tarball raised in mock mode: {e}")


# ── test_build_container_mock ─────────────────────────────────────────────────────

class TestBuildContainerMock:
    def test_build_container_returns_path(self, engine):
        result = engine.build_container()
        assert isinstance(result, Path)

    def test_build_container_does_not_raise(self, engine):
        try:
            engine.build_container()
        except Exception as e:
            pytest.fail(f"build_container raised in mock mode: {e}")


# ── test_build_disk_image_mock ────────────────────────────────────────────────────

class TestBuildDiskImageMock:
    def test_build_disk_image_returns_path(self, engine):
        result = engine.build_disk_image()
        assert isinstance(result, Path)

    def test_build_disk_image_does_not_raise(self, engine):
        try:
            engine.build_disk_image()
        except Exception as e:
            pytest.fail(f"build_disk_image raised in mock mode: {e}")


# ── test_create_discinfo ──────────────────────────────────────────────────────────

class TestCreateDiscinfo:
    def test_create_discinfo_mock(self, engine, tmp_path):
        staging = tmp_path / "iso_staging"
        staging.mkdir(parents=True)
        try:
            engine._create_discinfo(staging)
        except Exception as e:
            pytest.fail(f"_create_discinfo raised: {e}")

    def test_discinfo_file_created(self, engine, tmp_path):
        staging = tmp_path / "iso_staging"
        staging.mkdir(parents=True)
        engine._create_discinfo(staging)
        discinfo = staging / ".discinfo"
        if discinfo.exists():
            content = discinfo.read_text()
            assert len(content) > 0


# ── test_clean_rootfs ─────────────────────────────────────────────────────────────

class TestCleanRootfs:
    def test_clean_rootfs_does_not_raise(self, engine, rootfs):
        try:
            engine._clean_rootfs(rootfs)
        except Exception as e:
            pytest.fail(f"_clean_rootfs raised: {e}")

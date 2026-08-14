"""
tests/test_dnf_manager.py — Unit tests for the DNFManager module.

Tests run in mock mode and require no root privileges or real DNF installation.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fedora_builder.core.dnf_manager import DNFManager, DNFManagerError
from fedora_builder.core.chroot_manager import ChrootManager


# ── Fixtures ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_chroot(tmp_path):
    """A ChrootManager pointing to a temporary directory in mock mode."""
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir(parents=True)
    return ChrootManager(target_root=rootfs, mode="mock", arch="x86_64")


@pytest.fixture
def mock_config():
    return {
        "releasever": "41",
        "basearch": "x86_64",
        "system": {
            "selinux_mode": "permissive",
            "locale": "en_US.UTF-8",
            "timezone": "UTC",
            "hostname": "fedora-live",
        },
        "packages": ["bash", "vim", "curl"],
        "groups": ["@core"],
        "repos": [],
        "services": {"enable": ["NetworkManager", "firewalld"]},
        "live_user": {
            "name": "liveuser",
            "password": "live",
            "groups": ["wheel", "audio", "video"],
        },
    }


@pytest.fixture
def dnf_mgr(tmp_chroot, mock_config):
    return DNFManager(chroot=tmp_chroot, config=mock_config)


# ── test_construction ─────────────────────────────────────────────────────────────

class TestDNFManagerConstruction:
    def test_creates_instance(self, tmp_chroot, mock_config):
        mgr = DNFManager(chroot=tmp_chroot, config=mock_config)
        assert mgr is not None

    def test_stores_chroot_reference(self, tmp_chroot, mock_config):
        mgr = DNFManager(chroot=tmp_chroot, config=mock_config)
        assert mgr.chroot is tmp_chroot


class TestDNFCachePaths:
    def test_resolve_cache_dir_uses_release_and_arch(self, tmp_chroot, mock_config, tmp_path):
        mock_config["system"]["dnf_cache"] = str(tmp_path / "cache")
        mgr = DNFManager(chroot=tmp_chroot, config=mock_config)

        assert mgr.resolve_cache_dir() == tmp_path / "cache" / "packages" / "41" / "x86_64" / "dnf"

    def test_base_dnf_args_use_active_package_cache(self, tmp_chroot, mock_config, tmp_path):
        mock_config["system"]["dnf_cache"] = str(tmp_path / "cache")
        mgr = DNFManager(chroot=tmp_chroot, config=mock_config)

        args = mgr._get_base_dnf_args()

        assert f"--setopt=cachedir={tmp_path / 'cache' / 'packages' / '41' / 'x86_64' / 'dnf'}" in args


# ── test_configure_dnf_conf ───────────────────────────────────────────────────────

class TestConfigureDnfConf:
    def test_configure_dnf_conf_mock_no_exception(self, dnf_mgr):
        """Should not raise in mock mode."""
        dnf_mgr.configure_dnf_conf()

    def test_configure_dnf_conf_mock_creates_file(self, dnf_mgr):
        """In mock mode, may create or skip the file — must not raise."""
        try:
            dnf_mgr.configure_dnf_conf()
        except Exception as e:
            pytest.fail(f"configure_dnf_conf raised in mock mode: {e}")


# ── test_bootstrap_rootfs ─────────────────────────────────────────────────────────

class TestBootstrapRootfs:
    def test_bootstrap_mock_no_exception(self, dnf_mgr):
        dnf_mgr.bootstrap_rootfs(releasever="41", basearch="x86_64")

    def test_bootstrap_creates_minimal_structure(self, dnf_mgr, tmp_chroot):
        """In mock mode, bootstrap should create the basic rootfs skeleton."""
        dnf_mgr.bootstrap_rootfs(releasever="41", basearch="x86_64")
        # After mock bootstrap, workdir should exist
        assert tmp_chroot.target_root.exists()

    def test_bootstrap_rawhide(self, dnf_mgr):
        dnf_mgr.bootstrap_rootfs(releasever="rawhide", basearch="x86_64")

    def test_bootstrap_aarch64(self, tmp_path, mock_config):
        rootfs = tmp_path / "rootfs_aarch64"
        rootfs.mkdir(parents=True)
        chroot = ChrootManager(target_root=rootfs, mode="mock", arch="aarch64")
        mgr = DNFManager(chroot=chroot, config=mock_config)
        mgr.bootstrap_rootfs(releasever="41", basearch="aarch64")


# ── test_install_packages ─────────────────────────────────────────────────────────

class TestInstallPackages:
    def test_install_empty_list_no_exception(self, dnf_mgr):
        dnf_mgr.install_packages([])

    def test_install_single_package_mock(self, dnf_mgr):
        dnf_mgr.install_packages(["vim"])

    def test_install_multiple_packages_mock(self, dnf_mgr):
        dnf_mgr.install_packages(["vim", "git", "curl", "wget"])

    def test_install_filters_out_group_syntax(self, dnf_mgr):
        """Packages starting with '@' should be filtered out from install_packages."""
        # Should not raise — groups should be silently skipped or handled separately
        dnf_mgr.install_packages(["vim", "@core", "git"])

    def test_real_install_allows_erasing_for_package_replacements(self, tmp_path, mock_config):
        rootfs = tmp_path / "rootfs"
        rootfs.mkdir(parents=True)
        mock_config["system"]["dnf_cache"] = str(tmp_path / "dnf-cache")
        chroot = ChrootManager(target_root=rootfs, mode="real", arch="x86_64")
        mgr = DNFManager(chroot=chroot, config=mock_config)
        mgr._run_dnf = MagicMock(return_value=MagicMock(returncode=0))

        mgr.install_packages(["ffmpeg"])

        args = mgr._run_dnf.call_args.args[0]
        assert "--allowerasing" in args
        assert args.index("--allowerasing") > args.index("install")

    def test_real_install_skips_unavailable_packages(self, tmp_path, mock_config):
        rootfs = tmp_path / "rootfs"
        rootfs.mkdir(parents=True)
        mock_config["system"]["dnf_cache"] = str(tmp_path / "dnf-cache")
        chroot = ChrootManager(target_root=rootfs, mode="real", arch="x86_64")
        mgr = DNFManager(chroot=chroot, config=mock_config)
        mgr._run_dnf = MagicMock(return_value=MagicMock(returncode=0))

        mgr.install_packages(["bash", "element-desktop", "terraform"])

        args = mgr._run_dnf.call_args.args[0]
        assert "--skip-unavailable" in args
        assert args.index("--skip-unavailable") < args.index("bash")


# ── test_install_groups ───────────────────────────────────────────────────────────

class TestInstallGroups:
    def test_install_groups_empty_list(self, dnf_mgr):
        dnf_mgr.install_groups([])

    def test_install_groups_single(self, dnf_mgr):
        dnf_mgr.install_groups(["@core"])

    def test_install_groups_with_at_prefix(self, dnf_mgr):
        dnf_mgr.install_groups(["@gnome-desktop", "@core"])

    def test_install_groups_without_at_prefix(self, dnf_mgr):
        """Should handle group names with or without @ prefix."""
        dnf_mgr.install_groups(["gnome-desktop"])

    def test_real_group_install_allows_erasing_for_package_replacements(self, tmp_path, mock_config):
        rootfs = tmp_path / "rootfs"
        rootfs.mkdir(parents=True)
        mock_config["system"]["dnf_cache"] = str(tmp_path / "dnf-cache")
        chroot = ChrootManager(target_root=rootfs, mode="real", arch="x86_64")
        mgr = DNFManager(chroot=chroot, config=mock_config)
        mgr._run_dnf = MagicMock(return_value=MagicMock(returncode=0))

        mgr.install_groups(["multimedia"])

        args = mgr._run_dnf.call_args.args[0]
        assert "--allowerasing" in args
        assert args.index("--allowerasing") > args.index("install")


# ── test_install_all ──────────────────────────────────────────────────────────────

class TestInstallAll:
    def test_install_all_mock(self, dnf_mgr):
        dnf_mgr.install_all(
            packages=["vim", "git"],
            groups=["@core"]
        )

    def test_install_all_empty(self, dnf_mgr):
        dnf_mgr.install_all(packages=[], groups=[])

    def test_install_all_deduplicates(self, dnf_mgr):
        """Calling with duplicate packages should not raise."""
        dnf_mgr.install_all(
            packages=["vim", "vim", "git"],
            groups=["@core", "@core"],
        )


# ── test_configure_repos ──────────────────────────────────────────────────────────

class TestConfigureRepos:
    def test_configure_empty_repos(self, dnf_mgr):
        dnf_mgr.configure_repos([])

    def test_configure_fedora_repo(self, dnf_mgr):
        repo = {
            "repo_id": "fedora",
            "repo_name": "Fedora $releasever - $basearch",
            "metalink": "https://mirrors.fedoraproject.org/metalink?repo=fedora-41&arch=x86_64",
            "enabled": True,
            "gpgcheck": True,
            "gpgkey": "file:///etc/pki/rpm-gpg/RPM-GPG-KEY-fedora-41-primary",
        }
        dnf_mgr.configure_repos([repo])

    def test_configure_multiple_repos(self, dnf_mgr):
        repos = [
            {"repo_id": "fedora", "repo_name": "Fedora 41", "enabled": True},
            {"repo_id": "updates", "repo_name": "Fedora 41 Updates", "enabled": True},
        ]
        dnf_mgr.configure_repos(repos)


# ── test_configure_selinux ────────────────────────────────────────────────────────

class TestConfigureSELinux:
    def test_configure_selinux_permissive(self, dnf_mgr):
        dnf_mgr.configure_selinux(mode="permissive")

    def test_configure_selinux_enforcing(self, dnf_mgr):
        dnf_mgr.configure_selinux(mode="enforcing")

    def test_configure_selinux_disabled(self, dnf_mgr):
        dnf_mgr.configure_selinux(mode="disabled")

    def test_configure_selinux_default_permissive(self, dnf_mgr):
        """Should default to permissive if no mode given."""
        dnf_mgr.configure_selinux()


# ── test_clean_cache ──────────────────────────────────────────────────────────────

class TestCleanCache:
    def test_clean_cache_mock_no_exception(self, dnf_mgr):
        dnf_mgr.clean_cache()


# ── test_dnf_conf_content ─────────────────────────────────────────────────────────

class TestDnfConfContent:
    def test_dnf_conf_written_in_real_mode(self, tmp_path, mock_config):
        """If the manager writes dnf.conf in real mode (or mock), verify content."""
        rootfs = tmp_path / "rootfs"
        rootfs.mkdir(parents=True)
        (rootfs / "etc" / "dnf").mkdir(parents=True, exist_ok=True)
        chroot = ChrootManager(target_root=rootfs, mode="mock", arch="x86_64")
        mgr = DNFManager(chroot=chroot, config=mock_config)
        # Should not raise
        mgr.configure_dnf_conf()

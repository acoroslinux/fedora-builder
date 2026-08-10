from subprocess import CompletedProcess
from unittest.mock import patch

from fedora_builder.core.toolchain_manager import ToolchainManager


def test_toolchain_cache_uses_shared_root_split_by_release_and_arch(tmp_path):
    cache_root = tmp_path / "cache"

    mgr = ToolchainManager(
        workdir_base=tmp_path / "workdir" / "x86_64",
        mode="mock",
        target_arch="x86_64",
        releasever="45",
        cache_root=cache_root,
    )

    assert mgr.cache_root == cache_root
    assert mgr.cache_dir == cache_root / "toolchain" / "45" / "x86_64"


def test_toolchain_mount_virtual_fs_is_idempotent(tmp_path):
    mgr = ToolchainManager(
        workdir_base=tmp_path / "workdir" / "x86_64",
        mode="real",
        target_arch="x86_64",
        releasever="45",
        cache_root=tmp_path / "cache",
    )
    mgr.build_host_dir.mkdir(parents=True)

    with patch("os.geteuid", return_value=0), patch(
        "subprocess.run",
        return_value=CompletedProcess(args=["mount"], returncode=0, stdout="", stderr=""),
    ) as run:
        mgr.mount_virtual_fs()
        first_mount_call_count = run.call_count

        mgr.mount_virtual_fs()

    assert first_mount_call_count > 0
    assert run.call_count == first_mount_call_count

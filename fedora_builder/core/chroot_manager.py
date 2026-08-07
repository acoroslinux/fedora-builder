import logging
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

try:
    from fedora_builder.core.command_runner import CommandRunner
except ImportError:
    class CommandRunner:
        @staticmethod
        def run_chroot_stream(chroot_path, command, env=None, mode="mock"):
            if mode == "mock":
                return subprocess.CompletedProcess(command, 0)
            cmd = ["chroot", chroot_path] + (command if isinstance(command, list) else command.split())
            return subprocess.run(cmd, env=env)

logger = logging.getLogger("chroot_manager")

_QEMU_STATIC_MAP = {
    "aarch64": "qemu-aarch64-static",
    "arm64":   "qemu-aarch64-static",
    "ppc64le": "qemu-ppc64le-static",
    "s390x":   "qemu-s390x-static",
}

def _host_arch() -> str:
    return platform.machine().lower()

class ChrootManagerError(Exception):
    """Exception raised for ChrootManager operations."""
    pass

class ChrootManager:
    def __init__(
        self,
        target_root: Path,
        mode: str = "mock",
        cache_dir: Optional[Path] = None,
        arch: Optional[str] = None,
        toolchain=None,
    ):
        self.target_root = Path(target_root).resolve()
        self.mode = mode.lower()
        self.cache_dir = Path(cache_dir).resolve() if cache_dir else None
        self.toolchain = toolchain
        self.is_mounted = False
        if arch:
            self.arch = arch.lower()
        else:
            self.arch = self.target_root.parent.name.lower()

    def _setup_qemu_static(self):
        host = _host_arch()
        target = self.arch

        if host in ("x86_64", "amd64") and target in ("x86_64", "amd64"):
            return
        if host == target:
            return

        qemu_bin_name = _QEMU_STATIC_MAP.get(target)
        if not qemu_bin_name:
            logger.debug(f"No QEMU static binary mapping for {target}.")
            return

        host_qemu = shutil.which(qemu_bin_name)
        if not host_qemu:
            for candidate in [f"/usr/bin/{qemu_bin_name}", f"/usr/local/bin/{qemu_bin_name}"]:
                if Path(candidate).exists():
                    host_qemu = candidate
                    break

        if not host_qemu:
            logger.warning(f"[QEMU] {qemu_bin_name} not found on host. Cross-arch might fail.")
            return

        chroot_qemu_dir = self.target_root / "usr" / "bin"
        try:
            chroot_qemu_dir.mkdir(parents=True, exist_ok=True)
            chroot_qemu_path = chroot_qemu_dir / qemu_bin_name
            if not chroot_qemu_path.exists():
                shutil.copy2(host_qemu, chroot_qemu_path)
                chroot_qemu_path.chmod(0o755)
        except Exception as e:
            logger.warning(f"Failed to copy {qemu_bin_name} to chroot: {e}")

    def mount_virtual_fs(self) -> None:
        """Mount virtual filesystems into the target chroot if running in real root mode."""
        if self.mode != "real":
            self.is_mounted = True
            return

        if os.geteuid() != 0:
            logger.debug("[Chroot] Running unprivileged; skipping host kernel mount_virtual_fs.")
            self.is_mounted = True
            return

        if self.is_mounted:
            logger.info(f"[Chroot] Virtual filesystems already mounted at {self.target_root}.")
            return

        self._setup_qemu_static()

        # Bind-mount /proc, /sys, /dev from the host into the target chroot.
        # --rbind from the running host avoids creating new kernel namespaces
        # which hits 'move_mount() failed: No space left on device' when many
        # mounts are already active on the host.
        pseudo_mounts = [
            (Path("/proc"), self.target_root / "proc"),
            (Path("/sys"),  self.target_root / "sys"),
            (Path("/dev"),  self.target_root / "dev"),
        ]

        for host_src, target in pseudo_mounts:
            try:
                target.mkdir(parents=True, exist_ok=True)
                res = subprocess.run(
                    ["mount", "--rbind", str(host_src), str(target)],
                    capture_output=True, text=True,
                )
                if res.returncode != 0 and "already mounted" not in res.stderr:
                    logger.warning(f"Failed to rbind {host_src} at {target}: {res.stderr.strip()}")
                else:
                    subprocess.run(["mount", "--make-rslave", str(target)], capture_output=True)
            except Exception as e:
                logger.warning(f"Failed to bind-mount {host_src} at {target}: {e}")

        if self.cache_dir:
            dnf_cache_host = self.cache_dir / "dnf"
            dnf_cache_target = self.target_root / "var" / "cache" / "dnf"
            try:
                dnf_cache_host.mkdir(parents=True, exist_ok=True)
                dnf_cache_target.mkdir(parents=True, exist_ok=True)
                subprocess.run(["mount", "--bind", str(dnf_cache_host), str(dnf_cache_target)], capture_output=True)
            except Exception as e:
                logger.warning(f"Failed to bind-mount cache directory: {e}")

        self.is_mounted = True

    def umount_virtual_fs(self) -> None:
        """Unmount virtual filesystems safely."""
        if self.mode != "real":
            self.is_mounted = False
            return

        if os.geteuid() != 0:
            self.is_mounted = False
            return

        logger.info(f"Unmounting virtual filesystems from target root: {self.target_root}")

        # Unmount DNF cache bind-mount
        dnf_cache = self.target_root / "var" / "cache" / "dnf"
        if dnf_cache.exists():
            subprocess.run(["umount", "-l", "-f", str(dnf_cache)], capture_output=True)

        # Recursively unmount rbind pseudo-filesystems
        for pseudo in ["dev", "sys", "proc"]:
            target = self.target_root / pseudo
            if target.exists():
                subprocess.run(["umount", "-R", "-l", str(target)], capture_output=True)

        self.is_mounted = False

    def run_in_chroot(
        self,
        command: List[str] | str,
        env: Optional[Dict[str, str]] = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        """Run a command inside the target chroot using CommandRunner or Toolchain."""
        if self.mode == "mock":
            logger.info(f"[Chroot] [MOCK] Command inside chroot: {command}")
            return subprocess.CompletedProcess(command if isinstance(command, list) else [command], 0)

        # Guarantee standard Linux PATH inside chroot execution
        chroot_env = {"PATH": "/usr/bin:/usr/sbin:/bin:/sbin"}
        if env:
            chroot_env.update(env)

        if self.toolchain:
            # Use build_host's chroot binary to enter target_root, keeping full isolation.
            # The target_root path must be visible inside build_host (it is bind-mounted there).
            if isinstance(command, str):
                chroot_cmd = ["chroot", str(self.target_root), "/bin/sh", "-c", command]
            else:
                chroot_cmd = ["chroot", str(self.target_root)] + list(command)
            return self.toolchain.run_in_build_host(chroot_cmd, env=chroot_env, check=check)

        return CommandRunner.run_chroot_stream(
            chroot_path=str(self.target_root),
            command=command,
            env=chroot_env,
            mode=self.mode
        )

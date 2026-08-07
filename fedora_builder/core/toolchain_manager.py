import os
import shutil
import subprocess
from pathlib import Path
import logging

logger = logging.getLogger("toolchain_manager")

class ToolchainManagerError(Exception):
    pass

class ToolchainManager:
    def __init__(self, workdir_base: Path, mode: str = "mock", force_isolated: bool = False):
        self.workdir_base = Path(workdir_base)
        self.mode = mode.lower()
        self.force_isolated = force_isolated
        self._is_fedora = self._detect_host_distro() == "fedora"

    @property
    def is_fedora_host(self) -> bool:
        return self._is_fedora

    @property
    def is_mounted(self) -> bool:
        return False

    def _detect_host_distro(self) -> str:
        try:
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("ID="):
                        return line.strip().split("=")[1].strip('"').lower()
        except Exception:
            pass
        return "unknown"

    def _has_dnf(self) -> bool:
        return shutil.which("dnf") is not None

    def _has_xorriso(self) -> bool:
        return shutil.which("xorriso") is not None

    def _has_mksquashfs(self) -> bool:
        return shutil.which("mksquashfs") is not None

    def check_host_tools(self) -> bool:
        if self.mode == "mock":
            return True
        missing = []
        for tool in ["dnf", "xorriso", "mksquashfs", "grub2-mkrescue"]:
            if shutil.which(tool) is None:
                missing.append(tool)
        if missing:
            logger.warning(f"Missing host tools: {missing}")
            return False
        return True

    def setup(self):
        if self.mode == "mock":
            return
        if self.is_fedora_host and self.check_host_tools() and not self.force_isolated:
            logger.info("Using host tools.")
        else:
            logger.warning("Podman/Docker bootstrap not fully implemented. Please run on Fedora with tools installed.")

    def get_dnf_binary(self) -> str:
        return shutil.which("dnf") or "dnf"

    def run_command(self, command, chroot_path=None, **kwargs):
        if self.mode == "mock":
            logger.info(f"[MOCK] Running: {command}")
            return subprocess.CompletedProcess(command, 0, b"", b"")
        if chroot_path:
            cmd = ["chroot", str(chroot_path)] + (command if isinstance(command, list) else command.split())
        else:
            cmd = command
        return subprocess.run(cmd, **kwargs)

    def cleanup(self):
        pass

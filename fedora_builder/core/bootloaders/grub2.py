import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

class Grub2Bootloader:
    def __init__(self, config: Dict[str, Any], arch: str, iso_uuid: str = "", toolchain=None):
        self.config = config
        self.arch = arch
        self.iso_uuid = iso_uuid
        self.toolchain = toolchain  # ToolchainManager instance for isolated execution

    def generate_grub_cfg(self, kernel_name: str, initramfs_name: str, iso_label: str, kernel_params: str) -> str:
        cfg = "set default=0\nset timeout=5\n\n"
        cfg += "menuentry 'Start Fedora Live' {\n"
        cfg += f"    linux /images/pxeboot/{kernel_name} root=live:CDLABEL={iso_label} rd.live.image {kernel_params}\n"
        cfg += f"    initrd /images/pxeboot/{initramfs_name}\n"
        cfg += "}\n"
        return cfg

    def generate_isolinux_cfg(self, kernel_name: str, initramfs_name: str, iso_label: str, kernel_params: str) -> str:
        cfg = "default vesamenu.c32\ntimeout 50\n\n"
        cfg += "label linux\n"
        cfg += "  menu label Start Fedora Live\n"
        cfg += f"  kernel /images/pxeboot/{kernel_name}\n"
        cfg += f"  append initrd=/images/pxeboot/{initramfs_name} root=live:CDLABEL={iso_label} rd.live.image {kernel_params}\n"
        return cfg

    def generate_efiboot_img(self, iso_staging: Path, effective_root: Path) -> Path:
        img_path = iso_staging / "images" / "efiboot.img"
        img_path.parent.mkdir(parents=True, exist_ok=True)

        if self.toolchain and self.toolchain.mode != "mock":
            # Run mkfs.fat/mformat inside the isolated build_host where dosfstools is installed
            try:
                self.toolchain.run_in_build_host(
                    ["dd", "if=/dev/zero", f"of={img_path}", "bs=1M", "count=10"],
                    check=True,
                )
                self.toolchain.run_in_build_host(
                    ["mkfs.fat", "-F", "12", "-n", "EFI", str(img_path)],
                    check=True,
                )
                return img_path
            except Exception as e:
                import logging
                logging.getLogger("grub2").warning(f"mkfs.fat in build_host failed ({e}), creating empty efiboot.img placeholder.")
                img_path.touch()
                return img_path

        # mock mode or no toolchain — create placeholder
        img_path.touch()
        return img_path

    def prepare_files(self, iso_staging: Path, rootfs: Path):
        boot_dir = iso_staging / "boot" / "grub2"
        boot_dir.mkdir(parents=True, exist_ok=True)

import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any

class Grub2Bootloader:
    def __init__(self, config: Dict[str, Any], arch: str, iso_uuid: str = ""):
        self.config = config
        self.arch = arch
        self.iso_uuid = iso_uuid

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
        if not shutil.which("mkfs.fat"):
            img_path.parent.mkdir(parents=True, exist_ok=True)
            img_path.touch()
            return img_path
            
        img_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["dd", "if=/dev/zero", f"of={img_path}", "bs=1M", "count=10"])
        subprocess.run(["mkfs.fat", "-F", "12", "-n", "EFI", str(img_path)])
        return img_path

    def prepare_files(self, iso_staging: Path, rootfs: Path):
        boot_dir = iso_staging / "boot" / "grub2"
        boot_dir.mkdir(parents=True, exist_ok=True)

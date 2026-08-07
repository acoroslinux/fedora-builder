import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, Tuple, Any
import logging
from fedora_builder.core.toolchain_manager import ToolchainManager
from fedora_builder.core.bootloaders.grub2 import Grub2Bootloader
from fedora_builder.core.disk_engine import DiskEngine

logger = logging.getLogger("iso_engine")

class ISOEngineError(Exception):
    pass

class ISOEngine:
    def __init__(self, workdir: Path, target_root: Path, output_name: str, config: Dict[str, Any], mode: str, toolchain: ToolchainManager):
        self.workdir = Path(workdir)
        self.target_root = Path(target_root)
        self.output_name = output_name
        self.config = config
        self.mode = mode
        self.toolchain = toolchain
        self.iso_staging = self.workdir / "iso_root"

    def _get_iso_label(self) -> str:
        # Support both flat key and nested system.iso_label
        if "iso_label" in self.config:
            return self.config["iso_label"]
        return self.config.get("system", {}).get("iso_label", "FEDORA-LIVE")

    def _get_kernel_params(self) -> str:
        # Support both flat key and nested boot.kernel_params
        if "kernel_params" in self.config:
            base = self.config["kernel_params"]
        else:
            base = self.config.get("boot", {}).get("kernel_params", "quiet rhgb")
        # Ensure rd.live.image is always present (required for Fedora LiveOS)
        if "rd.live.image" not in base:
            base = f"rd.live.image {base}"
        return base

    def _find_kernel_and_initramfs(self) -> Tuple[str, str]:
        boot_dir = self.target_root / "boot"
        kernel = None
        initramfs = None
        if boot_dir.exists():
            for f in boot_dir.iterdir():
                if f.name.startswith("vmlinuz") and not f.name.endswith(".rescue"):
                    kernel = f.name
                elif f.name.startswith("initramfs") and not f.name.endswith(".rescue") and f.name.endswith(".img"):
                    initramfs = f.name
        
        if self.mode == "mock" and not kernel:
            kernel = "vmlinuz"
            initramfs = "initrd.img"
            
        return kernel or "vmlinuz", initramfs or "initrd.img"

    def _create_squashfs(self, source_dir: Path, output_path: Path):
        if self.mode == "mock":
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.touch()
            return
            
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            output_path.unlink()
            
        cmd = ["mksquashfs", str(source_dir), str(output_path), "-comp", "zstd", "-b", "1M"]
        subprocess.run(cmd, check=True)

    def _create_discinfo(self, iso_staging: Path):
        with open(iso_staging / ".discinfo", "w") as f:
            f.write(f"{time.time()}\n{self.config.get('releasever', '41')}\n{self.config.get('basearch', 'x86_64')}\n")

    def _create_treeinfo(self, iso_staging: Path):
        with open(iso_staging / ".treeinfo", "w") as f:
            f.write("[general]\nfamily = Fedora\n")

    def _generate_checksums(self, iso_file: Path):
        if self.mode == "mock":
            return
        subprocess.run(["sha256sum", str(iso_file)], check=True)

    def _clean_rootfs(self, rootfs: Path):
        if self.mode == "mock":
            return
        paths_to_clean = ["var/cache/dnf", "usr/share/doc", "usr/share/man"]
        for p in paths_to_clean:
            target = rootfs / p
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)

    def build_iso(self) -> Path:
        self.iso_staging.mkdir(parents=True, exist_ok=True)
        
        (self.iso_staging / "images" / "pxeboot").mkdir(parents=True, exist_ok=True)
        (self.iso_staging / "LiveOS").mkdir(parents=True, exist_ok=True)
        (self.iso_staging / "isolinux").mkdir(parents=True, exist_ok=True)
        (self.iso_staging / "boot" / "grub2").mkdir(parents=True, exist_ok=True)
        
        kernel, initramfs = self._find_kernel_and_initramfs()
        
        if self.mode != "mock":
            shutil.copy2(self.target_root / "boot" / kernel, self.iso_staging / "images" / "pxeboot" / kernel)
            shutil.copy2(self.target_root / "boot" / initramfs, self.iso_staging / "images" / "pxeboot" / initramfs)
            
        self._clean_rootfs(self.target_root)
        squashfs_path = self.iso_staging / "LiveOS" / "squashfs.img"
        self._create_squashfs(self.target_root, squashfs_path)
        
        grub = Grub2Bootloader(self.config, self.config.get("basearch", "x86_64"))
        iso_label = self._get_iso_label()
        kernel_params = self._get_kernel_params()
        
        with open(self.iso_staging / "boot" / "grub2" / "grub.cfg", "w") as f:
            f.write(grub.generate_grub_cfg(kernel, initramfs, iso_label, kernel_params))
            
        with open(self.iso_staging / "isolinux" / "isolinux.cfg", "w") as f:
            f.write(grub.generate_isolinux_cfg(kernel, initramfs, iso_label, kernel_params))
            
        grub.generate_efiboot_img(self.iso_staging, self.target_root)
        
        self._create_discinfo(self.iso_staging)
        self._create_treeinfo(self.iso_staging)
        
        iso_path = Path(f"output/{self.output_name}.iso")
        iso_path.parent.mkdir(parents=True, exist_ok=True)
        
        if self.mode == "mock":
            iso_path.touch()
        else:
            cmd = ["xorriso", "-as", "mkisofs", "-V", iso_label, "-o", str(iso_path), str(self.iso_staging)]
            subprocess.run(cmd, check=True)
            self._generate_checksums(iso_path)
            
        return iso_path

    def build_tarball(self) -> Path:
        out_path = Path(f"output/{self.output_name}.tar.xz")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if self.mode == "mock":
            out_path.touch()
            return out_path
            
        subprocess.run(["tar", "-cJf", str(out_path), "-C", str(self.target_root), "."], check=True)
        return out_path

    def build_disk_image(self) -> Path:
        engine = DiskEngine(self.workdir, self.target_root, self.output_name, self.config, self.mode)
        return engine.build_disk_image()

    def build_container(self) -> Path:
        out_path = Path(f"output/{self.output_name}-container.tar")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if self.mode == "mock":
            out_path.touch()
            return out_path
        subprocess.run(["tar", "-cf", str(out_path), "-C", str(self.target_root), "."], check=True)
        return out_path

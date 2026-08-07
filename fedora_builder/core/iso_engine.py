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
            for f in sorted(boot_dir.iterdir()):
                if f.is_file():
                    if f.name.startswith("vmlinuz") and not f.name.endswith(".rescue"):
                        kernel = f.name
                    elif (f.name.startswith("initramfs") or f.name.startswith("initrd")) and not f.name.endswith(".rescue") and f.name.endswith(".img"):
                        initramfs = f.name

        if not kernel or not initramfs:
            logger.warning(
                f"Kernel or initramfs missing in {boot_dir} (kernel={kernel}, initramfs={initramfs}). "
                "Ensuring fallback paths for bootloader preparation."
            )

        return kernel or "vmlinuz", initramfs or "initramfs.img"

    def _create_squashfs(self, source_dir: Path, output_path: Path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.mode == "mock":
            output_path.touch()
            return

        if output_path.exists():
            output_path.unlink()

        compression = self.config.get("compression", "zstd")
        num_cpus = os.cpu_count() or 4
        # Ensure build_host isolated chroot also sees the target directory
        if hasattr(self.toolchain, "build_host_dir") and self.toolchain.build_host_dir:
            rel_parent = output_path.parent.relative_to(self.workdir) if output_path.is_relative_to(self.workdir) else None
            if rel_parent:
                (self.toolchain.build_host_dir / "workdir" / rel_parent).mkdir(parents=True, exist_ok=True)

        logger.info(f"Creating SquashFS with {compression} compression using {num_cpus} CPU cores...")
        self.toolchain.run_tool(
            "mksquashfs",
            [
                str(source_dir),
                str(output_path),
                "-comp", compression,
                "-b", "1M",
                "-processors", str(num_cpus),
                "-noappend",
                "-e", "proc", "sys", "dev", "tmp", "var/cache/dnf"
            ],
        )

    def _create_discinfo(self, iso_staging: Path):
        with open(iso_staging / ".discinfo", "w") as f:
            f.write(f"{time.time()}\n{self.config.get('releasever', '41')}\n{self.config.get('basearch', 'x86_64')}\n")

    def _create_treeinfo(self, iso_staging: Path):
        with open(iso_staging / ".treeinfo", "w") as f:
            f.write("[general]\nfamily = Fedora\n")

    def _generate_checksums(self, iso_file: Path):
        if self.mode == "mock":
            return
        import hashlib
        # Generate SHA256 and MD5 checksums using Python (no host tools needed)
        sha256 = hashlib.sha256()
        md5    = hashlib.md5()
        with open(iso_file, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                sha256.update(chunk)
                md5.update(chunk)
        sha256_path = iso_file.with_suffix(".sha256")
        md5_path    = iso_file.with_suffix(".md5")
        sha256_path.write_text(f"{sha256.hexdigest()}  {iso_file.name}\n")
        md5_path.write_text(f"{md5.hexdigest()}  {iso_file.name}\n")
        logger.info(f"SHA256: {sha256.hexdigest()}")
        logger.info(f"MD5:    {md5.hexdigest()}")

    def _clean_rootfs(self, rootfs: Path):
        if self.mode == "mock":
            return

        logger.info("Cleaning target rootfs prior to SquashFS compression...")

        # Directories and paths to wipe completely
        paths_to_clean = [
            "var/cache/dnf",
            "var/cache/yum",
            "var/cache/rpm",
            "var/lib/dnf/history*",
            "var/log",
            "var/tmp",
            "tmp",
            "usr/share/doc",
            "usr/share/info",
            "usr/share/man",
            "usr/share/gnome/help",
        ]

        for p in paths_to_clean:
            target = rootfs / p
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.unlink(missing_ok=True)

        # Reset machine-id so systemd generates a fresh unique ID at first boot
        machine_id = rootfs / "etc" / "machine-id"
        if machine_id.exists():
            try:
                machine_id.write_text("")
            except Exception:
                pass

        # Reset random-seed
        random_seed = rootfs / "var" / "lib" / "systemd" / "random-seed"
        if random_seed.exists():
            try:
                random_seed.unlink(missing_ok=True)
            except Exception:
                pass

    def build_iso(self) -> Path:
        self.iso_staging.mkdir(parents=True, exist_ok=True)
        
        (self.iso_staging / "images" / "pxeboot").mkdir(parents=True, exist_ok=True)
        (self.iso_staging / "LiveOS").mkdir(parents=True, exist_ok=True)
        (self.iso_staging / "isolinux").mkdir(parents=True, exist_ok=True)
        (self.iso_staging / "boot" / "grub2").mkdir(parents=True, exist_ok=True)
        
        kernel, initramfs = self._find_kernel_and_initramfs()
        
        if self.mode != "mock":
            src_kernel = self.target_root / "boot" / kernel
            src_initramfs = self.target_root / "boot" / initramfs
            pxeboot_dir = self.iso_staging / "images" / "pxeboot"
            pxeboot_dir.mkdir(parents=True, exist_ok=True)

            if src_kernel.exists():
                shutil.copy2(src_kernel, pxeboot_dir / kernel)
                # Also create symlink or copy as vmlinuz for standard loader paths
                if kernel != "vmlinuz":
                    shutil.copy2(src_kernel, pxeboot_dir / "vmlinuz")
            else:
                logger.warning(f"Kernel file {src_kernel} not found in rootfs boot directory. Creating placeholder.")
                (pxeboot_dir / "vmlinuz").touch()

            if src_initramfs.exists():
                shutil.copy2(src_initramfs, pxeboot_dir / initramfs)
                if initramfs != "initrd.img":
                    shutil.copy2(src_initramfs, pxeboot_dir / "initrd.img")
            else:
                logger.warning(f"Initramfs file {src_initramfs} not found in rootfs boot directory. Creating placeholder.")
                (pxeboot_dir / "initrd.img").touch()
            
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
        
        from fedora_builder.core.path_utils import resolve_from_project
        iso_path = resolve_from_project(f"output/{self.output_name}.iso")
        iso_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Ensure build_host also sees the output directory
        if hasattr(self.toolchain, "build_host_dir") and self.toolchain.build_host_dir:
            project_root = self.workdir.parent.parent
            output_in_build_host = self.toolchain.build_host_dir / project_root.relative_to("/") / "output"
            output_in_build_host.mkdir(parents=True, exist_ok=True)
            (self.toolchain.build_host_dir / "workdir" / "output").mkdir(parents=True, exist_ok=True)

        if self.mode == "mock":
            iso_path.touch()
        else:
            # xorriso runs inside build_host via toolchain.run_tool() — full isolation
            self.toolchain.run_tool(
                "xorriso",
                [
                    "-as", "mkisofs",
                    "-V", iso_label,
                    "-rock",
                    "-joliet",
                    # BIOS El Torito boot
                    "-eltorito-boot", "isolinux/isolinux.bin",
                    "-eltorito-catalog", "isolinux/boot.cat",
                    "-no-emul-boot",
                    "-boot-load-size", "4",
                    "-boot-info-table",
                    # UEFI boot
                    "-eltorito-alt-boot",
                    "-e", "images/efiboot.img",
                    "-no-emul-boot",
                    # Output
                    "-o", str(iso_path),
                    str(self.iso_staging),
                ]
            )
            self._generate_checksums(iso_path)
            
        return iso_path

    def build_tarball(self) -> Path:
        out_path = Path(f"output/{self.output_name}.tar.xz")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if self.mode == "mock":
            out_path.touch()
            return out_path
            
        env = os.environ.copy()
        env["XZ_OPT"] = f"-T0 -{self.config.get('compression_level', '6')}"
        subprocess.run(["tar", "-cJf", str(out_path), "-C", str(self.target_root), "."], env=env, check=True)
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

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
from fedora_builder.core.path_utils import resolve_from_project

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
            label = self.config["iso_label"]
        else:
            label = self.config.get("system", {}).get("iso_label", "FEDORA-MODERN")
        # ISO 9660 silently uppercases volume labels; keep them consistent so
        # CDLABEL= in kernel cmdline matches the actual on-disc label dracut finds.
        return label.upper()

    def _get_kernel_params(self) -> str:
        # Support both flat key and nested boot.kernel_params
        if "kernel_params" in self.config:
            base = self.config["kernel_params"]
        else:
            base = self.config.get("boot", {}).get("kernel_params", "quiet rhgb")
        # Ensure rd.live.image is always present (required for Fedora LiveOS).
        # Strip any existing occurrence first to avoid duplicates.
        parts = [p for p in base.split() if p != "rd.live.image"]
        return "rd.live.image " + " ".join(parts)

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
            try:
                output_path.touch()
            except PermissionError:
                pass
            return

        if output_path.exists():
            try:
                output_path.unlink()
            except PermissionError:
                pass

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

    def _safe_write_file(self, path: Path, content: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(content)
        except PermissionError:
            if self.mode != "mock":
                raise

    def _create_discinfo(self, iso_staging: Path):
        self._safe_write_file(
            iso_staging / ".discinfo",
            f"{time.time()}\n{self.config.get('releasever', '41')}\n{self.config.get('basearch', 'x86_64')}\n"
        )

    def _create_treeinfo(self, iso_staging: Path):
        self._safe_write_file(
            iso_staging / ".treeinfo",
            "[general]\nfamily = Fedora\n"
        )

    def _generate_checksums(self, iso_file: Path):
        if self.mode == "mock":
            return
        import hashlib

        # Embed MD5 checksum inside the ISO using implantisomd5 so that
        # dracut's rd.live.check (media verification) works at boot time.
        # Without this the boot halts with "No checksum information available".
        try:
            self.toolchain.run_in_build_host(
                ["implantisomd5", str(iso_file)],
                check=True,
            )
            logger.info(f"Implanted ISO MD5 checksum into {iso_file.name}")
        except Exception as e:
            logger.warning(
                f"implantisomd5 not available ({e}). "
                "rd.live.check media verification will be disabled in grub menus."
            )

        # Generate external SHA256 and MD5 sidecar files for users to verify
        # the download integrity independently.
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

    def _copy_syslinux_binaries(self):
        syslinux_paths = [
            self.target_root / "usr" / "share" / "syslinux",
            self.target_root / "usr" / "lib" / "syslinux" / "bios",
            self.target_root / "usr" / "lib" / "syslinux",
            self.workdir / "build_host" / "usr" / "share" / "syslinux",
            self.workdir / "build_host" / "usr" / "lib" / "syslinux" / "bios",
            self.workdir / "build_host" / "usr" / "lib" / "syslinux",
            Path("/usr/share/syslinux"),
            Path("/usr/lib/syslinux/bios"),
            Path("/usr/lib/syslinux"),
        ]

        isolinux_target = self.iso_staging / "isolinux"
        isolinux_target.mkdir(parents=True, exist_ok=True)

        copied = False
        sys_files = ["isolinux.bin", "vesamenu.c32", "ldlinux.c32", "libcom32.c32", "libcom.c32", "libutil.c32", "chain.c32", "reboot.c32", "poweroff.c32"]
        for path in syslinux_paths:
            if path.exists():
                for sys_file in sys_files:
                    src_file = path / sys_file
                    if src_file.exists():
                        shutil.copy2(src_file, isolinux_target / sys_file)
                        copied = True
                if copied:
                    logger.info(f"Copied syslinux boot binaries from {path} into isolinux staging target")
                    break

        # Fallback placeholder so xorriso never fails if isolinux.bin is missing
        if not (isolinux_target / "isolinux.bin").exists() and self.mode != "mock":
            logger.warning("isolinux.bin not found in syslinux search paths. Creating fallback bootloader file for xorriso.")
            (isolinux_target / "isolinux.bin").touch()
            (isolinux_target / "boot.cat").touch()

    def _run_xorriso(self, primary_args: list[str], fallback_args: list[str] | None = None):
        try:
            self.toolchain.run_tool("xorriso", primary_args)
            return
        except Exception as exc:
            if fallback_args is None:
                raise
            logger.warning(
                "Hybrid xorriso layout failed (%s); retrying with classic BIOS/UEFI fallback.",
                exc,
            )
            self.toolchain.run_tool("xorriso", fallback_args)

    def build_iso(self) -> Path:
        self.iso_staging.mkdir(parents=True, exist_ok=True)

        bios_enabled = bool(self.config.get("bios_enabled", True))
        uefi_enabled = bool(self.config.get("uefi_enabled", True))

        for d in [
            self.iso_staging / "images" / "pxeboot",
            self.iso_staging / "LiveOS",
            self.iso_staging / "boot" / "grub2",
        ]:
            d.mkdir(parents=True, exist_ok=True)

        if bios_enabled:
            (self.iso_staging / "isolinux").mkdir(parents=True, exist_ok=True)

        kernel, initramfs = self._find_kernel_and_initramfs()

        if self.mode != "mock":
            src_kernel    = self.target_root / "boot" / kernel
            src_initramfs = self.target_root / "boot" / initramfs
            pxeboot_dir   = self.iso_staging / "images" / "pxeboot"
            pxeboot_dir.mkdir(parents=True, exist_ok=True)

            if src_kernel.exists():
                shutil.copy2(src_kernel, pxeboot_dir / kernel)
                if kernel != "vmlinuz":
                    shutil.copy2(src_kernel, pxeboot_dir / "vmlinuz")
            else:
                logger.warning(f"Kernel {src_kernel} not found — creating placeholder.")
                (pxeboot_dir / "vmlinuz").touch()

            if src_initramfs.exists():
                shutil.copy2(src_initramfs, pxeboot_dir / initramfs)
                if initramfs != "initrd.img":
                    shutil.copy2(src_initramfs, pxeboot_dir / "initrd.img")
            else:
                logger.warning(f"Initramfs {src_initramfs} not found — creating placeholder.")
                (pxeboot_dir / "initrd.img").touch()

        self._clean_rootfs(self.target_root)
        squashfs_path = self.iso_staging / "LiveOS" / "squashfs.img"
        self._create_squashfs(self.target_root, squashfs_path)

        grub = Grub2Bootloader(
            self.config,
            self.config.get("basearch", "x86_64"),
            toolchain=self.toolchain,
        )
        iso_label     = self._get_iso_label()
        kernel_params = self._get_kernel_params()

        # ---- BIOS grub.cfg + earlyboot.cfg + loopback.cfg ------------------
        if bios_enabled:
            config_template = resolve_from_project("configs/boot/templates/config.cfg.in")
            if config_template.exists():
                config_text = config_template.read_text()
                placeholders = grub._get_template_placeholders(iso_label, kernel_params)
                for k, v in placeholders.items():
                    config_text = config_text.replace(k, str(v))
                self._safe_write_file(self.iso_staging / "boot" / "grub2" / "config.cfg", config_text)
                self._safe_write_file(self.iso_staging / "boot" / "grub" / "config.cfg", config_text)

            self._safe_write_file(
                self.iso_staging / "boot" / "grub2" / "grub.cfg",
                grub.generate_grub_cfg(kernel, initramfs, iso_label, kernel_params)
            )
            self._safe_write_file(
                self.iso_staging / "boot" / "grub2" / "earlyboot.cfg",
                grub.generate_earlyboot_cfg(iso_label)
            )
            self._safe_write_file(
                self.iso_staging / "boot" / "grub2" / "loopback.cfg",
                grub.generate_loopback_cfg(kernel, initramfs, iso_label, kernel_params)
            )

        grub.prepare_files(self.iso_staging, self.target_root)

        # ---- GRUB2 BIOS modules (i386-pc) -----------------------------------
        if bios_enabled:
            grub._copy_grub_bios_modules(self.iso_staging, self.target_root)

        # ---- GRUB2 font in boot/grub2/fonts/ --------------------------------
        grub._copy_grub_font(self.iso_staging, self.target_root)

        # ---- mbrid (MBR ID stub used by some grub builds) -------------------
        if bios_enabled:
            mbrid_path = self.iso_staging / "boot" / "mbrid"
            import hashlib, struct
            mbrid_val = struct.pack('<I', hash(iso_label) & 0xFFFFFFFF)
            try:
                mbrid_path.write_bytes(mbrid_val)
            except Exception:
                pass

        # ---- syslinux for BIOS el-torito boot --------------------------------
        if bios_enabled:
            self._safe_write_file(
                self.iso_staging / "isolinux" / "isolinux.cfg",
                grub.generate_isolinux_cfg(kernel, initramfs, iso_label, kernel_params)
            )
            self._copy_syslinux_binaries()

        if uefi_enabled:
            grub.generate_efiboot_img(self.iso_staging, self.target_root)

        self._create_discinfo(self.iso_staging)
        self._create_treeinfo(self.iso_staging)

        iso_path = resolve_from_project(f"output/{self.output_name}.iso")
        iso_path.parent.mkdir(parents=True, exist_ok=True)

        if hasattr(self.toolchain, "build_host_dir") and self.toolchain.build_host_dir:
            project_root = self.workdir.parent.parent
            (self.toolchain.build_host_dir / project_root.relative_to("/") / "output").mkdir(parents=True, exist_ok=True)
            (self.toolchain.build_host_dir / "workdir" / "output").mkdir(parents=True, exist_ok=True)

        if self.mode == "mock":
            iso_path.touch()
        else:
            # --- Step 1: Generate images/eltorito.img via grub2-mkimage --------
            # This is the BIOS El Torito boot record (i386-pc-eltorito format).
            # lorax: grub2-mkimage -O i386-pc-eltorito -d usr/lib/grub/i386-pc
            #        -o images/eltorito.img -p /boot/grub2 iso9660 biosdisk
            eltorito_img = self.iso_staging / "images" / "eltorito.img"
            eltorito_img.parent.mkdir(parents=True, exist_ok=True)

            grub_i386_pc = self.target_root / "usr" / "lib" / "grub" / "i386-pc"
            if not grub_i386_pc.exists() and hasattr(self.toolchain, "build_host_dir"):
                grub_i386_pc = self.toolchain.build_host_dir / "usr" / "lib" / "grub" / "i386-pc"

            if bios_enabled and grub_i386_pc.exists():
                try:
                    self.toolchain.run_tool(
                        "grub2-mkimage",
                        [
                            "-O", "i386-pc-eltorito",
                            "-d", str(grub_i386_pc),
                            "-o", str(eltorito_img),
                            "-p", "/boot/grub2",
                            "iso9660", "biosdisk",
                        ]
                    )
                    logger.info(f"Generated BIOS El Torito image: {eltorito_img}")
                except Exception as e:
                    logger.warning(f"grub2-mkimage failed ({e}), falling back to isolinux for BIOS boot")
                    eltorito_img = None
            elif bios_enabled:
                logger.warning("grub2 i386-pc modules not found — skipping eltorito.img generation")
                eltorito_img = None
            else:
                eltorito_img = None

            # --- Step 2: Locate boot_hybrid.img (MBR for hybrid ISO) ----------
            mbr_candidates = [
                self.target_root / "usr" / "lib" / "grub" / "i386-pc" / "boot_hybrid.img",
            ]
            if hasattr(self.toolchain, "build_host_dir"):
                mbr_candidates.append(
                    self.toolchain.build_host_dir / "usr" / "lib" / "grub" / "i386-pc" / "boot_hybrid.img"
                )
            mbr_img = None
            for c in mbr_candidates:
                if c.exists() and c.stat().st_size > 0:
                    mbr_img = c
                    break

            # --- Step 3: Build ISO with xorrisofs ---------------------------------
            # Prefer the classic El Torito + EFI image layout for compatibility and
            # keep the hybrid GPT layout as a fallback only when required.
            if bios_enabled and uefi_enabled:
                classic_args = [
                    "-as", "mkisofs",
                    "-iso-level", "3",
                    "-V", iso_label,
                    "-rock",
                    "-joliet",
                    "-pad",
                    "-eltorito-boot", "isolinux/isolinux.bin",
                    "-eltorito-catalog", "isolinux/boot.cat",
                    "-no-emul-boot",
                    "-boot-load-size", "4",
                    "-boot-info-table",
                    "-eltorito-alt-boot",
                    "-e", "images/efiboot.img",
                    "-no-emul-boot",
                    "-o", str(iso_path),
                    str(self.iso_staging),
                ]
                hybrid_args = None
                if eltorito_img and eltorito_img.exists() and mbr_img:
                    hybrid_args = [
                        "-as", "mkisofs",
                        "-iso-level", "3",
                        "-V", iso_label,
                        "-rock",
                        "-joliet",
                        "-pad",
                        "--grub2-mbr", str(mbr_img),
                        "-partition_offset", "16",
                        "-appended_part_as_gpt",
                        "-append_partition", "2",
                        "C12A7328-F81F-11D2-BA4B-00A0C93EC93B",
                        str(self.iso_staging / "images" / "efiboot.img"),
                        "-iso_mbr_part_type", "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7",
                        "-c", "boot.cat",
                        "--boot-catalog-hide",
                        "-b", "images/eltorito.img",
                        "-no-emul-boot",
                        "-boot-load-size", "4",
                        "-boot-info-table",
                        "--grub2-boot-info",
                        "-eltorito-alt-boot",
                        "-e", "--interval:appended_partition_2:all::",
                        "-no-emul-boot",
                        "-graft-points",
                        f"images/pxeboot={self.iso_staging / 'images' / 'pxeboot'}",
                        f"LiveOS={self.iso_staging / 'LiveOS'}",
                        f"boot/grub2={self.iso_staging / 'boot' / 'grub2'}",
                        f"boot/grub2/i386-pc={grub_i386_pc}",
                        f"images/eltorito.img={eltorito_img}",
                        f"EFI/BOOT={self.iso_staging / 'EFI' / 'BOOT'}",
                        f"EFI/fedora={self.iso_staging / 'EFI' / 'fedora'}",
                        f"isolinux={self.iso_staging / 'isolinux'}",
                        "-o", str(iso_path),
                    ]
                self._run_xorriso(classic_args, hybrid_args)
            elif bios_enabled:
                logger.warning("Falling back to classic isolinux El Torito")
                
        offline_repo_dir = self.config.get("offline_repo_dir")
        if offline_repo_dir and __import__('pathlib').Path(offline_repo_dir).exists():
            target_repo = self.iso_staging / "repo"
            target_repo.parent.mkdir(parents=True, exist_ok=True)
            __import__('shutil').copytree(offline_repo_dir, target_repo, dirs_exist_ok=True)

        xorriso_args = [
                    "-as", "mkisofs",
                    "-iso-level", "3",
                    "-V", iso_label,
                    "-rock",
                    "-joliet",
                    "-eltorito-boot", "isolinux/isolinux.bin",
                    "-eltorito-catalog", "isolinux/boot.cat",
                    "-no-emul-boot",
                    "-boot-load-size", "4",
                    "-boot-info-table",
                    "-eltorito-alt-boot",
                    "-e", "images/efiboot.img",
                    "-no-emul-boot",
                    "-o", str(iso_path),
                    str(self.iso_staging),
                ]
                self.toolchain.run_tool("xorriso", xorriso_args)
            elif uefi_enabled:
                logger.info("UEFI-only build: generating ISO with EFI boot image only")
                
        offline_repo_dir = self.config.get("offline_repo_dir")
        if offline_repo_dir and __import__('pathlib').Path(offline_repo_dir).exists():
            target_repo = self.iso_staging / "repo"
            target_repo.parent.mkdir(parents=True, exist_ok=True)
            __import__('shutil').copytree(offline_repo_dir, target_repo, dirs_exist_ok=True)

        xorriso_args = [
                    "-as", "mkisofs",
                    "-iso-level", "3",
                    "-V", iso_label,
                    "-rock",
                    "-joliet",
                    "-eltorito-alt-boot",
                    "-e", "images/efiboot.img",
                    "-no-emul-boot",
                    "-o", str(iso_path),
                    str(self.iso_staging),
                ]
                self.toolchain.run_tool("xorriso", xorriso_args)
            else:
                logger.warning("No boot path enabled; creating empty ISO stub")
                
        offline_repo_dir = self.config.get("offline_repo_dir")
        if offline_repo_dir and __import__('pathlib').Path(offline_repo_dir).exists():
            target_repo = self.iso_staging / "repo"
            target_repo.parent.mkdir(parents=True, exist_ok=True)
            __import__('shutil').copytree(offline_repo_dir, target_repo, dirs_exist_ok=True)

        xorriso_args = [
                    "-as", "mkisofs",
                    "-iso-level", "3",
                    "-V", iso_label,
                    "-rock",
                    "-joliet",
                    "-o", str(iso_path),
                    str(self.iso_staging),
                ]
                self.toolchain.run_tool("xorriso", xorriso_args)

            self._generate_checksums(iso_path)
            
        return iso_path

    def build_tarball(self) -> Path:
        out_path = resolve_from_project(f"output/{self.output_name}.tar.xz")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if self.mode == "mock":
            out_path.touch()
            return out_path

        env = os.environ.copy()
        env["XZ_OPT"] = f"-T0 -{self.config.get('compression_level', '6')}"
        subprocess.run(["tar", "-cJf", str(out_path), "-C", str(self.target_root), "."], env=env, check=True)
        return out_path

    def build_disk_image(self, target_format: str = "img") -> Path:
        engine = DiskEngine(self.workdir, self.target_root, self.output_name, self.config, self.mode, self.toolchain)
        return engine.build_disk_image(target_format)

    def build_container(self) -> Path:
        out_path = resolve_from_project(f"output/{self.output_name}-container.tar")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if self.mode == "mock":
            out_path.touch()
            return out_path
        subprocess.run(["tar", "-cf", str(out_path), "-C", str(self.target_root), "."], check=True)
        return out_path

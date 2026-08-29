import logging
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional
from fedora_builder.core.path_utils import resolve_from_project

logger = logging.getLogger("grub2")


class Grub2Bootloader:
    def __init__(self, config: Dict[str, Any], arch: str, iso_uuid: str = "", toolchain=None):
        self.config = config
        self.arch = arch
        self.iso_uuid = iso_uuid
        self.toolchain = toolchain

    def _get_template_placeholders(self, iso_label: str, kernel_params: str) -> Dict[str, str]:
        variant = self.config.get("variant")
        installer = self.config.get("installer")
        desktop = str(self.config.get("desktop", "")).upper()
        distro = str(self.config.get("distro", "Fedora")).title()
        arch = self.arch
        keymap = self.config.get("keymap", "us")
        locale = self.config.get("locale", "en_US.UTF-8")
        live_user = self.config.get("live_user", "liveuser")
        if isinstance(live_user, dict):
            live_user = live_user.get("name", "liveuser")

        if variant == "server" and installer == "anaconda":
            boot_title = "Fedora Server Installer (text mode)"
            boot_cmdline = "inst.text"
        else:
            boot_title = f"{distro} Modern {desktop}".strip() if desktop else f"{distro} Modern"
            boot_cmdline = ""

        return {
            "@@VOL_ID@@": iso_label,
            "@@ISO_LABEL@@": iso_label,
            "@@BOOT_TITLE@@": boot_title,
            "@@DISTRO_NAME@@": f"{distro} Modern",
            "@@DESKTOP@@": desktop,
            "@@ARCH@@": arch,
            "@@KERNEL_PARAMS@@": kernel_params,
            "@@BOOT_CMDLINE@@": boot_cmdline,
            "@@KEYMAP@@": keymap,
            "@@LOCALE@@": locale,
            "@@LIVE_USER@@": live_user,
            "@@SPLASHIMAGE@@": "splash.png"
        }

    def _menu_labels(self) -> Dict[str, str]:
        variant = self.config.get("variant")
        installer = self.config.get("installer")
        if variant == "server" and installer == "anaconda":
            return {
                "start": "Start Fedora Server Installer (text mode)",
                "check": "Test this media & start Fedora Server Installer (text mode)",
            }
        return {
            "start": "Start Fedora Modern",
            "check": "Test this media & start Fedora Modern",
            "basic": "Start Fedora Modern in basic graphics mode",
        }

    def _background_config(self) -> str:
        return (
            "insmod png\n"
            "insmod jpeg\n"
            "if [ -f /boot/grub2/themes/fedora-modern/fedora-grub-bg.jpg ]; then\n"
            "  background_image /boot/grub2/themes/fedora-modern/fedora-grub-bg.jpg\n"
            "fi\n\n"
        )

    def _use_installer_text_mode(self) -> bool:
        return self.config.get("variant") == "server" and self.config.get("installer") == "anaconda"

    def _build_live_kernel_params(self, kernel_params: str, extra_params: Optional[list[str]] = None) -> str:
        parts = [p for p in kernel_params.split() if p and p != "rd.live.image"]
        tokens = ["rd.live.image"]
        if extra_params:
            tokens.extend(extra_params)
        tokens.extend(parts)
        return " ".join(tokens)

    def _graphics_setup(self, font_candidates=None, video_modules=None) -> str:
        font_candidates = font_candidates or ["/boot/grub2/fonts/unicode.pf2"]
        video_modules = video_modules or [
            "all_video",
            "vbe",
            "vga",
            "video_bochs",
            "video_cirrus",
        ]
        cfg  = "function load_video {\n"
        for module in video_modules:
            cfg += f"  insmod {module}\n"
        cfg += "}\n\n"
        cfg += "load_video\n"
        cfg += "insmod gfxterm\n"
        cfg += "set gfxmode=auto\n"
        for font_path in font_candidates:
            cfg += f"if [ -f {font_path} ]; then\n"
            cfg += f"  loadfont {font_path}\n"
            cfg += "fi\n"
        cfg += "terminal_output gfxterm\n\n"
        return cfg

    # -------------------------------------------------------------------------
    # grub.cfg for BIOS boot  (boot/grub2/grub.cfg)
    # -------------------------------------------------------------------------
    def generate_grub_cfg(self, kernel_name: str, initramfs_name: str,
                          iso_label: str, kernel_params: str) -> str:
        grub_template = resolve_from_project("configs/boot/templates/grub.cfg.in")
        if grub_template.exists():
            content = grub_template.read_text()
            placeholders = self._get_template_placeholders(iso_label, kernel_params)
            for k, v in placeholders.items():
                content = content.replace(k, str(v))
            return content

        root_param = f"root=live:CDLABEL={iso_label}"
        kernel_path = "/images/pxeboot/vmlinuz"
        initrd_path = "/images/pxeboot/initrd.img"
        labels = self._menu_labels()

        cfg  = "set default=0\n"
        cfg += "set timeout=5\n\n"
        cfg += f"search --no-floppy --set=root -l '{iso_label}'\n\n"
        cfg += self._graphics_setup()
        cfg += self._background_config()

        start_params = ["inst.text"] if self._use_installer_text_mode() else []
        cfg += f"menuentry '{labels['start']}' --class fedora --class gnu-linux --class gnu --class os {{\n"
        cfg += f"\tlinux {kernel_path} {root_param} {self._build_live_kernel_params(kernel_params, start_params)}\n"
        cfg += f"\tinitrd {initrd_path}\n"
        cfg += "}\n"

        cfg += f"menuentry '{labels['check']}' --class fedora --class gnu-linux --class gnu --class os {{\n"
        check_params = ["rd.live.check"]
        if self._use_installer_text_mode():
            check_params.append("inst.text")
        cfg += f"\tlinux {kernel_path} {root_param} {self._build_live_kernel_params(kernel_params, check_params)}\n"
        cfg += f"\tinitrd {initrd_path}\n"
        cfg += "}\n"
        if labels.get("basic"):
            cfg += "submenu 'Troubleshooting -->' {\n"
            cfg += f"\tmenuentry '{labels['basic']}' --class fedora --class gnu-linux --class gnu --class os {{\n"
            cfg += f"\t\tlinux {kernel_path} {root_param} rd.live.image nomodeset {kernel_params}\n"
            cfg += f"\t\tinitrd {initrd_path}\n"
            cfg += "\t}\n"
            cfg += "}\n"

        return cfg

    # -------------------------------------------------------------------------
    # earlyboot.cfg (boot/grub2/earlyboot.cfg)
    # -------------------------------------------------------------------------
    def generate_earlyboot_cfg(self, iso_label: str) -> str:
        return f"search --no-floppy --set=root -l '{iso_label}'\nset prefix=($root)/boot/grub2\n"

    # -------------------------------------------------------------------------
    # loopback.cfg (boot/grub2/loopback.cfg)
    # -------------------------------------------------------------------------
    def generate_loopback_cfg(self, kernel_name: str, initramfs_name: str,
                              iso_label: str, kernel_params: str) -> str:
        loopback_template = resolve_from_project("configs/boot/templates/loopback.cfg.in")
        if loopback_template.exists():
            content = loopback_template.read_text()
            placeholders = self._get_template_placeholders(iso_label, kernel_params)
            for k, v in placeholders.items():
                content = content.replace(k, str(v))
            return content

        root_param = f"root=live:CDLABEL={iso_label}"
        kernel_path = "/images/pxeboot/vmlinuz"
        initrd_path = "/images/pxeboot/initrd.img"
        labels = self._menu_labels()

        cfg  = "set gfxpayload=keep\ninsmod gzio\ninsmod part_gpt\ninsmod ext2\n\n"
        cfg += f"search --no-floppy --set=root -l '{iso_label}'\n\n"
        cfg += self._graphics_setup(video_modules=[
            "efi_gop",
            "efi_uga",
            "video_bochs",
            "video_cirrus",
            "all_video",
        ])
        cfg += self._background_config()
        start_params = ["inst.text"] if self._use_installer_text_mode() else []
        cfg += f"menuentry '{labels['start']}' {{\n"
        cfg += f"\tlinux {kernel_path} {root_param} {self._build_live_kernel_params(kernel_params, start_params)}\n"
        cfg += f"\tinitrd {initrd_path}\n"
        cfg += "}\n"
        return cfg

    # -------------------------------------------------------------------------
    # isolinux.cfg for BIOS legacy syslinux boot
    # -------------------------------------------------------------------------
    def generate_isolinux_cfg(self, kernel_name: str, initramfs_name: str,
                              iso_label: str, kernel_params: str) -> str:
        isolinux_template = resolve_from_project("configs/boot/templates/isolinux.cfg.in")
        if isolinux_template.exists():
            content = isolinux_template.read_text()
            placeholders = self._get_template_placeholders(iso_label, kernel_params)
            for k, v in placeholders.items():
                content = content.replace(k, str(v))
            return content

        root_param = f"root=live:CDLABEL={iso_label}"
        labels = self._menu_labels()
        cfg  = "default vesamenu.c32\n"
        cfg += "timeout 50\n\n"
        cfg += "label linux\n"
        cfg += f"  menu label {labels['start']}\n"
        cfg += "  kernel /images/pxeboot/vmlinuz\n"
        start_params = ["inst.text"] if self._use_installer_text_mode() else []
        cfg += f"  append initrd=/images/pxeboot/initrd.img {root_param} {self._build_live_kernel_params(kernel_params, start_params)}\n"
        cfg += "\nlabel check\n"
        cfg += f"  menu label {labels['check']}\n"
        cfg += "  kernel /images/pxeboot/vmlinuz\n"
        check_params = ["rd.live.check"]
        if self._use_installer_text_mode():
            check_params.append("inst.text")
        cfg += f"  append initrd=/images/pxeboot/initrd.img {root_param} {self._build_live_kernel_params(kernel_params, check_params)}\n"
        return cfg

    # -------------------------------------------------------------------------
    # EFI grub.cfg  (EFI/BOOT/grub.cfg  AND  EFI/fedora/grub.cfg)
    # -------------------------------------------------------------------------
    def generate_efi_grub_cfg(self, iso_label: str, kernel_params: str) -> str:
        grub_template = resolve_from_project("configs/boot/templates/grub.cfg.in")
        if grub_template.exists():
            content = grub_template.read_text()
            placeholders = self._get_template_placeholders(iso_label, kernel_params)
            for k, v in placeholders.items():
                content = content.replace(k, str(v))
            return content

        root_param = f"root=live:CDLABEL={iso_label}"
        kernel_path = "/images/pxeboot/vmlinuz"
        initrd_path = "/images/pxeboot/initrd.img"
        labels = self._menu_labels()

        cfg  = "set gfxpayload=keep\n"
        cfg += "insmod gzio\ninsmod part_gpt\ninsmod ext2\n\n"
        cfg += f"set timeout=60\n\n"
        cfg += f"search --no-floppy --set=root -l '{iso_label}'\n\n"
        cfg += self._graphics_setup([
            "/EFI/BOOT/fonts/unicode.pf2",
            "/boot/grub2/fonts/unicode.pf2",
        ], video_modules=[
            "efi_gop",
            "efi_uga",
            "video_bochs",
            "video_cirrus",
            "all_video",
        ])
        cfg += self._background_config()

        start_params = ["inst.text"] if self._use_installer_text_mode() else []
        cfg += f"menuentry '{labels['start']}' --class fedora --class gnu-linux --class gnu --class os {{\n"
        cfg += f"\tlinux {kernel_path} {root_param} {self._build_live_kernel_params(kernel_params, start_params)}\n"
        cfg += f"\tinitrd {initrd_path}\n"
        cfg += "}\n"

        cfg += f"menuentry '{labels['check']}' --class fedora --class gnu-linux --class gnu --class os {{\n"
        check_params = ["rd.live.check"]
        if self._use_installer_text_mode():
            check_params.append("inst.text")
        cfg += f"\tlinux {kernel_path} {root_param} {self._build_live_kernel_params(kernel_params, check_params)}\n"
        cfg += f"\tinitrd {initrd_path}\n"
        cfg += "}\n"

        if labels.get("basic"):
            cfg += "submenu 'Troubleshooting -->' {\n"
            cfg += f"\tmenuentry '{labels['basic']}' --class fedora --class gnu-linux --class gnu --class os {{\n"
            cfg += f"\t\tlinux {kernel_path} {root_param} rd.live.image nomodeset {kernel_params}\n"
            cfg += f"\t\tinitrd {initrd_path}\n"
            cfg += "\t}\n"
            cfg += "}\n"

        return cfg

    # -------------------------------------------------------------------------
    # Copy GRUB2 BIOS modules  (boot/grub2/i386-pc/)
    # -------------------------------------------------------------------------
    def _copy_grub_bios_modules(self, iso_staging: Path, rootfs: Path):
        """Copy all i386-pc GRUB2 modules and generate core.img for hybrid BIOS boot."""
        src_dirs = [
            rootfs / "usr" / "lib" / "grub" / "i386-pc",
        ]
        # Also check build_host chroot
        if self.toolchain and hasattr(self.toolchain, "build_host_dir") and self.toolchain.build_host_dir:
            src_dirs.append(self.toolchain.build_host_dir / "usr" / "lib" / "grub" / "i386-pc")

        grub_bios_dst = iso_staging / "boot" / "grub2" / "i386-pc"

        for src in src_dirs:
            if src.exists() and any(src.iterdir()):
                grub_bios_dst.mkdir(parents=True, exist_ok=True)
                for f in src.iterdir():
                    try:
                        shutil.copy2(f, grub_bios_dst / f.name)
                    except Exception as e:
                        logger.debug(f"Skipping {f.name}: {e}")
                logger.info(f"Copied GRUB2 BIOS modules from {src} → {grub_bios_dst}")
                return True

        logger.warning("GRUB2 i386-pc modules not found — BIOS boot will not work!")
        return False

    # -------------------------------------------------------------------------
    # Copy GRUB2 EFI modules (boot/grub2/x86_64-efi/) and generate core.efi
    # -------------------------------------------------------------------------
    def _copy_grub_efi_modules(self, iso_staging: Path, rootfs: Path):
        """Copy all x86_64-efi GRUB2 modules."""
        src_dirs = [
            rootfs / "usr" / "lib" / "grub" / "x86_64-efi",
        ]
        if self.toolchain and hasattr(self.toolchain, "build_host_dir") and self.toolchain.build_host_dir:
            src_dirs.append(self.toolchain.build_host_dir / "usr" / "lib" / "grub" / "x86_64-efi")

        grub_efi_dst = iso_staging / "boot" / "grub2" / "x86_64-efi"

        for src in src_dirs:
            if src.exists() and any(src.iterdir()):
                grub_efi_dst.mkdir(parents=True, exist_ok=True)
                for f in src.iterdir():
                    try:
                        shutil.copy2(f, grub_efi_dst / f.name)
                    except Exception as e:
                        logger.debug(f"Skipping {f.name}: {e}")
                logger.info(f"Copied GRUB2 EFI modules from {src} → {grub_efi_dst}")
                return True

        logger.warning("GRUB2 x86_64-efi modules not found")
        return False

    # -------------------------------------------------------------------------
    # Copy unicode font
    # -------------------------------------------------------------------------
    def _copy_grub_font(self, iso_staging: Path, rootfs: Path):
        font_dst = iso_staging / "boot" / "grub2" / "fonts"
        font_dst.mkdir(parents=True, exist_ok=True)
        search_paths = [
            rootfs / "usr" / "share" / "grub" / "unicode.pf2",
            rootfs / "usr" / "share" / "grub2" / "unicode.pf2",
        ]
        for p in search_paths:
            if p.exists():
                shutil.copy2(p, font_dst / "unicode.pf2")
                return

    # -------------------------------------------------------------------------
    # Locate EFI binaries from chroot
    # -------------------------------------------------------------------------
    def _find_efi_binaries(self, rootfs: Path) -> dict:
        """Find shimx64, grubx64, mmx64 (and i32 variants) in rootfs."""
        found = {
            "shim_x64": None, "grub_x64": None, "mm_x64": None,
            "gcd_x64": None,
            "shim_ia32": None, "grub_ia32": None, "mm_ia32": None,
            "gcd_ia32": None,
            "fb_x64": None, "fb_ia32": None,
        }
        efi_root = rootfs / "boot" / "efi" / "EFI"
        if not efi_root.exists():
            # Fallback: search entire rootfs
            efi_root = rootfs

        for p in efi_root.rglob("*.efi"):
            n = p.name.lower()
            if n in ("shimx64.efi", "shim.efi") and not found["shim_x64"]:
                found["shim_x64"] = p
            elif n == "shimia32.efi" and not found["shim_ia32"]:
                found["shim_ia32"] = p
            elif n == "grubx64.efi" and not found["grub_x64"]:
                found["grub_x64"] = p
            elif n == "grubia32.efi" and not found["grub_ia32"]:
                found["grub_ia32"] = p
            elif n == "mmx64.efi" and not found["mm_x64"]:
                found["mm_x64"] = p
            elif n == "mmia32.efi" and not found["mm_ia32"]:
                found["mm_ia32"] = p
            elif n == "gcdx64.efi" and not found["gcd_x64"]:
                found["gcd_x64"] = p
            elif n == "gcdia32.efi" and not found["gcd_ia32"]:
                found["gcd_ia32"] = p
            elif n in ("fbx64.efi", "fallback.efi") and not found["fb_x64"]:
                found["fb_x64"] = p
            elif n == "fbia32.efi" and not found["fb_ia32"]:
                found["fb_ia32"] = p

        return found

    # -------------------------------------------------------------------------
    # Build the complete EFI tree on the ISO:
    #   EFI/BOOT/   – standard UEFI boot location
    #   EFI/fedora/ – distribution specific (for Secure Boot)
    # Also produces images/efiboot.img (FAT image embedding EFI/BOOT)
    # -------------------------------------------------------------------------
    def generate_efiboot_img(self, iso_staging: Path, effective_root: Path) -> Path:
        iso_label = self.config.get("system", {}).get("iso_label", "FEDORA-MODERN")
        if "iso_label" in self.config:
            iso_label = self.config["iso_label"]
        # ISO 9660 uppercases volume labels; keep consistent with the on-disc label.
        iso_label = iso_label.upper()

        kernel_params = self.config.get("boot", {}).get("kernel_params", "quiet rhgb")
        if "kernel_params" in self.config:
            kernel_params = self.config["kernel_params"]
        # Strip duplicates and ensure rd.live.image is first
        parts = [p for p in kernel_params.split() if p != "rd.live.image"]
        kernel_params = "rd.live.image " + " ".join(parts)

        # ---- EFI/BOOT/ -------------------------------------------------------
        efi_boot_dir = iso_staging / "EFI" / "BOOT"
        efi_boot_dir.mkdir(parents=True, exist_ok=True)

        # ---- EFI/fedora/ (Fedora Secure Boot chain) -------------------------
        efi_fed_dir = iso_staging / "EFI" / "fedora"
        efi_fed_dir.mkdir(parents=True, exist_ok=True)

        secure_boot = bool(self.config.get("secure_boot", True))

        # ---- GRUB2 font in EFI -----------------------------------------------
        efi_fonts = efi_boot_dir / "fonts"
        efi_fonts.mkdir(parents=True, exist_ok=True)
        font_src = effective_root / "usr" / "share" / "grub" / "unicode.pf2"
        if font_src.exists():
            shutil.copy2(font_src, efi_fonts / "unicode.pf2")

        # ---- Locate EFI binaries --------------------------------------------
        efi_bins = self._find_efi_binaries(effective_root)

        # Copy to EFI/BOOT (standard names required by UEFI spec).
        # Secure Boot uses shim as BOOTX64.EFI. If Secure Boot is disabled,
        # the firmware should boot grubx64.efi directly as the default entry.
        grub_x64_src = efi_bins.get("gcd_x64") or efi_bins.get("grub_x64")
        if secure_boot and efi_bins["shim_x64"]:
            shutil.copy2(efi_bins["shim_x64"], efi_boot_dir / "BOOTX64.EFI")
        elif grub_x64_src:
            shutil.copy2(grub_x64_src, efi_boot_dir / "BOOTX64.EFI")
        if grub_x64_src:
            shutil.copy2(grub_x64_src, efi_boot_dir / "grubx64.efi")
        if efi_bins["mm_x64"]:
            shutil.copy2(efi_bins["mm_x64"], efi_boot_dir / "mmx64.efi")
        if efi_bins["fb_x64"]:
            shutil.copy2(efi_bins["fb_x64"], efi_boot_dir / "fbx64.efi")

        # 32-bit UEFI (tablets/older firmware)
        if secure_boot and efi_bins["shim_ia32"]:
            shutil.copy2(efi_bins["shim_ia32"], efi_boot_dir / "BOOTIA32.EFI")
        elif efi_bins.get("grub_ia32") or efi_bins.get("gcd_ia32"):
            grub_ia32_src = efi_bins.get("gcd_ia32") or efi_bins.get("grub_ia32")
            shutil.copy2(grub_ia32_src, efi_boot_dir / "BOOTIA32.EFI")
        
        grub_ia32_src = efi_bins.get("gcd_ia32") or efi_bins.get("grub_ia32")
        if grub_ia32_src:
            shutil.copy2(grub_ia32_src, efi_boot_dir / "grubia32.efi")
        if efi_bins["mm_ia32"]:
            shutil.copy2(efi_bins["mm_ia32"], efi_boot_dir / "mmia32.efi")
        if efi_bins["fb_ia32"]:
            shutil.copy2(efi_bins["fb_ia32"], efi_boot_dir / "fbia32.efi")

        # Copy to EFI/fedora/ (Fedora Secure Boot chain)
        for name, src in [
            ("shimx64.efi",  efi_bins["shim_x64"]),
            ("shim.efi",     efi_bins["shim_x64"]),
            ("grubx64.efi",  grub_x64_src),
            ("mmx64.efi",    efi_bins["mm_x64"]),
            ("gcdx64.efi",   efi_bins["gcd_x64"]),
            ("shimia32.efi", efi_bins["shim_ia32"]),
            ("grubia32.efi", grub_ia32_src),
            ("mmia32.efi",   efi_bins["mm_ia32"]),
            ("gcdia32.efi",  efi_bins["gcd_ia32"]),
        ]:
            if src:
                shutil.copy2(src, efi_fed_dir / name)

        # Fedora BOOTX64.CSV / BOOTIA32.CSV (optional, for some firmware)
        for csv_name in ("BOOTX64.CSV", "BOOTIA32.CSV"):
            src_csv_paths = [
                effective_root / "boot" / "efi" / "EFI" / "fedora" / csv_name,
            ]
            for c in src_csv_paths:
                if c.exists():
                    shutil.copy2(c, efi_fed_dir / csv_name)
                    break

        # ---- Write EFI grub.cfg everywhere it is needed ---------------------
        efi_cfg_content = self.generate_efi_grub_cfg(iso_label, kernel_params)
        (efi_boot_dir / "grub.cfg").write_text(efi_cfg_content)
        (efi_fed_dir  / "grub.cfg").write_text(efi_cfg_content)

        # ---- Generate efiboot.img (FAT image of EFI/BOOT/) ------------------
        img_path = iso_staging / "images" / "efiboot.img"
        img_path.parent.mkdir(parents=True, exist_ok=True)

        if self.toolchain and self.toolchain.mode != "mock":
            try:
                # 40 MB FAT image (increased from 20MB to fit all EFI binaries)
                self.toolchain.run_in_build_host(
                    ["dd", "if=/dev/zero", f"of={img_path}", "bs=1M", "count=40"],
                    check=True,
                )
                self.toolchain.run_in_build_host(
                    ["mkfs.fat", "-n", "EFI", str(img_path)],
                    check=True,
                )
                # Inject EFI/BOOT into FAT image via mcopy
                if hasattr(self.toolchain, "workdir_base"):
                    rel_img = img_path.relative_to(self.toolchain.workdir_base)
                    bh_img  = Path("/workdir") / rel_img
                    rel_efi = (iso_staging / "EFI").relative_to(self.toolchain.workdir_base)
                    bh_efi  = Path("/workdir") / rel_efi
                    self.toolchain.run_in_build_host(
                        ["mcopy", "-i", str(bh_img), "-s", str(bh_efi), "::/"],
                        check=True,
                    )
            except Exception as e:
                logger.warning(f"efiboot.img generation failed ({e}), creating placeholder.")
                img_path.touch()
        else:
            try:
                img_path.touch()
            except PermissionError:
                pass

        return img_path

    def prepare_files(self, iso_staging: Path, rootfs: Path):
        boot_dir = iso_staging / "boot" / "grub2"
        boot_dir.mkdir(parents=True, exist_ok=True)
        theme_src = rootfs / "boot" / "grub2" / "themes"
        theme_dst = boot_dir / "themes"
        if theme_src.exists():
            shutil.copytree(theme_src, theme_dst, dirs_exist_ok=True)

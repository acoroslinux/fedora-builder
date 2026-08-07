import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Dict, Any
from fedora_builder.core.chroot_manager import ChrootManager
from fedora_builder.core.toolchain_manager import ToolchainManager
from fedora_builder.core.dnf_manager import DNFManager
from fedora_builder.core.customizer import SystemCustomizer
from fedora_builder.core.iso_engine import ISOEngine
from fedora_builder.core.kickstart_manager import KickstartManager
from fedora_builder.core.path_utils import resolve_from_project
import logging

logger = logging.getLogger("orchestrator")

class BuildOrchestratorError(Exception):
    pass

class BuildOrchestrator:
    def __init__(
        self,
        arch: str = "x86_64",
        config_path: str = "configs/global_build.json",
        mode: str = "mock",
        clean: bool = True,
        release: Optional[str] = "fedora-41",
        desktop: Optional[str] = None,
        kernel: Optional[str] = "kernel",
        bootloader: Optional[str] = "grub2-hybrid",
        variant: Optional[str] = "live",
        package_profiles: Optional[List[str]] = None,
        service_profiles: Optional[List[str]] = None,
        repo_profiles: Optional[List[str]] = None,
        live_profile: Optional[str] = None,
        live_user: Optional[str] = None,
        live_groups: Optional[List[str]] = None,
        output_format: str = "iso",
        compression: str = "zstd",
        generate_manifest: bool = True,
        generate_kickstart: bool = False,
        with_calamares: bool = False,
        force_isolated_toolchain: bool = False,
        copr_repos: Optional[List[str]] = None,
        extra_repos: Optional[List[str]] = None,
    ):
        self.arch = arch
        self.config_path = config_path
        self.mode = mode
        self.clean = clean
        self.release = release
        self.desktop = desktop
        self.kernel = kernel
        self.bootloader = bootloader
        self.variant = variant
        self.package_profiles = package_profiles or []
        self.service_profiles = service_profiles or []
        self.repo_profiles = repo_profiles or []
        self.live_profile = live_profile
        self.live_user = live_user
        self.live_groups = live_groups or []
        self.output_format = output_format
        self.compression = compression
        self.generate_manifest = generate_manifest
        self.generate_kickstart = generate_kickstart
        self.with_calamares = with_calamares
        self.force_isolated_toolchain = force_isolated_toolchain
        self.copr_repos = copr_repos or []
        self.extra_repos = extra_repos or []
        
        # Use resolve_from_project to get absolute path regardless of CWD
        self.workdir = resolve_from_project(f"workdir/{self.arch}")
        self.target_root = self.workdir / "chroot"
        self.config = {"releasever": self.release.split("-")[-1] if self.release else "41", "basearch": self.arch}

    def _safe_clean_build_tree(self):
        # Preventively unmount any stale mountpoints before cleaning
        if self.mode != "mock" and os.geteuid() == 0:
            build_host = self.workdir / "build_host"
            subdirs = ["chroot", "iso_root", "output", "cache"]
            output_dir = resolve_from_project("output")

            stale_mounts = [
                # Target rootfs virtual filesystems
                self.target_root / "var" / "cache" / "dnf",
                self.target_root / "dev" / "shm",
                self.target_root / "dev" / "pts",
                self.target_root / "dev",
                self.target_root / "sys",
                self.target_root / "proc",
                # build_host virtual filesystems
                build_host / "dev" / "shm",
                build_host / "dev" / "pts",
                build_host / "dev",
                build_host / "sys",
                build_host / "proc",
            ]
            # build_host /workdir/<sub> bind-mounts
            for sub in subdirs:
                stale_mounts.append(build_host / "workdir" / sub)
            stale_mounts.append(build_host / "workdir")
            # Absolute-path mirrored bind-mounts inside build_host
            for sub in subdirs:
                host_sub = (self.workdir / sub) if sub != "output" else output_dir
                stale_mounts.append(build_host / host_sub.relative_to("/"))

            for mount_path in stale_mounts:
                if mount_path.exists():
                    subprocess.run(["umount", "-l", "-f", str(mount_path)], capture_output=True)

        try:
            if self.target_root.exists():
                shutil.rmtree(self.target_root, ignore_errors=True)
        except Exception:
            pass

        try:
            iso_root = self.workdir / "iso_root"
            if iso_root.exists():
                shutil.rmtree(iso_root, ignore_errors=True)
        except Exception:
            pass

    def validate(self) -> Dict[str, Any]:
        return {"valid": True, "errors": [], "summary": {}}

    def generate_kickstart_only(self, output_name: Optional[str] = None) -> Path:
        """Generate a Kickstart (.ks) file without performing a full build."""
        from fedora_builder.core.config_loader import ConfigLoader
        loader = ConfigLoader()
        config = loader.assemble_build_config(
            global_config_path=resolve_from_project(self.config_path),
            architecture=self.arch,
            release=self.release or "fedora-41",
            desktop=self.desktop,
            kernel=self.kernel,
            bootloader=self.bootloader,
            variant=self.variant,
            package_profiles=self.package_profiles,
            service_profiles=self.service_profiles,
            repo_profiles=self.repo_profiles,
            live_profile=self.live_profile,
        )
        ks_mgr = KickstartManager(config)
        name = output_name or f"fedora-{self.arch}"
        ks_path = resolve_from_project("output") / f"{name}.ks"
        ks_path.parent.mkdir(parents=True, exist_ok=True)
        ks_mgr.write(ks_path)
        return ks_path

    def build(self, output_name: Optional[str] = None) -> Path:
        if output_name:
            if output_name.endswith((".iso", ".img", ".tar.xz", ".tar")):
                output_name = Path(output_name).stem.replace(".tar", "")
            artifact_name = output_name
        else:
            artifact_name = f"fedora-{self.arch}"

        if self.clean:
            self._safe_clean_build_tree()
            
        toolchain = ToolchainManager(
            workdir_base=self.workdir,
            mode=self.mode,
            force_isolated=self.force_isolated_toolchain,
            target_arch=self.arch,
            releasever=self.config["releasever"],
        )
        toolchain.setup()
        
        cache_dir = self.workdir.parent / "cache"
        chroot = ChrootManager(self.target_root, self.mode, cache_dir=cache_dir, arch=self.arch)
        
        try:
            toolchain.mount_virtual_fs()
            chroot.mount_virtual_fs()

            dnf = DNFManager(chroot, self.config, toolchain=toolchain)
            dnf.bootstrap_rootfs(self.config["releasever"], self.config["basearch"])
            dnf.configure_repos(self.config.get("repos", []))
            
            packages = self.config.get("packages", [])
            groups = self.config.get("groups", [])
            dnf.install_all(packages, groups)
            
            dnf.configure_selinux(self.config.get("selinux_mode", "permissive"))
            
            customizer = SystemCustomizer(chroot, self.config)
            customizer.configure_live_environment()
            
            if self.mode != "mock":
                chroot.run_in_chroot(["dracut", "-f", "-N", "--add", "dmsquash-live"])
                
            if self.generate_kickstart:
                ks_mgr = KickstartManager(self.config)
                ks_path = Path("output") / f"fedora-{self.arch}.ks"
                ks_path.parent.mkdir(parents=True, exist_ok=True)
                ks_mgr.write(ks_path)
                
            # Unmount target rootfs virtual filesystems (proc, sys, dev, cache) before squashfs compression
            chroot.umount_virtual_fs()

            iso_engine = ISOEngine(
                self.workdir, self.target_root, artifact_name, 
                self.config, self.mode, toolchain
            )
            
            if self.output_format == "iso":
                artifact = iso_engine.build_iso()
            elif self.output_format == "img":
                artifact = iso_engine.build_disk_image()
            elif self.output_format == "tarball":
                artifact = iso_engine.build_tarball()
            elif self.output_format == "container":
                artifact = iso_engine.build_container()
            else:
                artifact = iso_engine.build_iso()
                
            return artifact
            
        finally:
            logger.info("Performing mandatory cleanup and unmounting all filesystems...")
            try:
                chroot.umount_virtual_fs()
            except Exception as e:
                logger.warning(f"Error unmounting target chroot: {e}")
            try:
                toolchain.umount_virtual_fs()
            except Exception as e:
                logger.warning(f"Error unmounting build_host toolchain: {e}")

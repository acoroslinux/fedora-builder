import shutil
from pathlib import Path
from typing import List, Optional, Dict, Any
from fedora_builder.core.chroot_manager import ChrootManager
from fedora_builder.core.toolchain_manager import ToolchainManager
from fedora_builder.core.dnf_manager import DNFManager
from fedora_builder.core.customizer import SystemCustomizer
from fedora_builder.core.iso_engine import ISOEngine
from fedora_builder.core.kickstart_manager import KickstartManager
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
        
        self.workdir = Path(f"workdir/{self.arch}").resolve()
        self.target_root = self.workdir / "chroot"
        self.config = {"releasever": self.release.split("-")[-1] if self.release else "41", "basearch": self.arch}

    def _safe_clean_build_tree(self):
        if self.target_root.exists():
            shutil.rmtree(self.target_root, ignore_errors=True)
        iso_root = self.workdir / "iso_root"
        if iso_root.exists():
            shutil.rmtree(iso_root, ignore_errors=True)

    def validate(self) -> Dict[str, Any]:
        return {"valid": True, "errors": [], "summary": {}}

    def build(self) -> Path:
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
        
        chroot = ChrootManager(self.target_root, self.mode, arch=self.arch)
        chroot.mount_virtual_fs()
        
        try:
            dnf = DNFManager(chroot, self.config)
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
                
            iso_engine = ISOEngine(
                self.workdir, self.target_root, f"fedora-{self.arch}", 
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
            chroot.umount_virtual_fs()

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
from fedora_builder.core.path_utils import resolve_from_project, unmount_all_under
from fedora_builder.core.cache_paths import package_cache_dir, resolve_cache_root
import logging

logger = logging.getLogger("orchestrator")

class BuildOrchestratorError(Exception):
    pass

class BuildOrchestrator:
    def __init__(
        self,
        arch: str = "x86_64",
        config_path: str = "configs/global_build.json",
        release: Optional[str] = None,
        desktop: Optional[str] = None,
        kernel: Optional[str] = None,
        bootloader: Optional[str] = None,
        variant: Optional[str] = None,
        package_profiles: Optional[List[str]] = None,
        service_profiles: Optional[List[str]] = None,
        repo_profiles: Optional[List[str]] = None,
        live_profile: Optional[str] = None,
        output_format: str = "iso",
        compression: str = "zstd",
        mode: str = "mock",
        clean: bool = True,
        copr_repos: Optional[List[str]] = None,
        extra_repos: Optional[List[str]] = None,
        live_user: Optional[str] = None,
        live_groups: Optional[List[str]] = None,
        generate_manifest: bool = True,
        generate_kickstart: bool = False,
        with_calamares: bool = False,
        multimedia_codecs: bool = False,
        with_flathub: bool = False,
        with_zram: bool = False,
        force_isolated_toolchain: bool = False,
    ):
        self.arch = arch
        self.config_path = config_path
        self.release = release
        self.desktop = desktop
        self.kernel = kernel
        self.bootloader = bootloader
        self.variant = variant
        self.package_profiles = package_profiles or []
        self.service_profiles = service_profiles or []
        self.repo_profiles = repo_profiles or []
        self.live_profile = live_profile
        self.compression = compression
        self.copr_repos = copr_repos or []
        self.extra_repos = extra_repos or []
        self.live_user = live_user
        self.live_groups = live_groups or []
        self.output_format = output_format
        self.mode = mode.lower()
        self.clean = clean
        self.generate_manifest = generate_manifest
        self.generate_kickstart = generate_kickstart
        self.with_calamares = with_calamares
        self.multimedia_codecs = multimedia_codecs
        self.with_flathub = with_flathub
        self.with_zram = with_zram
        self.force_isolated_toolchain = force_isolated_toolchain

        if self.variant == "server" and "anaconda" not in self.package_profiles:
            self.package_profiles.append("anaconda")

        if self.with_calamares and "installer" not in self.package_profiles:
            self.package_profiles.append("installer")

        if self.multimedia_codecs:
            if "rpmfusion-free" not in self.repo_profiles:
                self.repo_profiles.append("rpmfusion-free")
            if "rpmfusion-nonfree" not in self.repo_profiles:
                self.repo_profiles.append("rpmfusion-nonfree")
            if "multimedia" not in self.package_profiles:
                self.package_profiles.append("multimedia")

        self.workdir = resolve_from_project(f"workdir/{self.arch}")
        self.target_root = self.workdir / "chroot"
        self.config = {
            "releasever": self.release.split("-")[-1] if self.release else "41",
            "basearch": self.arch,
            "with_flathub": self.with_flathub,
            "with_zram": self.with_zram,
        }

    def _build_dracut_command(self, kver: Optional[str] = None) -> List[str]:
        dracut_cmd = ["dracut", "-f", "-N", "--nomdadmconf", "--nolvmconf"]
        if self.config.get("live_media", True):
            for module in [
                "livenet",
                "dmsquash-live",
                "dmsquash-live-ntfs",
                "convertfs",
                "pollcdrom",
                "qemu",
                "qemu-net",
            ]:
                dracut_cmd.extend(["--add", module])
        if kver:
            dracut_cmd.extend(["--kver", kver])
        return dracut_cmd

    def _safe_clean_build_tree(self):
        if self.mode != "mock" and os.geteuid() == 0:
            unmount_all_under(resolve_from_project("workdir"))

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

        from fedora_builder.core.config_loader import ConfigLoader
        loader = ConfigLoader()
        assembled_config = loader.assemble_build_config(
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
        self.config.update(assembled_config)
        cache_root = resolve_cache_root(self.config)
        dnf_cache_dir = package_cache_dir(
            self.config,
            self.config["releasever"],
            self.config["basearch"],
        )

        toolchain = ToolchainManager(
            workdir_base=self.workdir,
            mode=self.mode,
            force_isolated=self.force_isolated_toolchain,
            target_arch=self.arch,
            releasever=self.config["releasever"],
            cache_root=cache_root,
        )
        toolchain.setup()

        chroot = ChrootManager(
            self.target_root,
            self.mode,
            cache_dir=dnf_cache_dir,
            arch=self.arch,
            toolchain=toolchain,
        )

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
            customizer.configure_environment()
            if self.mode != "mock":
                kver = None
                modules_dir = self.target_root / "lib" / "modules"
                if modules_dir.exists():
                    versions = [d.name for d in modules_dir.iterdir() if d.is_dir()]
                    if versions:
                        kver = sorted(versions)[-1]

                dracut_cmd = self._build_dracut_command(kver=kver)
                if kver:
                    logger.info(f"Running Dracut initramfs generation for kernel version {kver}...")
                else:
                    logger.info("Running Dracut initramfs generation...")
                chroot.run_in_chroot(dracut_cmd)

            if self.generate_kickstart:
                ks_mgr = KickstartManager(self.config)
                ks_path = Path("output") / f"fedora-{self.arch}.ks"
                ks_path.parent.mkdir(parents=True, exist_ok=True)
                ks_mgr.write(ks_path)

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
            if self.generate_manifest and artifact and artifact.exists():
                self._generate_checksums(artifact)

            output_dir = resolve_from_project("output")
            self._fix_output_permissions(output_dir)

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

            if self.mode != "mock" and os.geteuid() == 0:
                unmount_all_under(resolve_from_project("workdir"))

            output_dir = resolve_from_project("output")
            self._fix_output_permissions(output_dir)

    def _fix_output_permissions(self, output_dir: Path):
        if not output_dir.exists():
            return
        sudo_uid = os.environ.get("SUDO_UID")
        sudo_gid = os.environ.get("SUDO_GID")
        if sudo_uid and sudo_gid:
            try:
                uid = int(sudo_uid)
                gid = int(sudo_gid)
                for root, dirs, files in os.walk(output_dir):
                    for d in dirs:
                        try:
                            os.chown(os.path.join(root, d), uid, gid)
                        except Exception:
                            pass
                    for f in files:
                        try:
                            os.chown(os.path.join(root, f), uid, gid)
                        except Exception:
                            pass
                os.chown(output_dir, uid, gid)
                logger.info(f"Updated ownership of {output_dir} to non-root user ({sudo_uid}:{sudo_gid})")
            except Exception as e:
                logger.warning(f"Could not update output ownership: {e}")

    def _generate_checksums(self, artifact_path: Path):
        if not artifact_path or not artifact_path.exists():
            return
        import hashlib
        logger.info(f"Generating checksums for artifact: {artifact_path.name}")
        sha256 = hashlib.sha256()
        md5 = hashlib.md5()
        with open(artifact_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha256.update(chunk)
                md5.update(chunk)

        sha256_path = artifact_path.with_name(f"{artifact_path.name}.sha256")
        md5_path = artifact_path.with_name(f"{artifact_path.name}.md5")

        sha256_path.write_text(f"{sha256.hexdigest()}  {artifact_path.name}\n")
        md5_path.write_text(f"{md5.hexdigest()}  {artifact_path.name}\n")

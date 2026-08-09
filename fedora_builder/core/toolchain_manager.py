"""
fedora_builder/core/toolchain_manager.py

Manages a fully isolated Fedora build_host chroot inside workdir/<arch>/build_host/.
This makes fedora-builder 100% host-distribution agnostic: it can build Fedora
ISOs from any Linux host (Arch, Debian, Ubuntu, Alpine, Gentoo, etc.) without
requiring DNF, xorriso, mksquashfs or any Fedora-specific tools to be installed
on the host itself.

Bootstrap strategy (in priority order):
  1. build_host/ already exists and is functional → reuse it
  2. Pull Fedora minimal OCI image from registry.fedoraproject.org and extract
     it as the build_host base, then install tools inside it via DNF
  3. Podman/Docker export (if available on host) as alternative OCI extraction
  4. --force-isolated-toolchain: always re-bootstrap even if host tools exist
  5. mock mode: simulate everything, no network or root required

The build_host chroot contains:
  - dnf, rpm, rpm-build               ← package management inside target chroot
  - xorriso                            ← ISO creation (El Torito BIOS + UEFI)
  - squashfs-tools (mksquashfs)        ← SquashFS rootfs compression
  - grub2-tools, grub2-efi-x64-modules ← GRUB2 bootloader
  - syslinux, syslinux-utils          ← Legacy BIOS isolinux boot
  - mtools, dosfstools                ← FAT EFI boot image (efiboot.img)
  - parted, e2fsprogs                 ← Disk image creation
  - shim-x64                         ← Secure Boot chain
  - qemu-user-static                  ← Cross-arch builds (aarch64, ppc64le, s390x)
  - curl, tar, xz, zstd              ← Download and compression utilities
"""

import json
import os
import platform
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fedora_builder.core.logger_setup import setup_logger
from fedora_builder.core.path_utils import resolve_from_project as _resolve_from_project

logger = setup_logger("toolchain_manager")

# ── Host architecture detection ──────────────────────────────────────────────────

_HOST_ARCH = platform.machine().lower()  # x86_64, aarch64, etc.

# Packages to install inside build_host to support all build operations
_BUILD_HOST_PACKAGES = [
    # Package management
    "dnf",
    "rpm",
    "rpm-build",
    "dnf-plugins-core",
    # ISO creation
    "xorriso",
    "libisoburn",
    # SquashFS
    "squashfs-tools",
    # GRUB2 (x86_64 only; aarch64/ppc64le handled via arch override)
    "grub2-common",
    "grub2-tools",
    "grub2-tools-extra",
    "grub2-efi-x64-modules",
    "grub2-pc-modules",
    # Secure Boot shim
    "shim-x64",
    # Legacy BIOS boot (ISOLINUX)
    "syslinux",
    # EFI FAT image tooling
    "mtools",
    "dosfstools",
    # Disk images
    "parted",
    "e2fsprogs",
    # QEMU for cross-arch
    "qemu-user-static",
    # Utilities
    "curl",
    "tar",
    "xz",
    "zstd",
    "findutils",
    "coreutils",
    "bash",
    "shadow-utils",
]

# Architecture-specific extra packages for build_host
_ARCH_EXTRA_PACKAGES: Dict[str, List[str]] = {
    "aarch64": ["grub2-efi-aa64-modules"],
    "ppc64le": ["grub2-ppc64le-modules"],
    "s390x":   ["s390utils"],
    "x86_64":  ["grub2-pc", "grub2-efi-x64", "grub2-efi-x64-modules"],
}

# ── Fedora OCI Image Registry ────────────────────────────────────────────────────
# Fedora publishes official minimal OCI container images. We can extract them
# without requiring podman/docker by using the OCI Distribution Spec APIs directly.

_FEDORA_OCI_REGISTRY   = "registry.fedoraproject.org"
_FEDORA_OCI_IMAGE      = "fedora-minimal"
_FEDORA_OCI_MANIFEST_URL_TEMPLATE = (
    "https://registry.fedoraproject.org/v2/fedora-minimal/manifests/{tag}"
)
_FEDORA_OCI_BLOB_URL_TEMPLATE = (
    "https://registry.fedoraproject.org/v2/fedora-minimal/blobs/{digest}"
)


class ToolchainManagerError(Exception):
    pass


class ToolchainManager:
    """
    Manages a fully isolated Fedora build_host chroot.
    The build_host is a minimal Fedora rootfs inside workdir/build_host/,
    bootstrapped from the official Fedora minimal OCI container image.
    All build tools (dnf, xorriso, mksquashfs, grub2, syslinux, mtools)
    run inside this chroot, making fedora-builder 100% host-distro agnostic.
    """

    def __init__(
        self,
        workdir_base: Path,
        mode: str = "mock",
        force_isolated: bool = False,
        target_arch: str = "x86_64",
        releasever: str = "41",
    ):
        self.workdir_base  = Path(workdir_base).resolve()
        self.mode          = mode.lower()
        self.force_isolated = force_isolated
        self.target_arch   = target_arch
        self.releasever    = releasever

        # The isolated Fedora chroot used for running build tools (sibling to arch workdir, preventing recursive mounts)
        self.build_host_dir = self.workdir_base.parent / "build_host"
        # Persistent download cache (shared between builds)
        self.cache_dir = self.workdir_base.parent / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.is_mounted: bool = False

    # ── Public Interface ─────────────────────────────────────────────────────────

    def setup(self) -> None:
        """
        Main entry point: prepare the build_host.
        In mock mode: creates minimal directory skeleton, no network access.
        In real mode: bootstraps or reuses the Fedora build_host chroot.
        """
        if self.mode == "mock":
            logger.info("[MOCK TOOLCHAIN] Simulating build_host setup.")
            self._create_mock_build_host()
            return

        if os.geteuid() != 0:
            raise ToolchainManagerError(
                "Real mode requires root privileges to set up the isolated build_host."
            )

        if self._build_host_ready() and not self.force_isolated:
            logger.info(
                f"Isolated build_host already exists at {self.build_host_dir}. "
                "Skipping bootstrap. Use --force-isolated-toolchain to rebuild."
            )
            return

        logger.info("─" * 60)
        logger.info("  Fedora-Builder: Bootstrapping isolated build_host")
        logger.info("─" * 60)
        logger.info(
            f"  build_host path : {self.build_host_dir}\n"
            f"  Fedora release  : {self.releasever}\n"
            f"  Target arch     : {self.target_arch}\n"
            f"  Host arch       : {_HOST_ARCH}"
        )

        # Bootstrap: extract Fedora minimal OCI image as build_host base
        self._bootstrap_from_oci()

        # Copy resolv.conf so DNF inside build_host can reach the network
        self._copy_resolv_conf()

        # Mount proc, sys, dev before installing packages so RPM scriptlets run cleanly
        self.mount_virtual_fs()

        # Install all required build tools inside the build_host
        self._install_build_tools()

        logger.info("✓ Isolated build_host ready.")

    def cleanup(self) -> None:
        """Unmount virtual filesystems if still mounted."""
        if self.is_mounted:
            self.umount_virtual_fs()

    def run_in_build_host(
        self,
        command: List[str] | str,
        env: Optional[Dict[str, str]] = None,
        check: bool = False,
    ) -> subprocess.CompletedProcess:
        """
        Execute a command inside the isolated build_host chroot.
        In mock mode, prints the command and returns a dummy result.
        """
        if isinstance(command, str):
            cmd_args = ["/bin/sh", "-c", command]
            cmd_str  = command
        else:
            cmd_args = command
            cmd_str  = " ".join(str(a) for a in command)

        if self.mode == "mock":
            logger.info(f"[MOCK BUILD_HOST] {cmd_str}")
            return subprocess.CompletedProcess(
                args=cmd_args, returncode=0,
                stdout="[MOCK TOOLCHAIN OUTPUT]", stderr=""
            )

        if os.geteuid() != 0:
            raise ToolchainManagerError(
                "Running commands inside build_host requires root privileges."
            )

        full_cmd = ["chroot", str(self.build_host_dir)] + cmd_args
        logger.info(f"[BUILD_HOST CHROOT] {cmd_str}")

        merged_env = {
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "HOME": "/root",
            "TERM": "xterm",
        }
        merged_env.update(os.environ)
        if env:
            merged_env.update(env)

        process = subprocess.Popen(
            full_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=merged_env,
        )
        stdout_lines = []
        for line in process.stdout:
            import sys
            sys.stdout.write(line)
            sys.stdout.flush()
            stdout_lines.append(line)
        process.wait()
        stdout = "".join(stdout_lines)

        result = subprocess.CompletedProcess(
            args=full_cmd, returncode=process.returncode,
            stdout=stdout, stderr=""
        )
        if check and process.returncode != 0:
            raise ToolchainManagerError(
                f"Command failed in build_host (exit {process.returncode}): {cmd_str}"
            )
        return result

    def run_tool(
        self,
        tool: str,
        args: List[str],
        cwd: Optional[Path] = None,
    ) -> subprocess.CompletedProcess:
        """
        Run a build tool (xorriso, mksquashfs, grub2-mkrescue, etc.) from inside
        the build_host chroot, mounting the workdir so it can access the ISO staging
        and rootfs directories.

        In real mode: uses chroot into build_host with workdir bind-mounted.
        In mock mode: simulates the command.
        """
        if self.mode == "mock":
            logger.info(f"[MOCK TOOL] {tool} {' '.join(str(a) for a in args)}")
            return subprocess.CompletedProcess(
                args=[tool] + args, returncode=0,
                stdout="[MOCK TOOL OUTPUT]", stderr=""
            )

        translated_args = []
        for arg in args:
            arg_str = str(arg)
            try:
                arg_path = Path(arg_str).resolve()
                if arg_path.is_relative_to(self.workdir_base):
                    rel = arg_path.relative_to(self.workdir_base)
                    arg_str = str(Path("/workdir") / rel)
            except Exception:
                pass
            translated_args.append(arg_str)

        full_args = [tool] + translated_args
        return self.run_in_build_host(full_args, check=True)

    def mount_virtual_fs(self) -> None:
        """Mount proc/sys/dev inside build_host, and bind-mount workdir into it."""
        if self.mode == "mock":
            logger.info("[MOCK TOOLCHAIN] Mounting virtual filesystems into build_host.")
            self.is_mounted = True
            return

        if os.geteuid() != 0:
            raise ToolchainManagerError("Root privileges required to mount build_host.")

        logger.info(f"Mounting virtual filesystems into build_host: {self.build_host_dir}")

        # Bind-mount /proc, /sys, /dev from the host into build_host.
        # Using --rbind from the host is the same strategy as arch-chroot, debootstrap, and lorax.
        # Creating NEW filesystems with -t proc/sysfs/devtmpfs hits the kernel's mount namespace
        # limit with "move_mount() failed: No space left on device" when many mounts are already active.
        pseudo_mounts = [
            (Path("/proc"),     self.build_host_dir / "proc"),
            (Path("/sys"),      self.build_host_dir / "sys"),
            (Path("/dev"),      self.build_host_dir / "dev"),
        ]

        for host_src, target in pseudo_mounts:
            target.mkdir(parents=True, exist_ok=True)
            if not host_src.exists():
                logger.warning(f"Host path {host_src} does not exist, skipping bind-mount.")
                continue
            res = subprocess.run(
                ["mount", "--rbind", str(host_src), str(target)],
                capture_output=True, text=True,
            )
            if res.returncode != 0 and "already mounted" not in res.stderr:
                logger.warning(f"Could not bind-mount {host_src} → {target}: {res.stderr.strip()}")
            else:
                # Make the bind-mount read-writable and slave so host changes propagate
                subprocess.run(
                    ["mount", "--make-rslave", str(target)],
                    capture_output=True,
                )

        # Bind-mount target leaf paths explicitly both to /workdir/<subname> and to matching host absolute paths inside build_host
        # to avoid infinite recursion of build_host inside itself.
        subdirs = ["chroot", "iso_root", "cache"]
        output_dir = _resolve_from_project("output")

        # 1. Bind-mount to /workdir/<subname> inside build_host
        workdir_mount_base = self.build_host_dir / "workdir"
        workdir_mount_base.mkdir(parents=True, exist_ok=True)
        for sub in subdirs:
            host_sub = self.workdir_base / sub
            host_sub.mkdir(parents=True, exist_ok=True)
            chroot_sub = workdir_mount_base / sub
            chroot_sub.mkdir(parents=True, exist_ok=True)
            subprocess.run(["mount", "--bind", str(host_sub), str(chroot_sub)], capture_output=True)
        # Also bind-mount the output dir
        output_dir.mkdir(parents=True, exist_ok=True)
        (workdir_mount_base / "output").mkdir(parents=True, exist_ok=True)
        subprocess.run(["mount", "--bind", str(output_dir), str(workdir_mount_base / "output")], capture_output=True)

        # 2. Bind-mount to exact absolute host path inside build_host for transparent path resolution
        for sub in subdirs:
            host_sub = self.workdir_base / sub
            host_sub.mkdir(parents=True, exist_ok=True)
            target_in_chroot = self.build_host_dir / host_sub.relative_to("/")
            target_in_chroot.mkdir(parents=True, exist_ok=True)
            subprocess.run(["mount", "--bind", str(host_sub), str(target_in_chroot)], capture_output=True)
        # Mirror output dir at its absolute path inside build_host
        output_dir.mkdir(parents=True, exist_ok=True)
        output_in_chroot = self.build_host_dir / output_dir.relative_to("/")
        output_in_chroot.mkdir(parents=True, exist_ok=True)
        subprocess.run(["mount", "--bind", str(output_dir), str(output_in_chroot)], capture_output=True)

        self.is_mounted = True

    def umount_virtual_fs(self) -> None:
        """Unmount all filesystems from build_host in reverse order."""
        if self.mode == "mock":
            logger.info("[MOCK TOOLCHAIN] Unmounting virtual filesystems from build_host.")
            self.is_mounted = False
            return

        logger.info(f"Unmounting virtual filesystems from build_host: {self.build_host_dir}")

        subdirs = ["chroot", "iso_root", "cache"]
        output_dir = _resolve_from_project("output")

        # Unmount bind-mounted workdir leaves (reverse order)
        bind_targets = []
        for sub in subdirs:
            bind_targets.append(self.build_host_dir / (self.workdir_base / sub).relative_to("/"))
        bind_targets.append(self.build_host_dir / output_dir.relative_to("/"))
        for sub in subdirs:
            bind_targets.append(self.build_host_dir / "workdir" / sub)
        bind_targets.append(self.build_host_dir / "workdir" / "output")
        bind_targets.append(self.build_host_dir / "workdir")

        for target in bind_targets:
            if target.exists():
                subprocess.run(["umount", "-l", str(target)], capture_output=True)

        # Recursively unmount rbind pseudo-filesystems (proc/sys/dev and all submounts)
        for pseudo in ["dev", "sys", "proc"]:
            target = self.build_host_dir / pseudo
            if target.exists():
                subprocess.run(["umount", "-R", "-l", str(target)], capture_output=True)

        self.is_mounted = False

    def get_tool_path(self, tool: str) -> str:
        """
        Return the path to a tool binary inside build_host.
        Used by ISOEngine and DiskEngine to reference tools unambiguously.
        """
        if self.mode == "mock":
            return f"/usr/bin/{tool}"
        candidates = [
            self.build_host_dir / "usr" / "bin" / tool,
            self.build_host_dir / "usr" / "sbin" / tool,
            self.build_host_dir / "bin" / tool,
            self.build_host_dir / "sbin" / tool,
        ]
        for c in candidates:
            if c.exists():
                return str(c)
        return f"/usr/bin/{tool}"  # fallback — chroot will resolve it

    # ── OCI Bootstrap ────────────────────────────────────────────────────────────

    def _build_host_ready(self) -> bool:
        """Check whether build_host already has a functional Fedora rootfs."""
        markers = [
            self.build_host_dir / "etc" / "os-release",
            self.build_host_dir / "usr" / "bin" / "dnf",
            self.build_host_dir / "usr" / "bin" / "mksquashfs",
            self.build_host_dir / "usr" / "bin" / "xorriso",
        ]
        return all(m.exists() for m in markers)

    def _bootstrap_from_oci(self) -> None:
        """
        Download the official Fedora minimal OCI image from registry.fedoraproject.org
        and extract it as the base for build_host, without requiring podman or docker.

        OCI images are standard HTTPS-accessible manifests + blobs. We:
          1. Fetch the image manifest to get the list of layer blob digests
          2. Download each layer (gzipped tar) into cache/
          3. Extract layers sequentially into build_host/
          4. DNF is already present in fedora-minimal → use it to install extra tools
        """
        if self.build_host_dir.exists() and (self.build_host_dir / "etc").exists():
            if not self.force_isolated:
                logger.info("build_host base already extracted. Skipping OCI download.")
                return

        tag = self.releasever  # e.g. "41"
        logger.info(
            f"Downloading Fedora {tag} minimal OCI image from "
            f"{_FEDORA_OCI_REGISTRY} (no podman/docker needed)..."
        )

        # Try direct OCI pull first, fall back to podman/docker export
        try:
            self._pull_oci_direct(tag)
        except Exception as e:
            logger.warning(f"Direct OCI pull failed ({e}). Trying podman/docker export...")
            try:
                self._pull_via_container_runtime(tag)
            except Exception as e2:
                raise ToolchainManagerError(
                    f"Could not bootstrap build_host from Fedora OCI image.\n"
                    f"  Direct OCI: {e}\n"
                    f"  Container runtime: {e2}\n\n"
                    "Solutions:\n"
                    "  1. Install podman: dnf/apt/pacman install podman\n"
                    "  2. Run on a Fedora host with --force-isolated-toolchain\n"
                    "  3. Manually extract a Fedora minimal rootfs into "
                    f"{self.build_host_dir}"
                ) from e2

    def _pull_oci_direct(self, tag: str) -> None:
        """
        Pull Fedora minimal OCI image directly via HTTPS without any container runtime.
        Uses the OCI Distribution Spec v2 API (plain HTTP + JSON + blob downloads).
        """
        manifest_url = _FEDORA_OCI_MANIFEST_URL_TEMPLATE.format(tag=tag)
        logger.info(f"Fetching OCI manifest: {manifest_url}")

        manifest = self._fetch_json(
            manifest_url,
            accept=(
                "application/vnd.oci.image.manifest.v1+json,"
                "application/vnd.docker.distribution.manifest.v2+json"
            ),
        )

        # Handle manifest lists (multi-arch index) — select the right platform
        media_type = manifest.get("mediaType", "")
        if "index" in media_type or manifest.get("manifests"):
            manifest = self._resolve_manifest_from_index(manifest, tag)

        layers: List[Dict[str, Any]] = manifest.get("layers", [])
        if not layers:
            raise ToolchainManagerError(
                f"OCI manifest for fedora-minimal:{tag} has no layers. "
                "The registry may require authentication or the tag may be invalid."
            )

        logger.info(f"Found {len(layers)} layer(s) to download.")
        self.build_host_dir.mkdir(parents=True, exist_ok=True)

        for i, layer in enumerate(layers, 1):
            digest: str = layer["digest"]          # e.g. sha256:abc123...
            size_mb = layer.get("size", 0) // 1024 // 1024
            short_digest = digest[:19]
            logger.info(f"  Layer {i}/{len(layers)}: {short_digest}... ({size_mb} MB)")

            layer_cache = self.cache_dir / f"fedora-minimal-{tag}-layer-{i}.tar.gz"
            if not layer_cache.exists():
                blob_url = _FEDORA_OCI_BLOB_URL_TEMPLATE.format(digest=digest)
                self._download_file(blob_url, layer_cache)

            logger.info(f"  Extracting layer {i}/{len(layers)}...")
            res = subprocess.run(
                ["tar", "-xpf", str(layer_cache), "-C", str(self.build_host_dir),
                 "--numeric-owner", "--overwrite"],
                capture_output=True, text=True,
            )
            if res.returncode != 0:
                # Some whiteout files cause non-fatal errors — warn, don't abort
                logger.warning(f"  tar extract warnings: {res.stderr.strip()[:300]}")

        logger.info("✓ Fedora minimal OCI image extracted into build_host.")

    def _resolve_manifest_from_index(
        self, index: Dict[str, Any], tag: str
    ) -> Dict[str, Any]:
        """
        Given a manifest list (multi-arch index), select the manifest for the
        current host architecture and fetch it.
        """
        oci_arch_map = {
            "x86_64":  "amd64",
            "aarch64": "arm64",
            "ppc64le": "ppc64le",
            "s390x":   "s390x",
        }
        target_oci_arch = oci_arch_map.get(_HOST_ARCH, "amd64")
        manifests = index.get("manifests", [])

        for m in manifests:
            platform_info = m.get("platform", {})
            if platform_info.get("architecture") == target_oci_arch:
                digest = m["digest"]
                logger.info(
                    f"Resolving platform manifest for {target_oci_arch}: digest={digest[:19]}..."
                )
                url_manifest = _FEDORA_OCI_BLOB_URL_TEMPLATE.format(digest=digest).replace(
                    "/blobs/", "/manifests/"
                )
                url_blob = _FEDORA_OCI_BLOB_URL_TEMPLATE.format(digest=digest)
                try:
                    return self._fetch_json(url_manifest)
                except Exception:
                    logger.info("  Manifest endpoint by digest failed, fetching via blobs endpoint...")
                    return self._fetch_json(url_blob)

        raise ToolchainManagerError(
            f"No manifest found in OCI index for architecture '{target_oci_arch}'."
        )

    def _pull_via_container_runtime(self, tag: str) -> None:
        """
        Fallback: use podman or docker to export the Fedora minimal image.
        This requires podman or docker to be installed on the host.
        """
        runtime = shutil.which("podman") or shutil.which("docker")
        if not runtime:
            raise ToolchainManagerError(
                "Neither podman nor docker is available on this host."
            )

        image = f"fedora-minimal:{tag}"
        logger.info(f"Using {runtime} to pull and export {image}...")

        # Pull the image
        res = subprocess.run(
            [runtime, "pull", f"registry.fedoraproject.org/{image}"],
            capture_output=True, text=True,
        )
        if res.returncode != 0:
            raise ToolchainManagerError(
                f"{runtime} pull failed: {res.stderr.strip()}"
            )

        # Create a container and export its filesystem
        export_cache = self.cache_dir / f"fedora-minimal-{tag}-export.tar"
        if not export_cache.exists():
            create_res = subprocess.run(
                [runtime, "create", f"registry.fedoraproject.org/{image}"],
                capture_output=True, text=True,
            )
            if create_res.returncode != 0:
                raise ToolchainManagerError(
                    f"{runtime} create failed: {create_res.stderr.strip()}"
                )
            container_id = create_res.stdout.strip()

            logger.info(f"Exporting container {container_id[:12]}...")
            with open(export_cache, "wb") as f:
                export_res = subprocess.run(
                    [runtime, "export", container_id],
                    stdout=f, stderr=subprocess.PIPE,
                )
            subprocess.run([runtime, "rm", container_id], capture_output=True)

            if export_res.returncode != 0:
                raise ToolchainManagerError(
                    f"{runtime} export failed: {export_res.stderr.decode().strip()}"
                )

        logger.info("Extracting container filesystem into build_host...")
        self.build_host_dir.mkdir(parents=True, exist_ok=True)
        res = subprocess.run(
            ["tar", "-xpf", str(export_cache), "-C", str(self.build_host_dir),
             "--numeric-owner"],
            capture_output=True, text=True,
        )
        if res.returncode != 0:
            logger.warning(f"tar extract warnings: {res.stderr.strip()[:300]}")

        logger.info("✓ Container filesystem extracted into build_host.")

    # ── Tool Installation ────────────────────────────────────────────────────────

    def _install_build_tools(self) -> None:
        """Install all required build tools inside the build_host via DNF."""
        logger.info("Installing build tools inside build_host...")

        packages = list(_BUILD_HOST_PACKAGES)
        packages += _ARCH_EXTRA_PACKAGES.get(self.target_arch, [])
        packages = sorted(set(packages))

        # Configure DNF inside build_host (fast downloads, no docs)
        self._configure_build_host_dnf()

        dnf_cmd = [
            "dnf",
            f"--releasever={self.releasever}",
            "--setopt=install_weak_deps=False",
            "--nodocs",
            "-y",
            "install",
        ] + packages

        logger.info(f"  Installing {len(packages)} packages in build_host...")
        result = self.run_in_build_host(dnf_cmd)
        if result.returncode != 0:
            raise ToolchainManagerError(
                f"Failed to install build tools in build_host (exit {result.returncode}). "
                "Check network connectivity and Fedora repo availability."
            )

        logger.info("✓ All build tools installed in build_host.")
        self._verify_build_tools()

    def _configure_build_host_dnf(self) -> None:
        """Write an optimised dnf.conf inside build_host for fast tool installation."""
        dnf_conf_path = self.build_host_dir / "etc" / "dnf" / "dnf.conf"
        dnf_conf_path.parent.mkdir(parents=True, exist_ok=True)
        dnf_conf_path.write_text(
            "[main]\n"
            "gpgcheck=1\n"
            "installonly_limit=2\n"
            "clean_requirements_on_remove=True\n"
            "best=False\n"
            "skip_if_unavailable=True\n"
            "max_parallel_downloads=10\n"
            "fastestmirror=False\n"
            "install_weak_deps=False\n"
            "tsflags=nodocs\n"
        )

    def _verify_build_tools(self) -> None:
        """Verify that critical tools are present in build_host after installation."""
        required_tools = ["mksquashfs", "xorriso", "dnf"]
        missing = []
        for tool in required_tools:
            found = False
            for subdir in ["usr/sbin", "usr/bin", "sbin", "bin"]:
                if (self.build_host_dir / subdir / tool).exists():
                    found = True
                    break
            if not found:
                missing.append(tool)

        if missing:
            raise ToolchainManagerError(
                f"Build tool verification failed — missing in build_host: "
                f"{', '.join(missing)}"
            )
        logger.info("✓ All critical build tools verified in build_host.")

    # ── Helpers ──────────────────────────────────────────────────────────────────

    def _create_mock_build_host(self) -> None:
        """Create a minimal directory skeleton for mock mode (no network, no root)."""
        self.build_host_dir.mkdir(parents=True, exist_ok=True)
        for d in ["usr/bin", "usr/sbin", "etc/dnf", "workdir"]:
            (self.build_host_dir / d).mkdir(parents=True, exist_ok=True)

    def _copy_resolv_conf(self) -> None:
        """Copy host resolv.conf into build_host for DNS resolution."""
        host_resolv = Path("/etc/resolv.conf")
        target_resolv = self.build_host_dir / "etc" / "resolv.conf"
        target_resolv.parent.mkdir(parents=True, exist_ok=True)
        if host_resolv.exists():
            if target_resolv.is_symlink():
                target_resolv.unlink()
            shutil.copy2(host_resolv, target_resolv)
            logger.debug("Copied /etc/resolv.conf into build_host.")

    def _fetch_json(
        self,
        url: str,
        accept: str = (
            "application/vnd.oci.image.manifest.v1+json, "
            "application/vnd.docker.distribution.manifest.v2+json, "
            "application/vnd.oci.image.index.v1+json, "
            "application/vnd.docker.distribution.manifest.list.v2+json, "
            "application/json"
        ),
    ) -> Dict[str, Any]:
        """Fetch a JSON document from a URL, returning a parsed dict."""
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "fedora-builder/1.0",
                "Accept":     accept,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            raise ToolchainManagerError(f"Failed to fetch JSON from {url}: {e}") from e

    def _download_file(self, url: str, dest: Path) -> None:
        """Download a file from url to dest with progress logging."""
        logger.info(f"  Downloading: {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "fedora-builder/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as out:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                chunk = 1024 * 256  # 256 KiB chunks
                while True:
                    buf = resp.read(chunk)
                    if not buf:
                        break
                    out.write(buf)
                    downloaded += len(buf)
                    if total:
                        pct = downloaded * 100 // total
                        logger.debug(
                            f"    {downloaded // 1024 // 1024} MB / "
                            f"{total // 1024 // 1024} MB ({pct}%)"
                        )
        except Exception as e:
            dest.unlink(missing_ok=True)
            raise ToolchainManagerError(f"Download failed from {url}: {e}") from e

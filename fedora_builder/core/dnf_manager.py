import os
import subprocess
from pathlib import Path
from typing import List, Dict, Any
import logging
from fedora_builder.core.chroot_manager import ChrootManager
from fedora_builder.core.cache_paths import (
    package_cache_dir,
    rootfs_seed_cache_path,
)

logger = logging.getLogger("dnf_manager")

class DNFManagerError(Exception):
    pass

class DNFManager:
    def __init__(self, chroot: ChrootManager, config: Dict[str, Any], toolchain=None):
        self.chroot = chroot
        self.config = config
        self.target_root = chroot.target_root
        self.toolchain = toolchain

    def resolve_cache_dir(self) -> Path:
        """
        Determine resilient package cache directory with write testing and fallback to /tmp.
        Identical to void-builder's cache resolution strategy.
        """
        releasever = self.config.get("releasever", "rawhide")
        basearch = self.config.get("basearch", getattr(self.chroot, "arch", "x86_64"))
        candidate = package_cache_dir(self.config, releasever, basearch)

        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write_test"
            probe.write_text("ok")
            probe.unlink(missing_ok=True)
            return candidate
        except Exception:
            import tempfile
            fallback = (
                Path(tempfile.gettempdir())
                / "fedora-builder-cache"
                / "software"
                / str(releasever)
                / str(basearch)
                / "dnf"
            )
            fallback.mkdir(parents=True, exist_ok=True)
            logger.info(f"Using fallback cache directory: {fallback}")
            return fallback

    def _run_dnf(self, args: List[str]) -> subprocess.CompletedProcess:
        """Run DNF (or DNF5 for F41+) using the isolated build_host toolchain if available, otherwise host fallback."""
        import shutil
        dnf_cmd = "dnf"
        releasever = str(self.config.get("releasever", "41"))
        if releasever in ["41", "42", "rawhide"] and shutil.which("dnf5"):
            dnf_cmd = "dnf5"

        if self.toolchain:
            return self.toolchain.run_tool(dnf_cmd, args)
        else:
            return subprocess.run([dnf_cmd] + args)

    def configure_dnf_conf(self):
        dnf_conf_dir = self.target_root / "etc" / "dnf"
        if self.chroot.mode == "mock":
            dnf_conf_dir.mkdir(parents=True, exist_ok=True)
            (dnf_conf_dir / "dnf.conf").touch()
            return
        
        dnf_conf_dir.mkdir(parents=True, exist_ok=True)
        conf_content = "[main]\n"
        conf_content += "gpgcheck=1\n"
        conf_content += "installonly_limit=3\n"
        conf_content += "clean_requirements_on_remove=True\n"
        conf_content += "best=False\n"
        conf_content += "skip_if_unavailable=True\n"
        cpus = os.cpu_count() or 4
        max_downloads = min(20, max(10, cpus * 2))
        conf_content += f"max_parallel_downloads={max_downloads}\n"
        conf_content += "keepcache=True\n"
        conf_content += "deltarpm=true\n"
        conf_content += "fastestmirror=true\n"
        conf_content += "install_weak_deps=False\n"
        conf_content += "tsflags=nodocs\n"
        with open(dnf_conf_dir / "dnf.conf", "w") as f:
            f.write(conf_content)

    def import_gpg_keys(self):
        if self.chroot.mode == "mock":
            return
        logger.info("Importing GPG keys")

    def configure_repos(self, repos: List[Any]):
        repo_dir = self.target_root / "etc" / "yum.repos.d"
        if self.chroot.mode == "mock":
            repo_dir.mkdir(parents=True, exist_ok=True)
            return
            
        repo_dir.mkdir(parents=True, exist_ok=True)
        from fedora_builder.core.config_loader import ConfigLoader
        loader = ConfigLoader()

        for repo in repos:
            if isinstance(repo, str):
                loaded = loader.load_profile("repos", repo)
                if loaded:
                    repo = loaded
                else:
                    repo = {
                        "repo_id": repo,
                        "install_package": f"https://mirrors.rpmfusion.org/free/fedora/{repo}-release-{self.config.get('releasever', '41')}.noarch.rpm" if "rpmfusion" in repo else None
                    }

            repo_id = repo.get("repo_id")
            if not repo_id:
                continue
            repo_name = repo.get("repo_name", repo_id)
            metalink = repo.get("metalink")
            baseurl = repo.get("baseurl")
            enabled = repo.get("enabled", 1)
            gpgcheck = repo.get("gpgcheck", 1)
            gpgkey = repo.get("gpgkey", "")
            install_package = repo.get("install_package")
            copr_user = repo.get("copr_user")
            copr_project = repo.get("copr_project")
            
            if install_package:
                self.install_rpmfusion_release(repo, self.config.get("releasever", "41"))
                continue
                
            if copr_user and copr_project:
                baseurl = f"https://copr-be.cloud.fedoraproject.org/results/{copr_user}/{copr_project}/fedora-$releasever-$basearch/"
            
            repo_content = f"[{repo_id}]\nname={repo_name}\n"
            if metalink:
                repo_content += f"metalink={metalink}\n"
            elif baseurl:
                repo_content += f"baseurl={baseurl}\n"
            repo_content += f"enabled={enabled}\ngpgcheck={gpgcheck}\nskip_if_unavailable=True\n"
            if gpgkey:
                repo_content += f"gpgkey={gpgkey}\n"
                
            with open(repo_dir / f"{repo_id}.repo", "w") as f:
                f.write(repo_content)

    def install_rpmfusion_release(self, rpmfusion_config: Dict, releasever: str):
        url = rpmfusion_config.get("install_package")
        if not url:
            return
        basearch = self.config.get("basearch", "x86_64")
        url = url.replace("$releasever", str(releasever)).replace("$basearch", str(basearch))
        args = self._get_base_dnf_args() + ["-y", "--nogpgcheck", "install", url]
        if self.chroot.mode != "mock":
            logger.info(f"Installing RPMFusion release package from {url}...")
            res = self._run_dnf(args)
            if res.returncode != 0:
                logger.warning(f"Could not install RPMFusion release package from {url}: {res.stderr}")

            gpg_dir = self.target_root / "etc" / "pki" / "rpm-gpg"
            gpg_dir.mkdir(parents=True, exist_ok=True)
            for repo_type in ["free", "nonfree"]:
                key_name = f"RPM-GPG-KEY-rpmfusion-{repo_type}-fedora-{releasever}"
                key_file = gpg_dir / key_name
                if not key_file.exists():
                    key_url = f"https://rpmfusion.org/keys?action=AttachFile&do=get&target={key_name}"
                    import urllib.request
                    try:
                        logger.info(f"Downloading missing GPG key: {key_name}...")
                        urllib.request.urlretrieve(key_url, key_file)
                    except Exception as e:
                        logger.warning(f"Could not download {key_name}: {e}")

                if key_file.exists():
                    self.chroot.run_in_chroot(["rpm", "--import", f"/etc/pki/rpm-gpg/{key_name}"], check=False)

    def bootstrap_rootfs(self, releasever: str, basearch: str, use_seed: bool = True):
        if self.chroot.mode == "mock":
            try:
                self.target_root.mkdir(parents=True, exist_ok=True)
                for d in ["etc/dnf", "etc/yum.repos.d", "boot", "usr/bin", "var/cache/dnf"]:
                    (self.target_root / d).mkdir(parents=True, exist_ok=True)
            except PermissionError:
                logger.debug("Mock rootfs directory creation ignored due to root permissions.")
            return

        if (self.target_root / "etc" / "os-release").exists() or (self.target_root / "usr" / "bin" / "bash").exists():
            logger.info("⚡ Target rootfs already contains base system, reusing existing tree.")
            return

        self.target_root.mkdir(parents=True, exist_ok=True)
        (self.target_root / "var" / "log").mkdir(parents=True, exist_ok=True)

        cache_dir = self.resolve_cache_dir()
        seed_cache = rootfs_seed_cache_path(self.config, releasever, basearch)

        if use_seed and seed_cache.exists():
            logger.info(f"⚡ Fast-bootstrapping Fedora rootfs from local seed tarball: {seed_cache}")
            res = subprocess.run(["tar", "xzpf", str(seed_cache), "-C", str(self.target_root), "--numeric-owner"])
            if res.returncode == 0:
                logger.info("Successfully bootstrapped Fedora rootfs from local seed tarball in seconds!")
                return
            else:
                logger.warning("Local seed tarball extraction failed (archive corrupt). Removing bad seed and falling back to DNF @core bootstrap.")
                try:
                    seed_cache.unlink(missing_ok=True)
                except Exception:
                    pass

        args = self._get_base_dnf_args() + [
            f"--releasever={releasever}",
            f"--forcearch={basearch}",
            "--setopt=install_weak_deps=False",
            "--nodocs",
            "-y",
            "install",
            "@core",
        ]
        res = self._run_dnf(args)
        if res.returncode != 0:
            raise DNFManagerError(f"Bootstrap failed: {res.returncode}")

        # Save rootfs seed tarball for instant future builds (excluding virtual kernel filesystems)
        try:
            seed_cache.parent.mkdir(parents=True, exist_ok=True)
            logger.info(f"⚡ Fast-caching Fedora rootfs seed tarball to {seed_cache}...")
            subprocess.run([
                "tar", "czpf", str(seed_cache),
                "--exclude=./proc/*", "--exclude=./sys/*", "--exclude=./dev/*", "--exclude=./tmp/*", "--exclude=./run/*",
                "-C", str(self.target_root), "."
            ], check=False)
        except Exception as e:
            logger.warning(f"Could not save seed tarball cache: {e}")

    def _get_base_dnf_args(self) -> List[str]:
        cache_dir = self.resolve_cache_dir()
        args = [
            f"--installroot={self.target_root}",
            f"--setopt=cachedir={cache_dir}",
            "--setopt=keepcache=True",
        ]
        target_repos = list((self.target_root / "etc" / "yum.repos.d").glob("*.repo")) if (self.target_root / "etc" / "yum.repos.d").exists() else []
        if not target_repos:
            args.append("--use-host-config")
        return args

    def _get_install_dnf_args(self) -> List[str]:
        return self._get_base_dnf_args()

    def install_packages(self, packages: List[str]):
        if not packages:
            return
        real_pkgs = [p for p in packages if not p.startswith("@")]
        if not real_pkgs:
            return
        if self.chroot.mode == "mock":
            return
        # "--allowerasing" and "--skip-unavailable" must come after the "install"
        # subcommand: dnf5 rejects them as global options and requires them to be
        # placed after the command. This allows builds to continue when optional
        # packages are not present in the target release or repo set.
        args = self._get_install_dnf_args() + ["-y", "install", "--allowerasing", "--skip-unavailable"] + real_pkgs
        res = self._run_dnf(args)
        if res.returncode != 0:
            raise DNFManagerError("Package installation failed")

    def install_groups(self, groups: List[str]):
        if not groups:
            return
        formatted_groups = [g if g.startswith("@") else f"@{g}" for g in groups]
        if self.chroot.mode == "mock":
            return
        # "--allowerasing" and "--skip-unavailable" must come after the "install"
        # subcommand: dnf5 rejects them as global options and requires them to be
        # placed after the command. This allows optional groups to be skipped cleanly.
        args = self._get_install_dnf_args() + ["-y", "install", "--allowerasing", "--skip-unavailable"] + formatted_groups
        res = self._run_dnf(args)
        if res.returncode != 0:
            raise DNFManagerError("Group installation failed")

    def install_all(self, packages: List[str], groups: List[str]):
        self.install_groups(groups)
        self.install_packages(packages)

    def clean_cache(self):
        if self.chroot.mode == "mock":
            return
        args = self._get_base_dnf_args() + ["clean", "dbcache"]
        self._run_dnf(args)

    def configure_selinux(self, mode: str = "permissive"):
        selinux_dir = self.target_root / "etc" / "selinux"
        if self.chroot.mode == "mock":
            selinux_dir.mkdir(parents=True, exist_ok=True)
            return
        selinux_dir.mkdir(parents=True, exist_ok=True)
        with open(selinux_dir / "config", "w") as f:
            f.write(f"SELINUX={mode}\nSELINUXTYPE=targeted\n")
        if mode == "enforcing":
            (self.target_root / ".autorelabel").touch()

    def configure_dnf_in_rootfs(self):
        self.configure_dnf_conf()

    def download_offline_packages(self, packages: list, dest_dir):
        if not packages: return
        import os, subprocess
        dest_dir.mkdir(parents=True, exist_ok=True)
        cmd = ["dnf", "download", "--resolve", "--destdir", str(dest_dir)] + packages
        self.chroot.run_in_chroot(cmd)

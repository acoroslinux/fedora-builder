import os
import subprocess
from pathlib import Path
from typing import List, Dict, Any
import logging
from fedora_builder.core.chroot_manager import ChrootManager

logger = logging.getLogger("dnf_manager")

class DNFManagerError(Exception):
    pass

class DNFManager:
    def __init__(self, chroot: ChrootManager, config: Dict[str, Any], toolchain=None):
        self.chroot = chroot
        self.config = config
        self.target_root = chroot.target_root
        self.toolchain = toolchain

    def _run_dnf(self, args: List[str]) -> subprocess.CompletedProcess:
        """Run DNF using the isolated build_host toolchain if available, otherwise host fallback."""
        if self.toolchain:
            return self.toolchain.run_tool("dnf", args)
        else:
            return subprocess.run(["dnf"] + args)

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
        conf_content += "max_parallel_downloads=10\n"
        conf_content += "keepcache=True\n"
        conf_content += "deltarpm=true\n"
        conf_content += "fastestmirror=false\n"
        conf_content += "install_weak_deps=False\n"
        conf_content += "tsflags=nodocs\n"
        with open(dnf_conf_dir / "dnf.conf", "w") as f:
            f.write(conf_content)

    def import_gpg_keys(self):
        if self.chroot.mode == "mock":
            return
        logger.info("Importing GPG keys")

    def configure_repos(self, repos: List[Dict]):
        repo_dir = self.target_root / "etc" / "yum.repos.d"
        if self.chroot.mode == "mock":
            repo_dir.mkdir(parents=True, exist_ok=True)
            return
            
        repo_dir.mkdir(parents=True, exist_ok=True)
        for repo in repos:
            repo_id = repo.get("repo_id")
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
            repo_content += f"enabled={enabled}\ngpgcheck={gpgcheck}\n"
            if gpgkey:
                repo_content += f"gpgkey={gpgkey}\n"
                
            with open(repo_dir / f"{repo_id}.repo", "w") as f:
                f.write(repo_content)

    def install_rpmfusion_release(self, rpmfusion_config: Dict, releasever: str):
        url = rpmfusion_config.get("install_package")
        if not url:
            return
        args = ["--installroot", str(self.target_root), "-y", "install", url]
        if self.chroot.mode != "mock":
            self._run_dnf(args)

    def bootstrap_rootfs(self, releasever: str, basearch: str):
        if self.chroot.mode == "mock":
            try:
                self.target_root.mkdir(parents=True, exist_ok=True)
                for d in ["etc/dnf", "etc/yum.repos.d", "boot", "usr/bin", "var/cache/dnf"]:
                    (self.target_root / d).mkdir(parents=True, exist_ok=True)
            except PermissionError:
                logger.debug("Mock rootfs directory creation ignored due to root permissions.")
            return
        args = [
            f"--installroot={self.target_root}",
            f"--releasever={releasever}",
            f"--forcearch={basearch}",
            "--use-host-config",
            "--setopt=install_weak_deps=False",
            "--nodocs",
            "-y",
            "install",
            "@core",
        ]
        res = self._run_dnf(args)
        if res.returncode != 0:
            raise DNFManagerError(f"Bootstrap failed: {res.returncode}")

    def _get_base_dnf_args(self) -> List[str]:
        args = ["--installroot", str(self.target_root)]
        target_repos = list((self.target_root / "etc" / "yum.repos.d").glob("*.repo")) if (self.target_root / "etc" / "yum.repos.d").exists() else []
        if not target_repos:
            args.append("--use-host-config")
        return args

    def install_packages(self, packages: List[str]):
        if not packages:
            return
        real_pkgs = [p for p in packages if not p.startswith("@")]
        if not real_pkgs:
            return
        if self.chroot.mode == "mock":
            return
        args = self._get_base_dnf_args() + ["-y", "install"] + real_pkgs
        res = self._run_dnf(args)
        if res.returncode != 0:
            raise DNFManagerError("Package installation failed")

    def install_groups(self, groups: List[str]):
        if not groups:
            return
        real_groups = [g.lstrip("@") for g in groups]
        if self.chroot.mode == "mock":
            return
        args = self._get_base_dnf_args() + ["-y", "groupinstall"] + real_groups
        res = self._run_dnf(args)
        if res.returncode != 0:
            raise DNFManagerError("Group installation failed")

    def install_all(self, packages: List[str], groups: List[str]):
        self.install_groups(groups)
        self.install_packages(packages)

    def clean_cache(self):
        if self.chroot.mode == "mock":
            return
        subprocess.run(["dnf", "--installroot", str(self.target_root), "clean", "all"])

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

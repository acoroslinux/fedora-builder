import subprocess
from pathlib import Path
from typing import Dict, Any
from fedora_builder.core.chroot_manager import ChrootManager

class SystemCustomizer:
    def __init__(self, chroot: ChrootManager, config: Dict[str, Any]):
        self.chroot = chroot
        self.config = config
        self.target_root = chroot.target_root

    def setup_live_users(self):
        live_user = self.config.get("live_user", "liveuser")
        if isinstance(live_user, dict):
            live_user = live_user.get("name", "liveuser")
        groups = self.config.get("live_groups", ["wheel"])
        if self.chroot.mode == "mock":
            return
            
        groups_str = ",".join(groups)
        try:
            self.chroot.run_in_chroot(["useradd", "-m", "-G", groups_str, str(live_user)], check=False)
            self.chroot.run_in_chroot(f"echo '{live_user}:live' | chpasswd", check=False)
        except Exception:
            pass
        
        sudoers_file = self.target_root / "etc" / "sudoers.d" / "wheel_nopasswd"
        sudoers_file.parent.mkdir(parents=True, exist_ok=True)
        with open(sudoers_file, "w") as f:
            f.write("%wheel ALL=(ALL) NOPASSWD: ALL\n")

    def configure_system_defaults(self):
        if self.chroot.mode == "mock":
            return
        hostname = self.config.get("hostname", "fedora-live")
        with open(self.target_root / "etc" / "hostname", "w") as f:
            f.write(f"{hostname}\n")
            
        locale = self.config.get("locale", "en_US.UTF-8")
        with open(self.target_root / "etc" / "locale.conf", "w") as f:
            f.write(f"LANG={locale}\n")

    def setup_services(self):
        if self.chroot.mode == "mock":
            return
        services = self.config.get("services", [])
        if isinstance(services, dict):
            services = services.get("enable", [])
        for svc in services:
            try:
                self.chroot.run_in_chroot(["systemctl", "enable", str(svc)], check=False)
            except Exception:
                pass

    def configure_autologin(self):
        if self.chroot.mode == "mock":
            return
        dm = self.config.get("display_manager")
        if not dm:
            return
            
        live_user = self.config.get("live_user", "liveuser")
        if isinstance(live_user, dict):
            live_user = live_user.get("name", "liveuser")
        if dm == "gdm":
            gdm_conf = self.target_root / "etc" / "gdm" / "custom.conf"
            gdm_conf.parent.mkdir(parents=True, exist_ok=True)
            with open(gdm_conf, "w") as f:
                f.write(f"[daemon]\nAutomaticLoginEnable=True\nAutomaticLogin={live_user}\n")

    def configure_plymouth(self):
        if self.chroot.mode == "mock":
            return
        theme = self.config.get("plymouth_theme", "spinner")
        try:
            self.chroot.run_in_chroot(["plymouth-set-default-theme", str(theme)], check=False)
        except Exception:
            pass

    def configure_selinux(self):
        if self.chroot.mode == "mock":
            return
        from fedora_builder.core.dnf_manager import DNFManager
        dnf_mgr = DNFManager(self.chroot, self.config)
        dnf_mgr.configure_selinux(self.config.get("selinux_mode", "permissive"))

    def copy_custom_files(self):
        if self.chroot.mode == "mock":
            return
        # Usually copies from configs/custom_files to target_root

    def configure_live_environment(self):
        self.setup_live_users()
        self.configure_system_defaults()
        self.setup_services()
        self.configure_autologin()
        self.configure_plymouth()
        self.configure_selinux()
        self.copy_custom_files()

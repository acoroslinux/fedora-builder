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

    def configure_zram(self):
        """Configure systemd-zram-generator for RAM compressed swap (Fedora default)."""
        if self.chroot.mode == "mock":
            return
        if not self.config.get("with_zram", True):
            return
        zram_conf = self.target_root / "etc" / "systemd" / "zram-generator.conf"
        zram_conf.parent.mkdir(parents=True, exist_ok=True)
        with open(zram_conf, "w") as f:
            f.write("[zram0]\nzram-size = ram / 2\ncompression-algorithm = zstd\n")

    def configure_flathub(self):
        """Configure Flathub Flatpak repository on system first-boot."""
        if self.chroot.mode == "mock":
            return
        if not self.config.get("with_flathub", False):
            return
        flatpak_dir = self.target_root / "etc" / "flatpak" / "remotes.d"
        flatpak_dir.mkdir(parents=True, exist_ok=True)
        flathub_repo = flatpak_dir / "flathub.flatpakrepo"
        repo_content = (
            "[Flatpak Remote]\n"
            "Title=Flathub\n"
            "Url=https://dl.flathub.org/repo/\n"
            "GPGKeyURL=https://dl.flathub.org/repo/flathub.gpg\n"
            "Homepage=https://flathub.org/\n"
            "Comment=Central repository of Flatpak applications\n"
        )
        with open(flathub_repo, "w") as f:
            f.write(repo_content)

    def configure_polkit_power(self):
        """Configure passwordless administrative and power management Polkit rules."""
        if self.chroot.mode == "mock":
            return
        polkit_dir = self.target_root / "etc" / "polkit-1" / "rules.d"
        polkit_dir.mkdir(parents=True, exist_ok=True)
        rule_file = polkit_dir / "10-enable-power-actions.rules"
        rule_content = (
            "/* Allow all users to perform power off, reboot, suspend, and session logout */\n"
            "polkit.addRule(function(action, subject) {\n"
            "    if (action.id.indexOf('org.freedesktop.login1.') === 0 ||\n"
            "        action.id.indexOf('org.freedesktop.upower.') === 0 ||\n"
            "        action.id.indexOf('org.gnome.SessionManager.') === 0 ||\n"
            "        action.id.indexOf('org.freedesktop.consolekit.') === 0) {\n"
            "        return polkit.Result.YES;\n"
            "    }\n"
            "});\n"
        )
        with open(rule_file, "w") as f:
            f.write(rule_content)

    def configure_calamares(self):
        """Configure Calamares desktop launcher and autostart script if with_calamares is enabled."""
        if self.chroot.mode == "mock":
            return
        if not self.config.get("with_calamares", False):
            return
        
        script_path = self.target_root / "usr" / "local" / "bin" / "create-install-icon.sh"
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_content = (
            "#!/bin/bash\n"
            "for user_home in /home/*; do\n"
            "    if [ -d \"$user_home\" ]; then\n"
            "        desktop_dir=\"$user_home/Desktop\"\n"
            "        mkdir -p \"$desktop_dir\"\n"
            "        cat << 'EOF' > \"$desktop_dir/install-fedora.desktop\"\n"
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Install Fedora Modern\n"
            "Comment=Install Fedora Linux to disk\n"
            "Exec=sudo calamares\n"
            "Icon=system-software-install\n"
            "Terminal=false\n"
            "Categories=System;\n"
            "EOF\n"
            "        chmod +x \"$desktop_dir/install-fedora.desktop\"\n"
            "        chown -R $(basename \"$user_home\"): \"$desktop_dir\"\n"
            "    fi\n"
            "done\n"
        )
        script_path.write_text(script_content)
        script_path.chmod(0o755)

        autostart_dir = self.target_root / "etc" / "xdg" / "autostart"
        autostart_dir.mkdir(parents=True, exist_ok=True)
        (autostart_dir / "create-install-icon.desktop").write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Create Install Icon\n"
            "Exec=/usr/local/bin/create-install-icon.sh\n"
            "Hidden=false\n"
            "NoDisplay=false\n"
            "X-GNOME-Autostart-enabled=true\n"
        )

    def copy_custom_files(self):
        if self.chroot.mode == "mock":
            return

    def configure_live_environment(self):
        self.setup_live_users()
        self.configure_system_defaults()
        self.setup_services()
        self.configure_autologin()
        self.configure_plymouth()
        self.configure_selinux()
        self.configure_zram()
        self.configure_flathub()
        self.configure_polkit_power()
        self.configure_calamares()
        self.copy_custom_files()

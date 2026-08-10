import logging
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any
from fedora_builder.core.chroot_manager import ChrootManager

logger = logging.getLogger("customizer")

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
        hostname = self.config.get("hostname", "fedora-modern")
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
            enable = services.get("enable", [])
            disable = services.get("disable", [])
        elif isinstance(services, list):
            enable = services
            disable = []
        else:
            return

        for svc in enable:
            try:
                self.chroot.run_in_chroot(["systemctl", "enable", str(svc)], check=False)
            except Exception:
                pass

        for svc in disable:
            try:
                self.chroot.run_in_chroot(["systemctl", "disable", "--now", str(svc)], check=False)
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

        session = self.config.get("session", "")
        session_type = self.config.get("session_type", "x11")

        if dm == "gdm":
            self._configure_gdm_autologin(live_user, session)
        elif dm == "lightdm":
            self._configure_lightdm_autologin(live_user, session)
        elif dm == "sddm":
            self._configure_sddm_autologin(live_user, session, session_type)
        elif dm == "lxdm":
            self._configure_lxdm_autologin(live_user, session)
        else:
            logger.warning(f"Autologin not implemented for display manager: {dm}")

    def _configure_gdm_autologin(self, live_user: str, session: str):
        gdm_conf = self.target_root / "etc" / "gdm" / "custom.conf"
        gdm_conf.parent.mkdir(parents=True, exist_ok=True)
        content = (
            "[daemon]\n"
            f"AutomaticLoginEnable=True\n"
            f"AutomaticLogin={live_user}\n"
            "TimedLoginEnable=False\n\n"
            "[security]\n\n"
            "[xdmcp]\n\n"
            "[chooser]\n\n"
            "[debug]\n"
        )
        gdm_conf.write_text(content)
        logger.info(f"Configured GDM autologin for {live_user}")

    def _configure_lightdm_autologin(self, live_user: str, session: str):
        # Main lightdm.conf
        lightdm_conf = self.target_root / "etc" / "lightdm" / "lightdm.conf"
        lightdm_conf.parent.mkdir(parents=True, exist_ok=True)
        content = (
            "[LightDM]\n\n"
            "[Seat:*]\n"
            f"autologin-user={live_user}\n"
            "autologin-user-timeout=0\n"
            "autologin-in-background=false\n"
        )
        if session:
            content += f"autologin-session={session}\n"
        content += (
            "user-session=default\n"
            "greeter-show-manual-login=false\n"
            "greeter-hide-users=false\n\n"
            "[XDMCPServer]\n\n"
            "[VNCServer]\n"
        )
        lightdm_conf.write_text(content)

        # Drop-in for greeter-specific config (gtk greeter)
        greeter_conf = self.target_root / "etc" / "lightdm" / "lightdm-gtk-greeter.conf"
        greeter_content = (
            "[greeter]\n"
            "background=/usr/share/backgrounds/fedora-modern/fedora-modern.jpg\n"
            "theme-name=Adwaita-dark\n"
            "icon-theme-name=Adwaita\n"
            "font-name=Sans 11\n"
            "indicators=~spacer;~clock;~spacer;~session;~language;~a11y;~power\n"
            "clock-format=%H:%M\n"
            "show-indicators-on-lockscreen=true\n"
            "hide-user-image=false\n"
        )
        greeter_conf.write_text(greeter_content)

        # PAM group for autologin (required for lightdm autologin to work without password)
        pam_autologin = self.target_root / "etc" / "pam.d" / "lightdm-autologin"
        if not pam_autologin.exists():
            pam_content = (
                "#%PAM-1.0\n"
                "auth        required    pam_env.so\n"
                "auth        required    pam_nologin.so\n"
                "-auth       optional    pam_kwallet5.so\n"
                "-auth       optional    pam_gnome_keyring.so\n"
                "auth        sufficient  pam_succeed_if.so user ingroup autologin\n"
                "auth        required    pam_permit.so\n"
                "account     include     system-auth\n"
                "password    include     system-auth\n"
                "session     required    pam_limits.so\n"
                "session     include     system-auth\n"
                "-session    optional    pam_kwallet5.so auto_start\n"
                "-session    optional    pam_gnome_keyring.so auto_start\n"
                "session     required    pam_loginuid.so\n"
                "session     optional    pam_systemd.so\n"
            )
            pam_autologin.parent.mkdir(parents=True, exist_ok=True)
            pam_autologin.write_text(pam_content)

        # Add live_user to autologin group (required by PAM rule above)
        try:
            self.chroot.run_in_chroot(["groupadd", "-f", "autologin"], check=False)
            self.chroot.run_in_chroot(["usermod", "-aG", "autologin", live_user], check=False)
        except Exception:
            pass

        logger.info(f"Configured LightDM autologin for {live_user} (session={session or 'default'})")

    def _configure_sddm_autologin(self, live_user: str, session: str, session_type: str):
        sddm_conf_dir = self.target_root / "etc" / "sddm.conf.d"
        sddm_conf_dir.mkdir(parents=True, exist_ok=True)
        # Determine correct session name for SDDM
        # SDDM looks for .desktop files in /usr/share/xsessions or /usr/share/wayland-sessions
        if session_type == "wayland":
            session_dir = "wayland-sessions"
        else:
            session_dir = "xsessions"

        content = (
            "[Autologin]\n"
            f"User={live_user}\n"
            f"Session={session}\n\n"
            "[General]\n"
            "HaltCommand=/usr/bin/systemctl poweroff\n"
            "RebootCommand=/usr/bin/systemctl reboot\n"
            "Numlock=none\n\n"
            "[Theme]\n"
            "Current=breeze\n\n"
            "[Users]\n"
            "MaximumUid=60000\n"
            "MinimumUid=1000\n"
            "RememberLastUser=true\n"
        )
        (sddm_conf_dir / "autologin.conf").write_text(content)

        # SDDM also needs the user in the autologin group on some distros
        try:
            self.chroot.run_in_chroot(["groupadd", "-f", "autologin"], check=False)
            self.chroot.run_in_chroot(["usermod", "-aG", "autologin", live_user], check=False)
        except Exception:
            pass

        logger.info(f"Configured SDDM autologin for {live_user} (session={session}, type={session_type})")

    def _configure_lxdm_autologin(self, live_user: str, session: str):
        lxdm_conf = self.target_root / "etc" / "lxdm" / "lxdm.conf"
        lxdm_conf.parent.mkdir(parents=True, exist_ok=True)
        content = (
            "[base]\n"
            f"autologin={live_user}\n"
            "arg=/usr/bin/X\n"
            "numlock=0\n"
            f"session={f'/usr/bin/{session}' if session else ''}\n\n"
            "[server]\n\n"
            "[display]\n"
            "gtk_theme=Clearlooks\n"
            "bg=/usr/share/backgrounds/fedora-modern/fedora-modern.jpg\n"
            "bottom_pane=1\n"
            "lang=1\n"
            "keyboard=0\n"
            "theme=Industrial\n"
        )
        lxdm_conf.write_text(content)
        logger.info(f"Configured LXDM autologin for {live_user}")

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

    def configure_live_performance(self):
        """
        Apply runtime optimisations for a live ISO environment:
          - Tune kernel VM parameters (swappiness, dirty ratio, huge pages)
          - Keep systemd-journald entirely in RAM (no persistent journal)
          - Silence coredumps
          - Reduce systemd default-timeout values for faster boot/shutdown
          - Disable systemd-oomd (earlyoom is lighter) or configure it
        """
        if self.chroot.mode == "mock":
            return

        # ── /etc/sysctl.d/90-live.conf ──────────────────────────────────────
        sysctl_dir = self.target_root / "etc" / "sysctl.d"
        sysctl_dir.mkdir(parents=True, exist_ok=True)
        (sysctl_dir / "90-live.conf").write_text(
            "# Fedora Live ISO performance tuning\n"
            "vm.swappiness = 10\n"               # prefer RAM over swap/zram
            "vm.dirty_ratio = 20\n"
            "vm.dirty_background_ratio = 5\n"
            "vm.vfs_cache_pressure = 50\n"        # keep inode/dentry cache longer
            "kernel.core_pattern = /dev/null\n"   # discard coredumps
            "kernel.nmi_watchdog = 0\n"
            "net.ipv4.tcp_fastopen = 3\n"
        )

        # ── /etc/systemd/journald.conf.d/90-live.conf ───────────────────────
        journald_dir = self.target_root / "etc" / "systemd" / "journald.conf.d"
        journald_dir.mkdir(parents=True, exist_ok=True)
        (journald_dir / "90-live.conf").write_text(
            "[Journal]\n"
            "Storage=volatile\n"           # RAM only, no /var/log/journal
            "Compress=yes\n"
            "SystemMaxUse=64M\n"
            "RuntimeMaxUse=64M\n"
            "ForwardToSyslog=no\n"
            "MaxLevelStore=warning\n"      # only warnings+ in live (reduces noise)
        )

        # ── /etc/systemd/system.conf.d/90-live.conf ─────────────────────────
        system_conf_dir = self.target_root / "etc" / "systemd" / "system.conf.d"
        system_conf_dir.mkdir(parents=True, exist_ok=True)
        (system_conf_dir / "90-live.conf").write_text(
            "[Manager]\n"
            "DefaultTimeoutStartSec=15s\n"
            "DefaultTimeoutStopSec=10s\n"
            "DefaultDeviceTimeoutSec=10s\n"
        )

        # ── /etc/systemd/coredump.conf.d/90-live.conf ───────────────────────
        coredump_dir = self.target_root / "etc" / "systemd" / "coredump.conf.d"
        coredump_dir.mkdir(parents=True, exist_ok=True)
        (coredump_dir / "90-live.conf").write_text(
            "[Coredump]\n"
            "Storage=none\n"
            "ProcessSizeMax=0\n"
        )

        # ── /etc/systemd/logind.conf.d/90-live.conf ─────────────────────────
        # Stop systemd-logind from keeping sessions in RAM after logout
        logind_dir = self.target_root / "etc" / "systemd" / "logind.conf.d"
        logind_dir.mkdir(parents=True, exist_ok=True)
        (logind_dir / "90-live.conf").write_text(
            "[Login]\n"
            "NAutoVTs=2\n"              # only 2 virtual ttys instead of 6
            "ReserveVT=1\n"
            "KillUserProcesses=yes\n"
            "RemoveIPC=yes\n"
        )

        logger.info("Applied live performance tuning (sysctl, journald, systemd, coredump)")

    def configure_network_sharing(self):
        """
        Configure NetworkManager, mDNS/Avahi and basic file-sharing defaults
        suitable for a live environment.
        """
        if self.chroot.mode == "mock":
            return

        # ── NetworkManager: use systemd-resolved, IPv6 privacy, faster roaming ──
        nm_conf_dir = self.target_root / "etc" / "NetworkManager" / "conf.d"
        nm_conf_dir.mkdir(parents=True, exist_ok=True)
        (nm_conf_dir / "90-live.conf").write_text(
            "[main]\n"
            "dns=systemd-resolved\n"
            "systemd-resolved=true\n\n"
            "[connection]\n"
            "connection.stable-id=${CONNECTION}/${BOOT}\n"  # fresh ID each boot
            "ipv6.ip6-privacy=2\n"                          # RFC4941 privacy
            "ethernet.cloned-mac-address=stable\n"
            "wifi.cloned-mac-address=stable\n\n"
            "[connectivity]\n"
            "uri=https://fedoraproject.org/static/hotspot.txt\n"
            "response=OK\n"
        )

        # ── systemd-resolved: enable mDNS and LLMNR on all interfaces ────────
        resolved_dir = self.target_root / "etc" / "systemd" / "resolved.conf.d"
        resolved_dir.mkdir(parents=True, exist_ok=True)
        (resolved_dir / "90-live.conf").write_text(
            "[Resolve]\n"
            "MulticastDNS=yes\n"
            "LLMNR=yes\n"
            "Cache=yes\n"
            "DNSStubListener=yes\n"
        )

        # ── /etc/nsswitch.conf: ensure mdns4_minimal for .local resolution ───
        nsswitch = self.target_root / "etc" / "nsswitch.conf"
        if nsswitch.exists():
            content = nsswitch.read_text()
            if "mdns4_minimal" not in content:
                content = content.replace(
                    "hosts:      files dns",
                    "hosts:      files mdns4_minimal [NOTFOUND=return] dns myhostname"
                )
                if "mdns4_minimal" not in content:
                    # Generic fallback if pattern didn't match
                    content = content.replace(
                        "hosts:      files",
                        "hosts:      files mdns4_minimal [NOTFOUND=return]"
                    )
                nsswitch.write_text(content)

        # ── Avahi daemon: enable wide-area mDNS, disable IPv6 if not needed ──
        avahi_conf = self.target_root / "etc" / "avahi" / "avahi-daemon.conf"
        if avahi_conf.exists():
            content = avahi_conf.read_text()
            # Ensure publish-workstation is on for easy discovery
            if "publish-workstation" not in content:
                content += "\n[publish]\npublish-workstation=yes\npublish-hinfo=yes\n"
                avahi_conf.write_text(content)
        else:
            avahi_conf.parent.mkdir(parents=True, exist_ok=True)
            avahi_conf.write_text(
                "[server]\n"
                "use-ipv4=yes\n"
                "use-ipv6=yes\n"
                "ratelimit-interval-usec=1000000\n"
                "ratelimit-burst=1000\n\n"
                "[wide-area]\n"
                "enable-wide-area=yes\n\n"
                "[publish]\n"
                "publish-hinfo=yes\n"
                "publish-workstation=yes\n"
                "publish-domain=yes\n\n"
                "[reflector]\n\n"
                "[rlimits]\n"
                "rlimit-core=0\n"
                "rlimit-data=4194304\n"
                "rlimit-fsize=0\n"
                "rlimit-nofile=768\n"
                "rlimit-stack=4194304\n"
                "rlimit-nproc=3\n"
            )

        # ── firewalld: open mDNS and Samba client ports in the default zone ──
        firewalld_services_dir = self.target_root / "etc" / "firewalld" / "zones"
        firewalld_services_dir.mkdir(parents=True, exist_ok=True)
        # Drop-in to ensure mdns and samba-client are allowed in FedoraWorkstation
        fw_live = self.target_root / "etc" / "firewalld" / "zones" / "FedoraWorkstation.xml"
        if not fw_live.exists():
            fw_live.write_text(
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<zone>\n'
                '  <short>FedoraWorkstation</short>\n'
                '  <description>Fedora Live default zone</description>\n'
                '  <service name="mdns"/>\n'
                '  <service name="samba-client"/>\n'
                '  <service name="ssh"/>\n'
                '  <service name="dhcpv6-client"/>\n'
                '</zone>\n'
            )

        logger.info("Configured NetworkManager, mDNS/Avahi, and firewalld for live sharing")

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
        """
        Install Calamares configuration, branding and the desktop-icon autostart
        script when with_calamares is enabled.

        Files are sourced from configs/custom_files/calamares/ (settings.conf,
        branding/, modules/) and configs/custom_files/scripts/.
        The copy_custom_files() step will overlay configs/custom_files/autostart/
        into /etc/xdg/autostart/ automatically, so only the script itself needs
        to be placed here.
        """
        if self.chroot.mode == "mock":
            return
        if not self.config.get("with_calamares", False):
            return

        from fedora_builder.core.path_utils import resolve_from_project
        project_root = resolve_from_project("")

        # ── Calamares config tree (/etc/calamares/) ───────────────────────────
        src_calamares = project_root / "configs" / "custom_files" / "calamares"
        dst_calamares = self.target_root / "etc" / "calamares"

        if src_calamares.exists():
            dst_calamares.mkdir(parents=True, exist_ok=True)

            # settings.conf
            src_settings = src_calamares / "settings.conf"
            if src_settings.exists():
                shutil.copy2(src_settings, dst_calamares / "settings.conf")

            # branding/
            src_branding = src_calamares / "branding"
            if src_branding.exists():
                dst_branding = dst_calamares / "branding"
                dst_branding.mkdir(parents=True, exist_ok=True)
                shutil.copytree(src_branding, dst_branding, dirs_exist_ok=True,
                                symlinks=True, ignore_dangling_symlinks=True)

            # modules/
            src_modules = src_calamares / "modules"
            if src_modules.exists():
                dst_modules = dst_calamares / "modules"
                dst_modules.mkdir(parents=True, exist_ok=True)
                shutil.copytree(src_modules, dst_modules, dirs_exist_ok=True,
                                symlinks=True, ignore_dangling_symlinks=True)

            logger.info(f"Installed Calamares config from {src_calamares} → {dst_calamares}")
        else:
            logger.warning("configs/custom_files/calamares/ not found — skipping Calamares config install")

        # ── Desktop-icon autostart script (/usr/local/bin/) ──────────────────
        src_script = project_root / "configs" / "custom_files" / "scripts" / "add-installer-desktop-icon.sh"
        if src_script.exists():
            dst_script = self.target_root / "usr" / "local" / "bin" / "add-installer-desktop-icon.sh"
            dst_script.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_script, dst_script)
            dst_script.chmod(0o755)

        # ── Autostart .desktop (/etc/xdg/autostart/) ─────────────────────────
        src_autostart = project_root / "configs" / "custom_files" / "autostart" / "create-install-icon.desktop"
        if src_autostart.exists():
            dst_autostart_dir = self.target_root / "etc" / "xdg" / "autostart"
            dst_autostart_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_autostart, dst_autostart_dir / "create-install-icon.desktop")

        # ── Polkit rule: allow pkexec calamares without password ──────────────
        polkit_dir = self.target_root / "etc" / "polkit-1" / "rules.d"
        polkit_dir.mkdir(parents=True, exist_ok=True)
        (polkit_dir / "49-calamares.rules").write_text(
            "/* Allow live user to launch calamares installer via pkexec */\n"
            "polkit.addRule(function(action, subject) {\n"
            "    if (action.id === 'org.freedesktop.policykit.exec' &&\n"
            "        action.lookup('program') === '/usr/bin/calamares') {\n"
            "        return polkit.Result.YES;\n"
            "    }\n"
            "});\n"
        )

    def copy_custom_files(self):
        """
        Copies custom files and overlays into the target rootfs chroot.
        Supports both:
        1. Direct rootfs overlay from configs/custom_files/ -> /
        2. Structured JSON custom_files / copy_files entries mapping source -> destination.
        """
        if self.chroot.mode == "mock":
            logger.info("[MOCK CUSTOMIZER] Simulating copying custom files into chroot.")
            return

        from fedora_builder.core.path_utils import resolve_from_project
        project_root = resolve_from_project("")
        custom_files_dir = project_root / "configs" / "custom_files"

        # 1. Direct overlay from configs/custom_files/ -> target_root/
        if custom_files_dir.exists() and custom_files_dir.is_dir():
            for item in custom_files_dir.iterdir():
                if item.name == ".gitkeep":
                    continue
                dest_path = self.target_root / item.name
                if item.is_dir():
                    shutil.copytree(item, dest_path, dirs_exist_ok=True, symlinks=True, ignore_dangling_symlinks=True)
                else:
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, dest_path)

        # 2. Structured list from JSON config
        custom_files_list = list(self.config.get("custom_files", []))
        copy_files = self.config.get("copy_files", [])
        if isinstance(copy_files, list):
            for entry in copy_files:
                if entry not in custom_files_list:
                    custom_files_list.append(entry)

        desktop_env = self.config.get("desktop_environment", {})
        if isinstance(desktop_env, dict):
            for entry in desktop_env.get("copy_files", []):
                if entry not in custom_files_list:
                    custom_files_list.append(entry)

        if not custom_files_list:
            return

        py_ver = "3.12"
        python_dirs = list(self.target_root.glob("usr/lib/python3.*"))
        if python_dirs:
            py_ver = python_dirs[0].name.replace("python", "")

        for entry in custom_files_list:
            if not isinstance(entry, dict):
                continue
            src_rel = entry.get("source")
            dest_rel = entry.get("destination")
            if not src_rel or not dest_rel:
                continue

            dest_rel = dest_rel.format(python_version=py_ver)
            src_path = custom_files_dir / src_rel
            if not src_path.exists():
                src_path = project_root / src_rel
            dest_path = self.target_root / dest_rel.lstrip("/")

            if not src_path.exists():
                logger.warning(f"Custom file source path does not exist, skipping: {src_path}")
                continue

            dest_path.parent.mkdir(parents=True, exist_ok=True)
            if src_path.is_dir():
                shutil.copytree(src_path, dest_path, dirs_exist_ok=True, symlinks=True, ignore_dangling_symlinks=True)
            else:
                shutil.copy2(src_path, dest_path)

            mode_str = entry.get("permissions")
            if mode_str:
                try:
                    mode = int(mode_str, 8)
                    dest_path.chmod(mode)
                except Exception:
                    pass

    def configure_live_environment(self):
        self.setup_live_users()
        self.configure_system_defaults()
        self.setup_services()
        self.configure_autologin()
        self.configure_plymouth()
        self.configure_selinux()
        self.configure_zram()
        self.configure_live_performance()
        self.configure_network_sharing()
        self.configure_flathub()
        self.configure_polkit_power()
        self.configure_calamares()
        self.configure_artwork()
        self.copy_custom_files()

    def configure_artwork(self):
        """Install custom Fedora Modern artwork and set default wallpaper."""
        if self.chroot.mode == "mock":
            return
        bg_dir = self.target_root / "usr" / "share" / "backgrounds" / "fedora-modern"
        bg_dir.mkdir(parents=True, exist_ok=True)
        from fedora_builder.core.path_utils import resolve_from_project
        artwork_src = resolve_from_project("artwork/wallpapers/fedora-modern.jpg")
        if artwork_src.exists():
            import shutil
            shutil.copy2(artwork_src, bg_dir / "fedora-modern.jpg")
        self.copy_custom_files()

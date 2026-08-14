from pathlib import Path

from fedora_builder.core.customizer import SystemCustomizer


class FakeChroot:
    def __init__(self, target_root: Path):
        self.mode = "real"
        self.target_root = target_root

    def run_in_chroot(self, *args, **kwargs):
        return None


def test_configure_anaconda_writes_launcher_and_service(tmp_path):
    customizer = SystemCustomizer(
        FakeChroot(tmp_path),
        {"installer": "anaconda"},
    )

    customizer.configure_anaconda()

    launcher = tmp_path / "usr" / "local" / "sbin" / "start-anaconda-server-installer.sh"
    service = tmp_path / "etc" / "systemd" / "system" / "anaconda-launch.service"
    symlink = tmp_path / "etc" / "systemd" / "system" / "multi-user.target.wants" / "anaconda-launch.service"

    assert launcher.exists()
    assert service.exists()
    assert symlink.is_symlink()
    assert "--text" in launcher.read_text()
    assert "grep -qw 'inst.text' /proc/cmdline" in launcher.read_text()


def test_setup_live_users_accepts_string_group_list(tmp_path):
    customizer = SystemCustomizer(
        FakeChroot(tmp_path),
        {"live_user": "demo", "live_groups": "wheel,audio"},
    )

    customizer.setup_live_users()

    sudoers = tmp_path / "etc" / "sudoers.d" / "wheel_nopasswd"
    assert sudoers.exists()
    assert "%wheel ALL=(ALL) NOPASSWD: ALL" in sudoers.read_text()

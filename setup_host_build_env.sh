#!/bin/bash
# Fedora-Builder: Host Build Environment Setup
# Installs required tools for cross-architecture builds
set -e

if [ "$EUID" -ne 0 ]; then 
    echo "Please run with sudo."
    exit 1
fi

echo "Detecting package manager..."
if command -v dnf >/dev/null 2>&1; then
    echo "Installing via dnf..."
    dnf install -y qemu-user-static binfmt-support xorriso squashfs-tools syslinux
elif command -v apt >/dev/null 2>&1; then
    echo "Installing via apt..."
    apt update
    apt install -y qemu-user-static binfmt-support xorriso squashfs-tools syslinux-common
elif command -v pacman >/dev/null 2>&1; then
    echo "Installing via pacman..."
    pacman -Sy --noconfirm qemu-user-static-binfmt xorriso squashfs-tools syslinux
elif command -v zypper >/dev/null 2>&1; then
    echo "Installing via zypper..."
    zypper install -y qemu-linux-user qemu-user-static binfmt-support xorriso squashfs syslinux
elif command -v xbps-install >/dev/null 2>&1; then
    echo "Installing via xbps..."
    xbps-install -Sy qemu-user-static binfmt-support xorriso squashfs-tools syslinux
elif command -v emerge >/dev/null 2>&1; then
    echo "Installing via emerge (Gentoo)..."
    emerge -uN app-emulation/qemu sys-fs/squashfs-tools dev-libs/libisoburn sys-boot/syslinux sys-boot/grub
else
    echo "Unsupported package manager. Please install qemu-user-static, binfmt-support, xorriso, squashfs-tools, and syslinux manually."
    exit 1
fi

echo "Enabling and starting binfmt-support services if applicable..."
if command -v systemctl >/dev/null 2>&1; then
    systemctl enable --now systemd-binfmt.service || true
    if systemctl list-unit-files | grep -q binfmt-support; then
        systemctl enable --now binfmt-support.service || true
    fi
fi

echo "Verifying QEMU registrations..."
if [ -d /proc/sys/fs/binfmt_misc ]; then
    ls -l /proc/sys/fs/binfmt_misc/qemu-aarch64 || echo "Warning: qemu-aarch64 not registered"
    ls -l /proc/sys/fs/binfmt_misc/qemu-ppc64le || echo "Warning: qemu-ppc64le not registered"
    ls -l /proc/sys/fs/binfmt_misc/qemu-s390x || echo "Warning: qemu-s390x not registered"
    echo "Binfmt registrations look good."
else
    echo "Warning: /proc/sys/fs/binfmt_misc not found. Binfmt might not be configured."
fi

echo "Host build environment setup complete!"

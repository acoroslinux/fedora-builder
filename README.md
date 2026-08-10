# Fedora-Builder

**Modular and Dynamic Fedora Linux ISO & Image Builder**

`fedora-builder` is a Python-based build system for creating customized Fedora Linux live ISOs, raw disk images, rootfs tarballs, and OCI container images. It follows the same modular, profile-driven architecture as its sibling builders (`arch-builder`, `void-builder`, `gentoo-builder`) while embracing Fedora's unique ecosystem: DNF, RPM, systemd, Kickstart, SELinux, Secure Boot, and the Fedora LiveOS squashfs structure.

---

## Features

- 🎯 **Profile-Driven**: JSON profiles for desktops, packages, services, repos, releases, variants, kernels, and bootloaders
- 🐧 **Fedora-Native**: DNF/RPM package management with `dnf --installroot` bootstrap
- 🏗️ **Multiple Output Formats**: `iso`, `img` (raw disk), `tarball` (rootfs tar.xz), `container` (OCI/Podman)
- 🏛️ **Multi-Architecture**: x86_64, aarch64, ppc64le, s390x
- 🔒 **Secure Boot Ready**: shim + GRUB2 EFI with GPT hybrid partitioning
- 📦 **COPR Support**: Easy integration of COPR repositories (like AUR for RPM)
- 🎨 **Desktop Environments**: GNOME, KDE, XFCE, MATE, Cinnamon, LXQt, i3, Sway, Hyprland
- 📋 **Kickstart Generation**: Auto-generates `.ks` files from JSON configs
- 🔍 **Mock Mode**: Full build simulation without root privileges (ideal for CI)
- ✅ **Config Validation**: `--validate` mode checks profiles and dependencies
- 🧩 **Fedora Releases**: F40, F41, Rawhide — all fully supported
- 🚀 **RPM Fusion Ready**: Seamless multimedia/codec support via RPM Fusion repos

---

## Requirements

### Host Requirements (for real builds)

- Fedora / RHEL / compatible host (for native DNF) **or** Podman/Docker (for isolated builds)
- Python 3.10+
- Root / sudo access for `real` mode

### Host Tools (auto-detected)

```
dnf        xorriso     mksquashfs    grub2-mkrescue
syslinux   mtools      dracut        dosfstools
```

### For Cross-Architecture Builds

Run the setup script to install QEMU user-mode emulators:

```bash
sudo bash setup_host_build_env.sh
```

---

## Quick Start

```bash
# Clone and enter
git clone <repo-url> fedora-builder
cd fedora-builder

# Simulate a GNOME F41 ISO build (no root required)
python cli.py x86_64 --desktop gnome --release fedora-41 --mode mock

# Build a real GNOME ISO (requires root)
sudo python cli.py x86_64 --desktop gnome --release fedora-41 --mode real

# Build KDE ISO with RPM Fusion multimedia support
sudo python cli.py x86_64 --desktop kde --release fedora-41 \
  --repo rpmfusion-free --repo rpmfusion-nonfree \
  --package-profile multimedia --mode real

# Build XFCE with Calamares installer
sudo python cli.py x86_64 --desktop xfce --with-calamares --mode real

# Build for aarch64 (cross-compile via QEMU)
sudo python cli.py aarch64 --desktop gnome --mode real

# Generate a raw disk image instead of ISO
sudo python cli.py x86_64 --desktop gnome --format img --mode real

# Generate a Kickstart file without building
python cli.py x86_64 --desktop kde --kickstart-only

# List all available profiles
python cli.py --list-options

# Validate configuration without building
python cli.py x86_64 --desktop gnome --validate
```

---

## CLI Reference

```
python cli.py [architecture] [OPTIONS]

Architectures:
  x86_64 (default), aarch64, ppc64le, s390x

Core Options:
  -c, --config PATH         Global config file (default: configs/global_build.json)
  --mode mock|real          Execution mode (default: mock)
  --clean / --no-clean      Pre-build cleanup (default: --clean)
  --force-isolated-toolchain Force isolated DNF bootstrap

Fedora-Specific:
  --release PROFILE         Fedora release (fedora-40, fedora-41, rawhide)
  --variant PROFILE         Image variant (live, minimal, server, cloud, iot)
  --copr USER/PROJECT       Enable COPR repo (repeatable)
  -R, --repo PROFILE        Extra DNF repo profile (repeatable)
  --generate-kickstart      Generate .ks alongside build
  --kickstart-only          Generate .ks only, no build
  --with-calamares          Include Calamares GUI installer

Customization:
  -d, --desktop PROFILE     Desktop environment
  -k, --kernel PROFILE      Kernel profile (kernel, kernel-lts)
  -b, --bootloader PROFILE  Bootloader (grub2-hybrid, grub2-efi, grub2-bios)
  -p, --package-profile     Package profiles (repeatable, comma-separated)
  -s, --service-profile     Service profiles (repeatable, comma-separated)
  --live-user USERNAME      Live user name
  --live-profile PROFILE    Live user profile (admin, guest)
  --live-groups wheel,...   Live user groups

Output:
  --format iso|img|tarball|container  Output format (default: iso)
  --compression xz|zstd|gzip          Compression (default: zstd)
  -o, --output FILENAME               Output filename

Information:
  --list-options            List all available profiles
  --check / --validate      Validate config without building
  -v, --verbose             Verbose logging
```

---

## Project Structure

```
fedora-builder/
├── cli.py                          ← Entry point
├── configs/
│   ├── global_build.json           ← Global defaults
│   ├── architectures/              ← x86_64, aarch64, ppc64le, s390x
│   ├── bootloaders/                ← grub2-hybrid, grub2-efi, grub2-bios
│   ├── desktops/                   ← gnome, kde, xfce, mate, cinnamon, lxqt, i3, sway, hyprland
│   ├── kernels/                    ← kernel, kernel-lts
│   ├── packages/                   ← base, audio, bluetooth, development, multimedia, ...
│   ├── releases/                   ← fedora-40, fedora-41, rawhide
│   ├── repos/                      ← fedora, updates, rpmfusion-free, rpmfusion-nonfree, copr-*
│   ├── services/                   ← base, desktop, network, hardware
│   ├── variants/                   ← live, minimal, server, cloud, iot
│   └── live-users/                 ← admin, guest
├── fedora_builder/
│   ├── core/
│   │   ├── config_loader.py        ← JSON profile loading & deep merge
│   │   ├── toolchain_manager.py    ← Host tool detection & bootstrap
│   │   ├── chroot_manager.py       ← Chroot env + virtual fs + QEMU
│   │   ├── dnf_manager.py          ← DNF/RPM operations (KEY MODULE)
│   │   ├── kickstart_manager.py    ← Kickstart .ks generation (UNIQUE)
│   │   ├── customizer.py           ← Post-install system configuration
│   │   ├── iso_engine.py           ← ISO + LiveOS assembly
│   │   ├── disk_engine.py          ← Raw disk image creation
│   │   ├── orchestrator.py         ← Build pipeline coordination
│   │   └── bootloaders/grub2.py   ← GRUB2 bootloader generation
│   └── utils/
│       └── command.py              ← Command execution with streaming
└── tests/
    ├── test_config_loader.py
    ├── test_dnf_manager.py
    ├── test_orchestrator.py
    └── test_iso_engine.py
```

### Cache Layout

All persistent build cache lives below the configured `system.dnf_cache` root
(`cache/` by default):

```
cache/
├── packages/<release>/<arch>/dnf/       ← DNF package metadata and RPM cache
├── rootfs-seeds/<release>/<arch>/       ← reusable bootstrapped rootfs tarballs
└── toolchain/<release>/<arch>/          ← Fedora minimal OCI/toolchain layers
```

Older paths such as `cache/dnf`, `cache/<arch>/dnf`, and
`cache/seed-fedora-*.tar.gz` are legacy cache locations and are no longer used.

---

## Fedora LiveOS ISO Structure

Unlike arch-builder or void-builder which embed squashfs directly, Fedora uses the **LiveOS** layout:

```
ISO/
├── .discinfo                 ← Fedora disc metadata
├── .treeinfo                 ← Repository tree metadata
├── EFI/BOOT/
│   ├── BOOTX64.EFI           ← shim (Secure Boot chain)
│   └── grubx64.efi           ← GRUB2 EFI
├── LiveOS/
│   └── squashfs.img          ← SquashFS of the rootfs directory
├── images/
│   ├── efiboot.img           ← FAT EFI boot image
│   └── pxeboot/
│       ├── vmlinuz           ← Kernel
│       └── initrd.img        ← Dracut initramfs (with dmsquash-live)
├── isolinux/                 ← BIOS/legacy boot (x86_64 only)
│   ├── isolinux.bin
│   └── isolinux.cfg
└── boot/grub2/
    └── grub.cfg              ← Boot params: root=live:CDLABEL=...
```

**Boot parameters**: `root=live:CDLABEL=FEDORA-LIVE rd.live.image quiet rhgb`

---

## Configuration

### Profile Inheritance Order

```
global_build.json
  └── releases/fedora-41.json       ← Fedora version metadata
    └── architectures/x86_64.json   ← Arch-specific packages & EFI config
      └── variants/live.json        ← Variant-specific settings
        └── desktops/gnome.json     ← Desktop packages, DM, session type
          └── kernels/kernel.json   ← Kernel packages
            └── bootloaders/grub2-hybrid.json
              └── packages/base.json + packages/*.json (profiles)
                └── services/*.json
                  └── repos/*.json
                    └── live-users/admin.json
```

### Example: Custom XFCE ISO with RPM Fusion

```bash
python cli.py x86_64 \
  --release fedora-41 \
  --desktop xfce \
  --repo rpmfusion-free \
  --repo rpmfusion-nonfree \
  --package-profile multimedia \
  --package-profile development \
  --with-calamares \
  --live-profile admin \
  --format iso \
  --compression zstd \
  --mode real
```

### Example: Minimal Server Tarball

```bash
python cli.py x86_64 \
  --release fedora-41 \
  --variant server \
  --format tarball \
  --no-manifest \
  --mode real
```

---

## Execution Modes

| Mode | Description | Requires Root | Network | Result |
|---|---|---|---|---|
| `mock` | Full simulation, no system changes | No | No | Dummy artifact files |
| `real` | Actual build with DNF + chroot | Yes | Yes | Real ISO/img/tarball |

---

## Running Tests

```bash
# Run all tests (mock mode, no root required)
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_config_loader.py -v
```

---

## License

MIT License — Copyright (c) 2026 AcorOS Linux

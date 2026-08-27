#!/usr/bin/env python3
"""
cli.py — Fedora-Builder Entry Point

Modular and Dynamic Fedora Linux ISO & Image Builder.
Follows the same CLI pattern as arch-builder, void-builder, and gentoo-builder.
"""

import argparse
import re
import sys
from pathlib import Path

from fedora_builder.core.orchestrator import BuildOrchestrator, BuildOrchestratorError
from fedora_builder.core.path_utils import resolve_from_project


def _available_profiles(config_root: Path, category: str):
    category_dir = config_root / category
    if not category_dir.exists() or not category_dir.is_dir():
        return []
    return sorted([p.stem for p in category_dir.glob("*.json")])


def _slugify_name(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", (value or "").strip().lower())
    normalized = normalized.strip("-._")
    return normalized or fallback


def _parse_list_arg(arg_value) -> list:
    if not arg_value:
        return []
    items = []
    if isinstance(arg_value, list):
        for val in arg_value:
            if isinstance(val, list):
                for inner in val:
                    items.extend([x.strip() for x in inner.split(",") if x.strip()])
            elif isinstance(val, str):
                items.extend([x.strip() for x in val.split(",") if x.strip()])
    elif isinstance(arg_value, str):
        items.extend([x.strip() for x in arg_value.split(",") if x.strip()])
    return items


def _resolve_output_name(
    architecture: str,
    release: str,
    desktop: str = None,
    output: str = None,
    output_format: str = "iso",
) -> str:
    if output:
        return output

    ext_map = {
        "iso": "iso",
        "img": "img",
        "tarball": "tar.xz",
        "container": "tar",
    }
    ext = ext_map.get(output_format, "iso")

    desktop_label = _slugify_name(desktop or "base", "base")
    arch_label = _slugify_name(architecture, "x86_64")
    release_label = _slugify_name(release or "fedora", "fedora")
    return f"fedora-builder-{release_label}-{desktop_label}-{arch_label}.{ext}"


VALID_ARCHS = ("x86_64", "aarch64", "ppc64le", "s390x")


def main():
    default_config_path = resolve_from_project("configs/global_build.json")

    parser = argparse.ArgumentParser(
        description="Fedora-Builder: Modular and Dynamic Fedora Linux ISO & Image Builder",
        epilog="Use --help to see a detailed list of available arguments.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # ── Positional ─────────────────────────────────────────────────────────────
    parser.add_argument(
        "architecture",
        nargs="?",
        default="x86_64",
        help="Target architecture (x86_64, aarch64, ppc64le, s390x). Default: x86_64",
    )

    # ── Configuration & Environment ────────────────────────────────────────────
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        default=str(default_config_path),
        help="Path to the global configuration JSON file. Default: configs/global_build.json",
    )

    parser.add_argument(
        "--mode",
        choices=["mock", "real"],
        default="mock",
        help="Execution mode: 'mock' (simulation, no root required) or 'real' (actual build, requires root). Default: mock",
    )

    clean_group = parser.add_mutually_exclusive_group()
    clean_group.add_argument(
        "--clean",
        dest="clean",
        action="store_true",
        help="Clean previous build artifacts before starting (default).",
    )
    clean_group.add_argument(
        "--no-clean",
        dest="clean",
        action="store_false",
        help="Reuse previous build tree without pre-build cleanup.",
    )
    parser.set_defaults(clean=True)

    parser.add_argument(
        "--force-isolated-toolchain",
        action="store_true",
        help="Force isolated toolchain bootstrap even if host DNF tools are available.",
    )

    # ── Fedora-Specific Configuration ──────────────────────────────────────────
    parser.add_argument(
        "--release",
        type=str,
        default=None,
        help="Fedora release profile (e.g. fedora-40, fedora-41, rawhide). Default: from global_build.json",
    )

    parser.add_argument(
        "--variant",
        type=str,
        default="live",
        help="Image variant profile (live, minimal, server, cloud, iot). Default: live",
    )

    parser.add_argument(
        "--copr",
        action="append",
        default=[],
        metavar="USER/PROJECT",
        help="Enable a COPR repository (e.g. arivenitez/calamares). Can be specified multiple times.",
    )

    parser.add_argument(
        "-R",
        "--repo",
        action="append",
        default=[],
        metavar="REPO_PROFILE",
        help="Add an extra DNF repo profile from configs/repos/ (e.g. rpmfusion-free). Can be specified multiple times.",
    )

    parser.add_argument(
        "--generate-kickstart",
        action="store_true",
        help="Generate a Kickstart (.ks) file alongside the build artifact.",
    )

    parser.add_argument(
        "--kickstart-only",
        action="store_true",
        help="Generate a Kickstart (.ks) file without performing the full build.",
    )

    parser.add_argument(
        "--with-calamares",
        action="store_true",
        help="Include the Calamares graphical installer in the ISO.",
    )

    parser.add_argument(
        "--multimedia-codecs",
        action="store_true",
        help="Automatically enable RPM Fusion repos and install complete H.264/AAC/FFmpeg multimedia codecs.",
    )

    parser.add_argument(
        "--with-flathub",
        action="store_true",
        help="Configure Flathub Flatpak repository on first boot.",
    )

    parser.add_argument(
        "--cloud-init",
        action="store_true",
        help="Include and enable cloud-init for cloud/server provisioning.",
    )

    parser.add_argument(
        "--gaming-tweaks",
        action="store_true",
        help="Apply aggressive sysctl, CPU governor, and IO performance tweaks.",
    )

    parser.add_argument(
        "--fs-type",
        choices=["ext4", "btrfs"],
        default="ext4",
        help="Root filesystem type for disk images. Default: ext4",
    )

    parser.add_argument(
        "--with-zram",
        action="store_true",
        help="Configure systemd-zram-generator for RAM compressed swap.",
    )

    # ── Customization Overrides ─────────────────────────────────────────────────
    parser.add_argument(
        "-d",
        "--desktop",
        type=str,
        default=None,
        help="Desktop environment profile (gnome, kde, xfce, mate, cinnamon, lxqt, i3, sway, hyprland).",
    )

    parser.add_argument(
        "-k",
        "--kernel",
        type=str,
        default="kernel",
        help="Kernel profile (kernel, kernel-lts). Default: kernel",
    )

    parser.add_argument(
        "-b",
        "--bootloader",
        type=str,
        default="grub2-hybrid",
        help="Bootloader profile (grub2-hybrid, grub2-efi, grub2-bios, systemd-boot). Default: grub2-hybrid",
    )

    parser.add_argument(
        "-p",
        "--package-profile",
        "--packages",
        "--package",
        nargs="+",
        action="append",
        default=[],
        help="Package profile from configs/packages. Can be provided multiple times (comma or space separated).",
    )

    parser.add_argument(
        "-s",
        "--service-profile",
        "--services",
        "--service",
        nargs="+",
        action="append",
        default=[],
        help="Service profile from configs/services. Can be provided multiple times.",
    )

    parser.add_argument(
        "--live-user",
        type=str,
        help="Override live ISO username.",
    )

    parser.add_argument(
        "--live-profile",
        type=str,
        help="Live user profile name from configs/live-users (admin, guest).",
    )

    parser.add_argument(
        "--live-groups",
        type=str,
        help="Comma-separated group list for live user (e.g. wheel,audio,video).",
    )

    # ── Output & Format ─────────────────────────────────────────────────────────
    parser.add_argument(
        "--format",
        choices=["iso", "img", "tarball", "container"],
        default="iso",
        help="Build artifact format: iso (default), img (raw disk), tarball (rootfs tar.xz), container (OCI tar).",
    )

    parser.add_argument(
        "--compression",
        choices=["xz", "zstd", "gzip"],
        default="zstd",
        help="SquashFS compression algorithm. Default: zstd",
    )

    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=None,
        help="Output file name. Default: fedora-builder-<release>-<desktop>-<arch>.<ext>",
    )

    manifest_group = parser.add_mutually_exclusive_group()
    manifest_group.add_argument(
        "--generate-manifest",
        dest="generate_manifest",
        action="store_true",
        help="Generate SHA256/MD5 checksums and manifest.json (default).",
    )
    manifest_group.add_argument(
        "--no-manifest",
        dest="generate_manifest",
        action="store_false",
        help="Disable checksum and manifest generation.",
    )
    parser.set_defaults(generate_manifest=True)

    # ── Information & Diagnostics ───────────────────────────────────────────────
    parser.add_argument(
        "--list-options",
        action="store_true",
        help="List all available profiles and exit.",
    )

    parser.add_argument(
        "--check",
        "--validate",
        dest="validate_only",
        action="store_true",
        help="Validate configuration without building.",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging.",
    )

    args = parser.parse_args()

    # ── --list-options ──────────────────────────────────────────────────────────
    config_root = resolve_from_project("configs")
    if args.list_options:
        print("Available Fedora-Builder profiles:")
        categories = [
            ("architectures",  "architectures"),
            ("releases",       "releases      "),
            ("variants",       "variants      "),
            ("desktops",       "desktops      "),
            ("kernels",        "kernels       "),
            ("bootloaders",    "bootloaders   "),
            ("packages",       "packages      "),
            ("services",       "services      "),
            ("repos",          "repos         "),
            ("live-users",     "live-users    "),
        ]
        for dir_name, label in categories:
            profs = _available_profiles(config_root, dir_name)
            print(f"  {label}: {', '.join(profs) if profs else '(none)'}")
        sys.exit(0)

    # ── Architecture validation ─────────────────────────────────────────────────
    arch_lower = args.architecture.lower()
    if arch_lower not in VALID_ARCHS:
        print(
            f"Error: Architecture '{args.architecture}' is not supported.\n"
            f"Supported architectures: {', '.join(VALID_ARCHS)}"
        )
        sys.exit(1)
    args.architecture = arch_lower

    # ── Config file validation ──────────────────────────────────────────────────
    config_path = resolve_from_project(args.config)
    if not config_path.exists():
        print(f"Error: Configuration file '{config_path}' not found.")
        sys.exit(1)

    # ── Parse list arguments ────────────────────────────────────────────────────
    parsed_package_profiles = _parse_list_arg(args.package_profile)
    parsed_service_profiles = _parse_list_arg(args.service_profile)
    parsed_repo_profiles = list(args.repo)
    parsed_copr_repos = list(args.copr)

    parsed_live_groups = None
    if args.live_groups:
        parsed_live_groups = [g.strip() for g in args.live_groups.split(",") if g.strip()]

    # ── Resolve output name ─────────────────────────────────────────────────────
    release = args.release or "fedora-41"
    output_name = _resolve_output_name(
        architecture=args.architecture,
        release=release,
        desktop=args.desktop,
        output=args.output,
        output_format=args.format,
    )

    # ── Build Orchestrator ──────────────────────────────────────────────────────
    orchestrator = BuildOrchestrator(
        arch=args.architecture,
        config_path=str(config_path),
        mode=args.mode,
        clean=args.clean,
        release=release,
        desktop=args.desktop,
        kernel=args.kernel,
        bootloader=args.bootloader,
        variant=args.variant,
        package_profiles=parsed_package_profiles,
        service_profiles=parsed_service_profiles,
        repo_profiles=parsed_repo_profiles,
        live_profile=args.live_profile,
        live_user=args.live_user,
        live_groups=parsed_live_groups,
        output_format=args.format,
        compression=args.compression,
        generate_manifest=args.generate_manifest,
        generate_kickstart=args.generate_kickstart,
        with_calamares=args.with_calamares,
        force_isolated_toolchain=args.force_isolated_toolchain,
        copr_repos=parsed_copr_repos,
        multimedia_codecs=args.multimedia_codecs,
        with_flathub=args.with_flathub,
        with_zram=args.with_zram,
        cloud_init=args.cloud_init,
        gaming_tweaks=args.gaming_tweaks,
        fs_type=args.fs_type,
    )

    # ── --validate mode ─────────────────────────────────────────────────────────
    if args.validate_only:
        print(f"\n🔍 Validating configuration for '{args.architecture}' / '{release}'...")
        report = orchestrator.validate()
        if report.get("valid"):
            print("✅ Configuration is VALID!")
            summary = report.get("summary", {})
            print("Summary:")
            print(f"  Target Architecture : {summary.get('target_arch', args.architecture)}")
            print(f"  Fedora Release      : {summary.get('release', release)}")
            print(f"  Desktop Profile     : {summary.get('desktop', '(none)')}")
            print(f"  Variant             : {summary.get('variant', 'live')}")
            print(f"  FS Type             : {args.fs_type}")
            print(f"  Gaming Tweaks       : {'Yes' if args.gaming_tweaks else 'No'}")
            print(f"  Cloud Init          : {'Yes' if args.cloud_init else 'No'}")
            print(f"  Total Packages      : {summary.get('total_packages', 0)}")
            print(f"  DNF Groups          : {', '.join(summary.get('groups', []))}")
            print(f"  Enabled Services    : {', '.join(summary.get('services', []))}")
            print(f"  Repos               : {', '.join(summary.get('repos', []))}")
            sys.exit(0)
        else:
            print("❌ Configuration validation FAILED!")
            for err in report.get("errors", []):
                print(f"  - ERROR: {err}")
            sys.exit(1)

    # ── --kickstart-only mode ───────────────────────────────────────────────────
    if args.kickstart_only:
        print(f"\n📋 Generating Kickstart file for '{args.architecture}' / '{release}'...")
        ks_path = orchestrator.generate_kickstart_only(output_name)
        print(f"✅ Kickstart file written to: {ks_path}")
        sys.exit(0)

    # ── Print build summary ─────────────────────────────────────────────────────
    print("─" * 50)
    print("  Fedora-Builder")
    print("─" * 50)
    print(f"  Architecture : {args.architecture}")
    print(f"  Release      : {release}")
    print(f"  Mode         : {args.mode}")
    print(f"  Variant      : {args.variant or 'live'}")
    print(f"  Format       : {args.format}")
    print(f"  Compression  : {args.compression}")
    print(f"  Manifest     : {'enabled' if args.generate_manifest else 'disabled'}")
    print(f"  Clean        : {'yes' if args.clean else 'no'}")
    if args.force_isolated_toolchain:
        print("  Toolchain    : forced isolated bootstrap")
    print(f"  Config       : {config_path}")
    print(f"  Output       : {output_name}")
    if args.desktop:
        print(f"  Desktop      : {args.desktop}")
    if args.kernel:
        print(f"  Kernel       : {args.kernel}")
    if args.bootloader:
        print(f"  Bootloader   : {args.bootloader}")
    if parsed_package_profiles:
        print(f"  Packages     : {', '.join(parsed_package_profiles)}")
    if parsed_service_profiles:
        print(f"  Services     : {', '.join(parsed_service_profiles)}")
    if parsed_repo_profiles:
        print(f"  Repos        : {', '.join(parsed_repo_profiles)}")
    if parsed_copr_repos:
        print(f"  COPR         : {', '.join(parsed_copr_repos)}")
    if args.live_profile:
        print(f"  Live Profile : {args.live_profile}")
    if args.live_user:
        print(f"  Live User    : {args.live_user} (override)")
    if parsed_live_groups:
        print(f"  Live Groups  : {', '.join(parsed_live_groups)}")
    if args.with_calamares:
        print("  Calamares    : included")
    if args.generate_kickstart:
        print("  Kickstart    : will be generated")
    print("─" * 50)
    print()

    # ── Run build ───────────────────────────────────────────────────────────────
    try:
        result_file = orchestrator.build(output_name)
        print(f"\n✅ Success! Build artifact created at: {result_file}")
    except BuildOrchestratorError as e:
        print(f"\n❌ Build Orchestration Error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n⚠️  Build interrupted by user.")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

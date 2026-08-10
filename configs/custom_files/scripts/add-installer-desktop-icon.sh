#!/bin/bash
# ==============================================================================
# Fedora Modern Installer - Universal Desktop Icon Creation Script
# ==============================================================================

# Only run in live mode (kernel parameter set by dracut LiveOS)
if grep -q "rd.live.image" /proc/cmdline; then
    live_user="liveuser"
    desktop_dir="/home/${live_user}/Desktop"
    mkdir -p "$desktop_dir"

    icon_file="$desktop_dir/Install Fedora Modern.desktop"

    cat << EOF > "$icon_file"
[Desktop Entry]
Version=1.0
Type=Application
Name=Install Fedora Modern
Name[es]=Instalar Fedora Modern
Name[fr]=Installer Fedora Modern
Name[pt]=Instalar Fedora Modern
Name[pt_BR]=Instalar Fedora Modern
Comment=Install Fedora Modern to disk
Exec=pkexec calamares
Icon=calamares
Terminal=false
Categories=System;
StartupNotify=true
EOF

    chmod +x "$icon_file"
    chown "${live_user}:${live_user}" "$icon_file" 2>/dev/null

    # Mark as trusted so desktop environments don't show security warnings
    gio set --type=string "$icon_file" metadata::trusted true 2>/dev/null

    # XFCE: bypass "Untrusted Launcher" by embedding the checksum
    gio set --type=string "$icon_file" metadata::xfce-exe-checksum \
        "$(sha256sum "$icon_file" | cut -f1 -d' ')" 2>/dev/null

    # KDE Plasma: refresh service menu cache
    if command -v kbuildsycoca5 >/dev/null 2>&1; then
        sudo -u "$live_user" kbuildsycoca5 --noincremental 2>/dev/null
    elif command -v kbuildsycoca6 >/dev/null 2>&1; then
        sudo -u "$live_user" kbuildsycoca6 --noincremental 2>/dev/null
    fi

    touch "$icon_file"
    echo "✓ Fedora Modern Installer icon created."
fi

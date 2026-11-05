#!/usr/bin/env bash
# Phosphor installer for Ubuntu: adds it to the app menu (sudo is only needed for python3-tk).
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
APPDIR="$HOME/.local/share/phosphor"
BIN="$HOME/.local/bin"
DESK="$HOME/.local/share/applications"

if ! python3 -c "import tkinter" 2>/dev/null; then
  echo "Installing python3-tk (needs sudo)..."
  sudo apt-get install -y python3-tk
fi

mkdir -p "$APPDIR" "$BIN" "$DESK"
cp "$HERE/phosphor.py" "$APPDIR/phosphor.py"

cat > "$APPDIR/phosphor.svg" <<'SVG'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" rx="14" fill="#07120b"/>
  <circle cx="32" cy="32" r="27" fill="#39ff88" opacity=".18"/>
  <circle cx="32" cy="32" r="22" fill="#39ff88"/>
  <circle cx="32" cy="32" r="11" fill="#07120b"/>
</svg>
SVG

cat > "$BIN/phosphor" <<EOF
#!/bin/sh
exec python3 "$APPDIR/phosphor.py" "\$@"
EOF
chmod +x "$BIN/phosphor"

cat > "$DESK/phosphor.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Phosphor
Comment=Lightweight API client
Exec=$BIN/phosphor
Icon=$APPDIR/phosphor.svg
Terminal=false
Categories=Development;Network;
StartupWMClass=Phosphor
EOF

# remove the old MiniPost launcher if it was installed (your saved data is migrated automatically)
rm -f "$DESK/minipost.desktop" "$BIN/minipost"
command -v update-desktop-database >/dev/null && update-desktop-database "$DESK" 2>/dev/null || true
echo "Done. Launch Phosphor from the app menu, or run: phosphor"
echo "(If 'phosphor' isn't found, log out and in so ~/.local/bin is on PATH.)"

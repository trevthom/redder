#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

VERSION="${VERSION:-1.0.0}"
PACKAGE="redder"
BUILD_ROOT="build/${PACKAGE}_${VERSION}_all"
DIST_DIR="dist"
DEB_PATH="${DIST_DIR}/${PACKAGE}_${VERSION}_all.deb"

rm -rf "$BUILD_ROOT"
mkdir -p \
    "$BUILD_ROOT/DEBIAN" \
    "$BUILD_ROOT/usr/bin" \
    "$BUILD_ROOT/usr/lib/${PACKAGE}" \
    "$BUILD_ROOT/usr/share/applications" \
    "$BUILD_ROOT/usr/share/icons/hicolor/scalable/apps" \
    "$BUILD_ROOT/etc/xdg/autostart" \
    "$BUILD_ROOT/usr/share/doc/${PACKAGE}" \
    "$DIST_DIR"

install -m 644 redder_app.py gamma_control.py redder.css "$BUILD_ROOT/usr/lib/${PACKAGE}/"
install -m 644 assets/redder.svg "$BUILD_ROOT/usr/share/icons/hicolor/scalable/apps/redder.svg"
install -m 644 packaging/com.redder.Redder.desktop "$BUILD_ROOT/usr/share/applications/com.redder.Redder.desktop"
install -m 644 packaging/redder-autostart.desktop "$BUILD_ROOT/etc/xdg/autostart/redder-autostart.desktop"
install -m 755 packaging/redder-launcher "$BUILD_ROOT/usr/bin/redder"
install -m 755 packaging/postinst "$BUILD_ROOT/DEBIAN/postinst"
install -m 755 packaging/postrm "$BUILD_ROOT/DEBIAN/postrm"
install -m 644 README.md "$BUILD_ROOT/usr/share/doc/${PACKAGE}/README.md"

INSTALLED_SIZE="$(du -sk "$BUILD_ROOT/usr" | cut -f1)"
sed -e "s/@VERSION@/${VERSION}/g" -e "s/@SIZE@/${INSTALLED_SIZE}/g" \
    packaging/control.in > "$BUILD_ROOT/DEBIAN/control"

if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate packaging/com.redder.Redder.desktop
    desktop-file-validate packaging/redder-autostart.desktop
fi

find "$BUILD_ROOT" -type d -exec chmod 755 {} +
find "$BUILD_ROOT" -type f -exec chmod go-w {} +
chmod 755 "$BUILD_ROOT/DEBIAN/postinst" "$BUILD_ROOT/DEBIAN/postrm"

rm -f "$DEB_PATH"
dpkg-deb --root-owner-group --build "$BUILD_ROOT" "$DEB_PATH" >/dev/null
sha256sum "$DEB_PATH" | tee "${DEB_PATH}.sha256"

echo
echo "Built: $DEB_PATH"

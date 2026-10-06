#!/bin/bash
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Spusť: sudo bash uninstall.sh [--purge]"; exit 1; }
PURGE=0
case "${1:-}" in '') ;; --purge) PURGE=1 ;; *) echo 'Použití: sudo bash uninstall.sh [--purge]'; exit 2;; esac
systemctl disable --now caracal-boot-info.service caracal-overlay.service caracal-player.service caracal-display.service caracal.service 2>/dev/null || true
pkill -u caracal -f '/usr/bin/chromium' 2>/dev/null || true
rm -f /etc/systemd/system/caracal-boot-info.service /etc/systemd/system/caracal-overlay.service /etc/systemd/system/caracal-player.service /etc/systemd/system/caracal-display.service /etc/systemd/system/caracal.service /etc/sudoers.d/caracal
rm -rf /opt/caracal
systemctl daemon-reload
systemctl reset-failed 2>/dev/null || true
if [ "$PURGE" -eq 1 ]; then
 rm -rf /var/lib/caracal
 userdel -r caracal 2>/dev/null || true
 rm -rf /usr/share/plymouth/themes/caracal
 plymouth-set-default-theme text 2>/dev/null || true
 update-initramfs -u -k all 2>/dev/null || true
 echo 'CARACAL byl odstraněn včetně dat, uživatele a splash screenu.'
else
 echo 'Aplikace byla odstraněna. Data zůstala v /var/lib/caracal.'
 echo 'Úplné odstranění: sudo bash uninstall.sh --purge'
fi

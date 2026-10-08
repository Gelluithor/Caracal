#!/bin/bash
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Spusť: sudo bash install.sh"; exit 1; }
BASE=$(cd "$(dirname "$0")" && pwd)

echo '[1/10] Instaluji systémové balíčky...'
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-tk chromium xserver-xorg xserver-xorg-core xserver-xorg-legacy xinit openbox unclutter dbus-x11 x11-xserver-utils xdotool wmctrl x11-utils alsa-utils curl ca-certificates fonts-dejavu-core plymouth plymouth-themes initramfs-tools

echo '[2/10] Připravuji uživatele a datové adresáře...'
id caracal >/dev/null 2>&1 || useradd -m -s /bin/bash caracal
usermod -a -G video,audio,input,render caracal
install -d -o caracal -g caracal /var/lib/caracal /var/lib/caracal/media /var/lib/caracal/chromium /home/caracal /home/caracal/.config/openbox

echo '[3/10] Zastavuji předchozí verzi...'
systemctl stop caracal-boot-info.service caracal-overlay.service caracal-player.service caracal-display.service caracal.service 2>/dev/null || true
pkill -u caracal -f '/usr/bin/chromium' 2>/dev/null || true

echo '[4/10] Instaluji aplikaci...'
rm -rf /opt/caracal.new
mkdir -p /opt/caracal.new
cp -a "$BASE"/. /opt/caracal.new/
rm -rf /opt/caracal.new/.venv
python3 -m venv /opt/caracal.new/.venv
/opt/caracal.new/.venv/bin/pip install --upgrade pip wheel
/opt/caracal.new/.venv/bin/pip install -r /opt/caracal.new/requirements.txt
rm -rf /opt/caracal
mv /opt/caracal.new /opt/caracal
# The virtualenv was built in /opt/caracal.new: its entry points (e.g. .venv/bin/uvicorn) still name that
# interpreter in their first line and would fail with "No such file or directory" after the move.
grep -rlI '^#!/opt/caracal.new/' /opt/caracal/.venv/bin 2>/dev/null | xargs -r sed -i '1s|^#!/opt/caracal.new/|#!/opt/caracal/|'
chown -R caracal:caracal /opt/caracal /var/lib/caracal
chmod 755 /opt/caracal/player/launch.sh /opt/caracal/player/fullscreen-watchdog.sh /opt/caracal/player/boot-info.py

echo '[5/10] Konfiguruji Xorg, VC4 a Openbox...'
install -d /etc/X11/xorg.conf.d
install -m 644 /opt/caracal/system-config/Xwrapper.config /etc/X11/Xwrapper.config
install -m 644 /opt/caracal/system-config/99-vc4.conf /etc/X11/xorg.conf.d/99-vc4.conf
install -m 755 -o caracal -g caracal /opt/caracal/system-config/xinitrc /home/caracal/.xinitrc
install -m 644 -o caracal -g caracal /opt/caracal/system-config/openbox-rc.xml /home/caracal/.config/openbox/rc.xml

echo '[6/10] Instaluji systemd služby...'
for svc in caracal.service caracal-display.service caracal-player.service caracal-overlay.service caracal-boot-info.service; do
 install -m 644 "/opt/caracal/systemd/$svc" "/etc/systemd/system/$svc"
done
cat >/etc/sudoers.d/caracal <<'SUDOEOF'
caracal ALL=(root) NOPASSWD: /bin/systemctl restart caracal-player.service, /bin/systemctl restart caracal-player, /bin/systemctl reboot
SUDOEOF
chmod 440 /etc/sudoers.d/caracal

echo '[7/10] Instaluji CARACAL splash screen...'
rm -rf /usr/share/plymouth/themes/caracal
install -d /usr/share/plymouth/themes/caracal
cp -a /opt/caracal/plymouth/. /usr/share/plymouth/themes/caracal/
chmod 644 /usr/share/plymouth/themes/caracal/*
plymouth-set-default-theme caracal
update-initramfs -u -k all

echo '[8/10] Aktivuju služby...'
systemctl daemon-reload
systemctl set-default graphical.target
systemctl enable caracal.service caracal-display.service caracal-player.service caracal-overlay.service caracal-boot-info.service
systemctl reset-failed caracal.service caracal-display.service caracal-player.service caracal-overlay.service caracal-boot-info.service 2>/dev/null || true
systemctl restart caracal.service
systemctl restart caracal-display.service
sleep 8
systemctl restart caracal-overlay.service
systemctl restart caracal-player.service
systemctl restart caracal-boot-info.service

echo '[9/10] Kontroluji instalaci...'
sleep 7
for svc in caracal.service caracal-display.service caracal-player.service caracal-overlay.service; do
 if ! systemctl is-active --quiet "$svc"; then
  echo "CHYBA: $svc neběží"
  journalctl -u "$svc" -n 100 --no-pager
  exit 1
 fi
done
curl -fsS http://127.0.0.1:8080/api/setup-status >/dev/null

echo '[10/10] Hotovo.'
IP=$(hostname -I | awk '{print $1}')
echo "Administrace: http://${IP}:8080"
echo 'Po restartu se zobrazí splash screen a úvodní obrazovka s IP adresou.'

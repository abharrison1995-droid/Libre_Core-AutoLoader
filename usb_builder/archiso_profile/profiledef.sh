#!/usr/bin/env bash
# shellcheck disable=SC2034

iso_name="libre_core_autoloader"
iso_label="AUTOLOADER"
iso_publisher="Libre_Core <https://github.com/Libre-Core>"
iso_application="Libre_Core AutoLoader USB Live Environment"
iso_version="2026.09.29"
install_dir="arch"
buildmodes=('iso')
bootmodes=('bios.syslinux' 'uefi.systemd-boot')
arch="x86_64"
pacman_conf="pacman.conf"
airootfs_image_type="squashfs"
airootfs_image_tool_options=('-comp' 'xz' '-Xbcj' 'x86' '-b' '1M' '-Xdict-size' '1M')
bootstrap_tarball_compression=('zstd' '-c' '-T0' '--auto-threads=logical' '--long' '-19')
file_permissions=(
  ["/etc/shadow"]="0:0:400"
  ["/opt/autoloader/start.sh"]="0:0:755"
  ["/etc/systemd/system/autoloader.service"]="0:0:644"
  ["/root/customize_airootfs.sh"]="0:0:700"
)

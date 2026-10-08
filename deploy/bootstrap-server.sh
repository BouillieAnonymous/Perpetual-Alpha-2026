#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
if [ "$(id -u)" -ne 0 ]; then echo 'Run with sudo.' >&2; exit 1; fi
. /etc/os-release
if [ "$ID" != ubuntu ]; then echo 'Ubuntu required.' >&2; exit 1; fi
apt-get update
apt-get install -y ca-certificates curl git ufw unattended-upgrades
install -m 0755 -d /etc/apt/keyrings
curl --fail --silent --show-error https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
install -d -m 0750 -o ubuntu -g ubuntu /opt/perpetual-alpha
install -d -m 0700 -o ubuntu -g ubuntu /opt/perpetual-alpha/config /opt/perpetual-alpha/data /opt/perpetual-alpha/logs
cat > /etc/apt/apt.conf.d/52perpetual-alpha-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
Unattended-Upgrade::Automatic-Reboot "false";
EOF
ufw allow 22/tcp comment 'SSH administration'
ufw default deny incoming
ufw default allow outgoing
ufw --force enable
docker version
docker compose version
ufw status verbose
test ! -e /var/run/reboot-required || echo 'REBOOT_REQUIRED'

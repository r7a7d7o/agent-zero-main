#!/bin/bash
set -e

if ! command -v apt-get >/dev/null 2>&1; then
  echo "apt-get unavailable; skipping Collabora CODE install"
  exit 0
fi

install -d -m 0755 /etc/apt/keyrings
if [ ! -f /etc/apt/keyrings/collaboraonline-release-keyring.gpg ]; then
  wget -O /etc/apt/keyrings/collaboraonline-release-keyring.gpg \
    https://collaboraoffice.com/downloads/gpg/collaboraonline-release-keyring.gpg
fi

cat >/etc/apt/sources.list.d/collaboraonline.sources <<'EOF'
Types: deb
URIs: https://www.collaboraoffice.com/repos/CollaboraOnline/CODE-deb
Suites: ./
Signed-By: /etc/apt/keyrings/collaboraonline-release-keyring.gpg
EOF

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends coolwsd coolwsd-deprecated code-brand
rm -rf /var/lib/apt/lists/*

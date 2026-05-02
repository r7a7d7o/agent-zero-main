#!/bin/bash
set -e

# install playwright - moved to install A0
# bash /ins/install_playwright.sh "$@"

# searxng - moved to base image
# bash /ins/install_searxng.sh "$@"

if ! command -v apt-get >/dev/null 2>&1; then
  echo "apt-get unavailable; skipping LibreOffice install"
  exit 0
fi

install_xpra_repo() {
  local os_id=""
  local codename=""
  local uri="https://xpra.org"
  local suite="trixie"
  local arch

  arch="$(dpkg --print-architecture 2>/dev/null || echo amd64)"

  if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    os_id="${ID:-}"
    codename="${VERSION_CODENAME:-}"
  fi

  if [ "$os_id" = "kali" ]; then
    uri="https://xpra.org/beta"
    suite="sid"
  elif [ "$codename" = "sid" ] || [ "$codename" = "forky" ]; then
    uri="https://xpra.org/beta"
    suite="$codename"
  elif [ -n "$codename" ]; then
    suite="$codename"
  fi

  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates wget
  configure_xpra_repo "$uri" "$suite" "$arch"
  apt-get update

  if ! DEBIAN_FRONTEND=noninteractive apt-get install -s --no-install-recommends xpra xpra-x11 xpra-html5 >/tmp/xpra-install-check.log 2>&1; then
    if [ "$arch" != "amd64" ]; then
      echo "xpra packages are not installable from ${uri} ${suite} for ${arch}; falling back to https://xpra.org trixie"
      configure_xpra_repo "https://xpra.org" "trixie" "$arch"
      apt-get update
      if ! DEBIAN_FRONTEND=noninteractive apt-get install -s --no-install-recommends xpra xpra-x11 xpra-html5 >/tmp/xpra-install-check.log 2>&1; then
        cat /tmp/xpra-install-check.log
        exit 1
      fi
    else
      cat /tmp/xpra-install-check.log
      exit 1
    fi
  fi
}

configure_xpra_repo() {
  local uri="$1"
  local suite="$2"
  local arch="$3"

  wget -O /usr/share/keyrings/xpra.asc https://xpra.org/xpra.asc
  cat >/etc/apt/sources.list.d/xpra.sources <<EOF
Types: deb
URIs: ${uri}
Suites: ${suite}
Components: main
Signed-By: /usr/share/keyrings/xpra.asc
Architectures: ${arch}
EOF
}

install_xpra_repo
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  libreoffice-core \
  libreoffice-writer \
  libreoffice-calc \
  libreoffice-impress \
  libreoffice-gtk3 \
  python3-uno \
  xpra \
  xpra-x11 \
  xpra-html5 \
  xfce4-session \
  xfwm4 \
  xfce4-panel \
  xfdesktop4 \
  xfce4-settings \
  thunar \
  gvfs \
  libglib2.0-bin \
  xfce4-terminal \
  pulseaudio \
  pulseaudio-utils \
  x11-xserver-utils \
  xdotool \
  xauth \
  dbus-x11 \
  fonts-dejavu \
  fonts-liberation \
  fonts-crosextra-caladea \
  fonts-crosextra-carlito \
  fonts-noto-core \
  fonts-noto-cjk \
  fonts-noto-color-emoji

rm -rf /var/lib/apt/lists/*

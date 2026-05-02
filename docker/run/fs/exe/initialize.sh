#!/bin/bash

echo "Running initialization script..."

configure_nofile_limit() {
    local desired="${A0_NOFILE_LIMIT:-65535}"
    local hard_limit
    local soft_limit
    local target

    if ! [[ "$desired" =~ ^[0-9]+$ ]] || [ "$desired" -le 0 ]; then
        echo "Invalid A0_NOFILE_LIMIT '$desired'; keeping current nofile limit $(ulimit -Sn)."
        return
    fi

    hard_limit="$(ulimit -Hn 2>/dev/null || true)"
    soft_limit="$(ulimit -Sn 2>/dev/null || true)"
    target="$desired"
    if [[ "$hard_limit" =~ ^[0-9]+$ ]] && [ "$hard_limit" -gt 0 ] && [ "$target" -gt "$hard_limit" ]; then
        target="$hard_limit"
    fi

    if [ "$soft_limit" = "unlimited" ]; then
        echo "Keeping nofile soft limit: unlimited (hard: $(ulimit -Hn))"
        return
    fi

    if [[ "$soft_limit" =~ ^[0-9]+$ ]] && [ "$soft_limit" -ge "$target" ]; then
        echo "Keeping nofile soft limit: $soft_limit (hard: $(ulimit -Hn))"
        return
    fi

    if ulimit -Sn "$target" 2>/dev/null; then
        echo "Configured nofile soft limit: $(ulimit -Sn) (hard: $(ulimit -Hn))"
    else
        echo "Could not raise nofile soft limit to $target; current: $(ulimit -Sn) (hard: $(ulimit -Hn))"
    fi
}

configure_nofile_limit

# branch from parameter
if [ -z "$1" ]; then
    echo "Error: Branch parameter is empty. Please provide a valid branch name."
    exit 1
fi
BRANCH="$1"

# Copy all contents from persistent /per to root directory (/) without overwriting
cp -r --no-preserve=ownership,mode /per/* /

# allow execution of /root/.bashrc and /root/.profile
chmod 444 /root/.bashrc
chmod 444 /root/.profile

# update package list to save time later
apt-get update > /dev/null 2>&1 &

# let supervisord handle the services
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf

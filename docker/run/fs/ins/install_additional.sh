#!/bin/bash
set -e

# install playwright - moved to install A0
# bash /ins/install_playwright.sh "$@"

# searxng - moved to base image
# bash /ins/install_searxng.sh "$@"

# Collabora CODE for future images. Existing containers still self-heal through the
# Office plugin runtime bootstrap, so this is an optimization rather than a release
# prerequisite.
bash /ins/install_collabora_code.sh "$@"

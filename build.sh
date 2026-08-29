#!/bin/bash
# Compile cv_master.tex -> cv_master.pdf using Tectonic
# Tectonic is a single-binary LaTeX engine that auto-fetches required packages.
# Install: brew install tectonic   (mac)
#          apt install tectonic    (debian / ubuntu)
#          See https://tectonic-typesetting.github.io/ for other platforms.
#
# To use a different engine, edit the line below.

set -euo pipefail
cd "$(dirname "$0")"

if ! command -v tectonic >/dev/null 2>&1; then
  echo "✗ tectonic not found. Install with: brew install tectonic"
  echo "  Or edit build.sh to use pdflatex / xelatex instead."
  exit 1
fi

tectonic --chatter minimal cv_master.tex
echo "✓ cv_master.pdf built"
ls -lh cv_master.pdf

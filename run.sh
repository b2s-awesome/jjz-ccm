#!/usr/bin/env bash
# Dev/testing on Linux/macOS. Roblox automation itself targets Windows.
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi
exec .venv/bin/python main.py

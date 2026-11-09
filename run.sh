#!/usr/bin/env bash
# Start AI Card Table and open it in your browser.
cd "$(dirname "$0")" && exec python3 server.py "$@"

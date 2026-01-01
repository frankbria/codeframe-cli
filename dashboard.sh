#!/bin/bash
# Quick launcher for Traycer Queue Manager Dashboard
cd "$(dirname "$0")"
source .venv/bin/activate
python -m traycer_queue.dashboard "$@"

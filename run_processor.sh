#!/bin/bash
cd /home/frankbria/projects/agentic-gh-coding
export GITHUB_TOKEN=$(gh auth token)
export GITHUB_USERNAME=frankbria
source .venv/bin/activate
python -m traycer_queue.processor

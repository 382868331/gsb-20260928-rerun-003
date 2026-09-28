#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
mkdir -p demo_voices
exec .venv/bin/python -m studio.demo demo_voices

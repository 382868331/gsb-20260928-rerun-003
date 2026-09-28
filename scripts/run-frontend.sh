#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/../frontend"
exec node node_modules/vite/bin/vite.js --host 127.0.0.1

#!/usr/bin/env bash
# Serve the editor for this codespace. Idempotent: re-attaching will not start
# a second server on the same port.
#
# `serve.py` rather than `python -m http.server`: the server is where your
# files live and the only thing that writes a hand-in, so a read-only static
# server is not enough to work in.
set -euo pipefail
cd "$(dirname "$0")/.."
if ! pgrep -f "serve.py" >/dev/null 2>&1; then
  nohup python .devcontainer/serve.py 8000 >/tmp/dsviz-editor.log 2>&1 &
  sleep 1
fi
echo "Editor served on port 8000. Your files are kept in .dsviz/workspace.json;"
echo "handing in writes solutions/ for you."

#!/bin/bash

# This script will find each main.py in src/mcp_servers/*, start it, show console output, kill after 10s, then move to the next.

set -e

MCP_SERVERS_DIR="src/mcp_servers"

for main_py in "$MCP_SERVERS_DIR"/*/main.py; do
    echo "Starting $main_py ..."
    # Start in background, redirect output to a temp file
    TMP_LOG=$(mktemp)
    python "$main_py" > "$TMP_LOG" 2>&1 &
    PID=$!
    echo "PID: $PID"
    sleep 10
    echo "Killing $PID ..."
    kill $PID 2>/dev/null || true
    # Wait a moment to ensure process is killed
    sleep 1
    echo "Console output from $main_py:"
    cat "$TMP_LOG"
    rm "$TMP_LOG"
    echo "Done with $main_py"
    echo "----------------------------------------"
done

echo "All main.py scripts processed."

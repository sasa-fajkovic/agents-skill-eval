#!/bin/sh
set -eu

redis-server --save "" --appendonly no --bind 127.0.0.1 --port 6379 --daemonize yes

exec /app/bin/app

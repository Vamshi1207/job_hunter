#!/bin/bash
# Keep Xvfb alive after this script execs uvicorn. A plain `Xvfb &` is killed by SIGHUP.
set -e
export DISPLAY="${DISPLAY:-:99}"
export MOZ_DISABLE_CONTENT_SANDBOX="${MOZ_DISABLE_CONTENT_SANDBOX:-1}"

start_xvfb() {
  local num="${DISPLAY#:}"
  if command -v xdpyinfo >/dev/null 2>&1 && xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
    return 0
  fi
  rm -f "/tmp/.X${num}-lock" "/tmp/.X11-unix/X${num}"
  mkdir -p /tmp/.X11-unix
  chmod 1777 /tmp/.X11-unix
  setsid Xvfb "$DISPLAY" -screen 0 1920x1080x24 -ac +extension GLX +render -noreset \
    >/tmp/xvfb.log 2>&1 &
  local i=0
  while [ "$i" -lt 50 ]; do
    if command -v xdpyinfo >/dev/null 2>&1; then
      if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
        return 0
      fi
    elif [ -S "/tmp/.X11-unix/X${num}" ]; then
      return 0
    fi
    i=$((i + 1))
    sleep 0.1
  done
  echo "WARNING: Xvfb did not become ready on $DISPLAY" >&2
  cat /tmp/xvfb.log >&2 || true
}

if command -v Xvfb >/dev/null 2>&1; then
  start_xvfb
fi
exec "$@"

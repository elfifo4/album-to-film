#!/bin/bash
# Start, stop or check the review site.
#   ./site.sh start    start it in the background and open it in the browser (the default)
#   ./site.sh stop     stop it
#   ./site.sh status   say whether it is running
# The site keeps running after the terminal window is closed. Its output goes to work/review.log.

cd "$(dirname "$0")" || exit 1
PORT=8765
URL="http://127.0.0.1:$PORT"
PID_FILE="work/review.pid"
LOG="work/review.log"

answers() { curl -s -o /dev/null --max-time 2 "$URL/"; }

# The process listening on the port, but only if it is this project's review server.
server_pid() {
  local pid
  pid=$(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null | head -1)
  [ -n "$pid" ] && ps -o command= -p "$pid" | grep -q "album_to_film review" && echo "$pid"
}

case "${1:-start}" in
  start)
    if answers; then
      echo "The site is already running: $URL"
    else
      if [ ! -x .venv/bin/python ]; then
        echo "The Python environment (.venv) is missing. See REBUILD.md to set it up again."
        exit 1
      fi
      mkdir -p work
      nohup .venv/bin/python -m album_to_film review > "$LOG" 2>&1 &
      echo $! > "$PID_FILE"
      for _ in $(seq 1 30); do answers && break; sleep 0.5; done
      if answers; then
        echo "The site is running: $URL"
      else
        echo "The site did not start. Last lines of $LOG:"
        tail -5 "$LOG"
        exit 1
      fi
    fi
    command -v open > /dev/null && open "$URL"
    ;;
  stop)
    pid=$(server_pid)
    if [ -n "$pid" ]; then
      kill "$pid"
      rm -f "$PID_FILE"
      echo "The site was stopped."
    else
      rm -f "$PID_FILE"
      echo "The site is not running."
    fi
    ;;
  status)
    if answers; then echo "Running: $URL"; else echo "Not running."; fi
    ;;
  *)
    echo "Usage: ./site.sh start | stop | status"
    exit 1
    ;;
esac

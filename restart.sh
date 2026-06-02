#!/bin/bash
PID=$(pgrep -f "python run.py")
if [ -n "$PID" ]; then
    echo "Killing process $PID"
    kill $PID
    sleep 2
fi
cd /home/robertpiyyra/id_project
nohup /home/robertpiyyra/id_project/venv/bin/python run.py > app.log 2>&1 < /dev/null &
echo "Server restarted"

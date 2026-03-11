ps aux | grep src/main.py | grep -v grep | awk '{print $2}' | xargs kill -9
source venv/bin/activate && PYTHONPATH=. python src/main.py
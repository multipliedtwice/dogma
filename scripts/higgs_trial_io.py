"""JSON reads that tolerate the short Windows atomic-replacement window."""
import json
import time

def read_json(path):
    for attempt in range(20):
        try:return json.loads(path.read_text('utf-8'))
        except (PermissionError,FileNotFoundError,json.JSONDecodeError):
            if attempt==19:raise
            time.sleep(min(.005*(attempt+1),.05))

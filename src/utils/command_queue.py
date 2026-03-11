import os
import json
from datetime import datetime
from src.utils.logger import logger


class CommandQueue:
    """
    텔레그램 리스너 → main.py 프로세스 간 명령 전달을 위한 파일 기반 큐.
    
    텔레그램에서 명령을 받으면 JSON 파일에 기록하고,
    main.py 스케줄러가 주기적으로 읽어서 실행합니다.
    """
    QUEUE_FILE = "manager/command_queue.json"

    @classmethod
    def push(cls, command: str, params: dict = None):
        """명령을 큐에 추가합니다."""
        os.makedirs(os.path.dirname(cls.QUEUE_FILE), exist_ok=True)
        
        queue = cls._load()
        queue.append({
            "command": command,
            "params": params or {},
            "created_at": datetime.now().isoformat(),
            "status": "pending"
        })
        
        with open(cls.QUEUE_FILE, 'w', encoding='utf-8') as f:
            json.dump(queue, f, indent=2, ensure_ascii=False)
        
        logger.info(f"[CommandQueue] 명령 추가: {command} (큐 크기: {len(queue)})")

    @classmethod
    def pop_all(cls) -> list:
        """대기 중인 모든 명령을 꺼내고 큐를 비웁니다."""
        queue = cls._load()
        if not queue:
            return []
        
        pending = [cmd for cmd in queue if cmd.get("status") == "pending"]
        
        # 큐 비우기
        with open(cls.QUEUE_FILE, 'w', encoding='utf-8') as f:
            json.dump([], f)
        
        return pending

    @classmethod
    def _load(cls) -> list:
        if not os.path.exists(cls.QUEUE_FILE):
            return []
        try:
            with open(cls.QUEUE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception):
            return []

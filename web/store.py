"""
内存数据存储 + JSON 文件持久化。
"""

import json
import threading
import time
from pathlib import Path


class DataStore:
    """保存当前 Token 快照、巡检历史和运行状态。"""

    MAX_HISTORY = 100

    def __init__(self, data_dir: str | Path | None = None):
        if data_dir is None:
            data_dir = Path(__file__).resolve().parent / "data"
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.history_file = self.data_dir / "history.json"

        self._lock = threading.Lock()
        self.tokens: list[dict] = []
        self.stats: dict = {
            "total": 0,
            "alive": 0,
            "dead": 0,
            "disabled": 0,
            "quota_full": 0,
            "has_refresh": 0,
        }
        self.history: list[dict] = []
        self.is_inspecting: bool = False
        self.is_refreshing: bool = False
        self.last_refresh_at: str | None = None
        self.last_inspect_at: str | None = None
        self.daemon_interval_seconds: int | None = None
        self.next_daemon_inspect_at: str | None = None
        self.inspect_output: str = ""

        self._load_history()

    # ------------------------------------------------------------------
    # Token 数据
    # ------------------------------------------------------------------

    def update_tokens(self, tokens: list[dict], stats: dict):
        with self._lock:
            self.tokens = tokens
            self.stats = stats
            self.last_refresh_at = time.strftime("%Y-%m-%d %H:%M:%S")

    def get_tokens(self) -> list[dict]:
        with self._lock:
            return list(self.tokens)

    def get_stats(self) -> dict:
        with self._lock:
            return dict(self.stats)

    # ------------------------------------------------------------------
    # 巡检状态
    # ------------------------------------------------------------------

    def set_inspecting(self, value: bool):
        with self._lock:
            self.is_inspecting = value

    def set_refreshing(self, value: bool):
        with self._lock:
            self.is_refreshing = value

    def set_daemon_state(self, interval_seconds: int | None, next_inspect_at: str | None):
        with self._lock:
            self.daemon_interval_seconds = interval_seconds
            self.next_daemon_inspect_at = next_inspect_at

    def set_inspect_done(self, output: str = ""):
        with self._lock:
            self.is_inspecting = False
            self.last_inspect_at = time.strftime("%Y-%m-%d %H:%M:%S")
            self.inspect_output = output

    def get_status(self) -> dict:
        with self._lock:
            return {
                "is_inspecting": self.is_inspecting,
                "is_refreshing": self.is_refreshing,
                "last_refresh_at": self.last_refresh_at,
                "last_inspect_at": self.last_inspect_at,
                "daemon_interval_seconds": self.daemon_interval_seconds,
                "next_daemon_inspect_at": self.next_daemon_inspect_at,
                "stats": dict(self.stats),
            }

    # ------------------------------------------------------------------
    # 巡检历史
    # ------------------------------------------------------------------

    def add_history(self, record: dict):
        with self._lock:
            self.history.append(record)
            if len(self.history) > self.MAX_HISTORY:
                self.history = self.history[-self.MAX_HISTORY:]
        self._save_history()

    def get_history(self) -> list[dict]:
        with self._lock:
            return list(self.history)

    def _save_history(self):
        try:
            with self._lock:
                data = list(self.history)
            self.history_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _load_history(self):
        if not self.history_file.exists():
            return
        try:
            data = json.loads(self.history_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self.history = data[-self.MAX_HISTORY:]
        except Exception:
            pass

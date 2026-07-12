import logging
import sys
import os
import glob
import time
from datetime import datetime, timedelta


class DailyFileHandler(logging.FileHandler):
    """
    每天直接写入带日期的日志文件 (e.g. server_2026-02-19.log)，
    无需 rename，彻底避免 Windows 多进程文件锁冲突。
    """

    def __init__(self, log_dir, log_name, retention_days=90, encoding='utf-8'):
        self.log_dir = log_dir
        self.log_name = log_name
        self.retention_days = retention_days
        self._current_date = self._today()
        filepath = self._make_path(self._current_date)
        super().__init__(filepath, mode='a', encoding=encoding)

    @staticmethod
    def _today():
        return datetime.now().strftime("%Y-%m-%d")

    def _make_path(self, date_str):
        return os.path.join(self.log_dir, f"{self.log_name}_{date_str}.log")

    def emit(self, record):
        today = self._today()
        if today != self._current_date:
            # 日期变了，切换到新文件
            self._current_date = today
            self.close()
            self.baseFilename = os.path.abspath(self._make_path(today))
            self.stream = self._open()
            self._cleanup_old_logs()
        super().emit(record)

    def _cleanup_old_logs(self):
        """删除超过 retention_days 的旧日志文件。"""
        try:
            cutoff = datetime.now() - timedelta(days=self.retention_days)
            pattern = os.path.join(self.log_dir, f"{self.log_name}_*.log")
            for path in glob.glob(pattern):
                basename = os.path.basename(path)
                # 提取日期部分: log_name_YYYY-MM-DD.log
                date_part = basename[len(self.log_name) + 1:-4]
                try:
                    file_date = datetime.strptime(date_part, "%Y-%m-%d")
                    if file_date < cutoff:
                        os.remove(path)
                except (ValueError, OSError):
                    pass
        except Exception:
            pass


def setup_logging(log_dir="logs", log_name="inbrief", retention_days=90):
    """
    Configure logging with date-based file (no rename, Windows-safe).

    Args:
        log_dir: Directory for log files
        log_name: Log file basename (e.g. 'scheduler' -> logs/scheduler_2026-02-19.log)
        retention_days: Number of days to keep old logs
    """
    os.makedirs(log_dir, exist_ok=True)

    file_handler = DailyFileHandler(log_dir, log_name, retention_days)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            file_handler,
            logging.StreamHandler(sys.stdout)
        ]
    )

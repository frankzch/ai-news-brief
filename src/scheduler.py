import time
import random
import logging
from datetime import datetime, timedelta, date

import schedule

from config_loader import ConfigLoader


class Scheduler:
    """
    定时任务调度器。

    interval 模式：两轮任务之间的间隔在 [0.5N, 2N] 之间随机；
    每天会在配置的时间范围内随机选一个睡眠窗口，
    若某次预定执行时间落入该窗口，则跳过本次，等待下一次。

    time_str 模式：保持原有的每日定点执行行为。

    睡眠窗口参数通过 config.yaml 的 schedule.sleep_window 配置。
    """

    def __init__(self, time_str=None, interval_hours=None, job_func=None):
        self.time_str = time_str
        self.interval_hours = interval_hours
        self.job_func = job_func
        self.running = False

        # 从配置文件读取睡眠窗口参数，提供默认值兜底
        sleep_cfg = ConfigLoader.get_instance().get('schedule', {}).get('sleep_window', {})
        self._sleep_start_hour = sleep_cfg.get('start_hour', 12)
        self._sleep_end_hour = sleep_cfg.get('end_hour', 20)
        self._sleep_duration_min = sleep_cfg.get('duration_min_hours', 5)
        self._sleep_duration_max = sleep_cfg.get('duration_max_hours', 7)

        self._sleep_window_date = None   # date 对象，用于判断是否需要重新生成
        self._sleep_start = None         # datetime
        self._sleep_end = None           # datetime

    # -------- 睡眠窗口 --------
    def _ensure_sleep_window(self, now: datetime):
        """确保 self._sleep_start/_sleep_end 对应 now 所在的本地日期。"""
        today = now.date()
        if self._sleep_window_date == today:
            return

        duration_hours = random.uniform(
            self._sleep_duration_min, self._sleep_duration_max
        )
        latest_start_hour = self._sleep_end_hour - duration_hours
        start_hour = random.uniform(self._sleep_start_hour, latest_start_hour)

        start_dt = datetime.combine(today, datetime.min.time()) + timedelta(hours=start_hour)
        end_dt = start_dt + timedelta(hours=duration_hours)

        self._sleep_window_date = today
        self._sleep_start = start_dt
        self._sleep_end = end_dt

        logging.info(
            f"Today's sleep window: {start_dt.strftime('%H:%M')} - "
            f"{end_dt.strftime('%H:%M')} (duration {duration_hours:.2f}h)"
        )

    def _in_sleep_window(self, dt: datetime) -> bool:
        self._ensure_sleep_window(dt)
        return self._sleep_start <= dt < self._sleep_end

    # -------- 主循环 --------
    def start(self):
        if self.interval_hours:
            logging.info(
                f"Scheduler started. Base interval = {self.interval_hours}h, "
                f"randomized in [{0.5 * self.interval_hours:.2f}h, "
                f"{1.5 * self.interval_hours:.2f}h]."
            )
            self._run_interval_loop()
        elif self.time_str:
            logging.info(f"Scheduler started. Job scheduled for {self.time_str} daily.")
            schedule.every().day.at(self.time_str).do(self._run_job_if_awake)
            self.running = True
            while self.running:
                schedule.run_pending()
                time.sleep(60)
        else:
            logging.error("No schedule configuration found (time or interval_hours).")
            return

    def _run_interval_loop(self):
        self.running = True

        # 启动时确定今天的睡眠窗口；首轮立即执行（若不在睡眠中）
        now = datetime.now()
        self._ensure_sleep_window(now)
        if self._in_sleep_window(now):
            logging.info("Startup time falls in sleep window, skipping initial run.")
        else:
            self._safe_run()

        while self.running:
            base = self.interval_hours
            delay_hours = random.uniform(0.5 * base, 1.5 * base)
            delay_seconds = delay_hours * 3600

            next_run = datetime.now() + timedelta(seconds=delay_seconds)
            logging.info(
                f"Next run at {next_run.strftime('%Y-%m-%d %H:%M:%S')} "
                f"(in {delay_hours:.2f}h)."
            )

            time.sleep(delay_seconds)

            now = datetime.now()
            if self._in_sleep_window(now):
                logging.info(
                    f"Run time {now.strftime('%H:%M:%S')} is in sleep window "
                    f"[{self._sleep_start.strftime('%H:%M')}-"
                    f"{self._sleep_end.strftime('%H:%M')}], skipping."
                )
                continue

            self._safe_run()

    def _run_job_if_awake(self):
        now = datetime.now()
        if self._in_sleep_window(now):
            logging.info("Scheduled time falls in sleep window, skipping.")
            return
        self._safe_run()

    def _safe_run(self):
        try:
            self.job_func()
        except Exception as e:
            logging.exception(f"Scheduled job raised an exception: {e}")

    def run_now(self):
        logging.info("Forcing job run now...")
        self._safe_run()

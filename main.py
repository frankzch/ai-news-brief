import logging
import sys
import os
import time

sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))
from log_config import setup_logging
setup_logging(log_name='scheduler')

from pipeline_runner import run_pipeline, cleanup_old_articles
from config_loader import ConfigLoader
def run_all_jobs():
    """Wrapper to run all scheduled jobs."""
    
    run_pipeline()
    cleanup_old_articles()


def run_all_jobs_in_subprocess():
    """在子进程中跑一次 run_all_jobs，跑完进程退出，整块 2G+ 内存随之释放。
    父进程（Scheduler）继续驻留循环，内存占用始终保持很小。

    关键防护：
    - 子进程的 stdin/stdout/stderr 全部脱钩到 DEVNULL，避免与父进程共用控制台
      时互相干扰（Windows QuickEdit、控制台句柄异常等都不会再影响父进程）。
    - Windows 上加 CREATE_NEW_PROCESS_GROUP，让子进程拥有独立的进程组，
      父控制台收到的 Ctrl+C / CTRL_CLOSE_EVENT 不会直接传染给子进程。
    - 子进程日志已经走 FileHandler 写到 logs/scheduler_*.log，丢掉 stdout 不影响排查。
    """
    import subprocess
    import sys
    import threading
    logging.info("Spawning child process to execute jobs and release memory...")
    popen_kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "bufsize": 1,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        # 强制子进程 Python 用 UTF-8 且 stdout 行缓冲，确保日志能实时透传到父控制台。
        child_env = os.environ.copy()
        child_env.setdefault("PYTHONIOENCODING", "utf-8")
        child_env["PYTHONUNBUFFERED"] = "1"
        popen_kwargs["env"] = child_env

    proc = subprocess.Popen([sys.executable, "-u", __file__, "--child-job"], **popen_kwargs)

    def _pump(stream):
        try:
            for line in iter(stream.readline, ""):
                if not line:
                    break
                sys.stdout.write(line)
                sys.stdout.flush()
        finally:
            try:
                stream.close()
            except Exception:
                pass

    t = threading.Thread(target=_pump, args=(proc.stdout,), daemon=True)
    t.start()
    rc = proc.wait()
    t.join(timeout=5)
    logging.info(f"Child process finished (exit={rc}). Memory released.")

if __name__ == "__main__":
    # ─────────────────────────────────────────────────────────────
    # 使用方式：直接命令行运行 `python main.py` 即可。
    # 父进程会常驻并按 config.yaml 的 schedule.interval_hours 循环触发任务。
    # 关闭就 Ctrl+C。
    #
    # `--child-job` 是父进程内部 spawn 子进程时用的开关，手动不要加。
    # ─────────────────────────────────────────────────────────────
    if len(sys.argv) > 1 and sys.argv[1] == "--child-job":
        # 子进程分支：单次执行后强制退出，防止 Playwright / 线程池等后台残留导致进程假死。
        run_all_jobs()
        os._exit(0)
    else:
        # 父进程分支：常驻循环调度器，每轮 spawn 子进程跑一次任务。
        from scheduler import Scheduler
        config = ConfigLoader.get_instance().get('schedule')
        run_interval = config.get('interval_hours')

        logging.info(
            f"InBrief scheduler starting. Interval = {run_interval}h "
            f"(randomized). Ctrl+C to stop."
        )
        scheduler = Scheduler(interval_hours=run_interval, job_func=run_all_jobs_in_subprocess)
        # 顶层兜底：除了用户 Ctrl+C / SystemExit，其它所有异常都记录下来并保持进程存活前的诊断。
        # 这样即使父进程被某个偶发 OSError / 控制台异常击中，日志里也能看到原因，
        # 而不是悄无声息地消失。
        try:
            scheduler.start()
        except KeyboardInterrupt:
            logging.info("Scheduler stopped by user (Ctrl+C).")
        except SystemExit:
            raise
        except BaseException:
            logging.exception("Scheduler crashed with an unexpected exception.")
            raise

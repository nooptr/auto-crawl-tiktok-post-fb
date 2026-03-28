import signal
import time

from app.core.config import settings
from app.services.observability import configure_logging, record_event, update_worker_heartbeat
from app.worker.cron import WORKER_NAME, scheduler, start_scheduler


def main():
    configure_logging()
    running = True

    def stop_worker(*_args):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop_worker)
    signal.signal(signal.SIGTERM, stop_worker)

    record_event(
        "worker",
        "info",
        "Tiến trình nền đã khởi động.",
        details={"worker_name": WORKER_NAME, "app_role": settings.APP_ROLE},
    )
    update_worker_heartbeat(WORKER_NAME, app_role=settings.APP_ROLE, status="khởi động")
    start_scheduler()

    try:
        while running:
            time.sleep(1)
    finally:
        update_worker_heartbeat(WORKER_NAME, app_role=settings.APP_ROLE, status="đã dừng")
        if scheduler.running:
            scheduler.shutdown(wait=False)
        record_event(
            "worker",
            "warning",
            "Tiến trình nền đã dừng.",
            details={"worker_name": WORKER_NAME},
        )


if __name__ == "__main__":
    main()

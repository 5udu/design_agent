"""Exit 0 if a local worker owns the OS lock, 1 if no worker is active."""
from app.config import settings
from app.runtime.worker import WorkerLock

settings.data_dir.mkdir(parents=True, exist_ok=True)
try:
    with WorkerLock(settings.data_dir / 'worker.lock'):
        print('inactive')
        raise SystemExit(1)
except RuntimeError:
    print('active')

"""Short OS locks coordinate project removal with worker and preview writes."""
import hashlib
import os
import time


class LockBusy(RuntimeError):
    pass


class FileLock:
    def __init__(self, path, wait=False):
        self.path, self.wait = path, wait

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('a+b')
        deadline = time.monotonic()+(15 if self.wait else 0)
        while True:
            try:
                self._acquire()
                break
            except OSError:
                if time.monotonic() >= deadline:
                    self.file.close()
                    raise LockBusy('项目或图片正在更新，请稍后重试') from None
                time.sleep(0.02)
        return self

    def _acquire(self):
        if os.name == 'nt':
            import msvcrt
            if self.path.stat().st_size == 0:
                self.file.write(b'0')
                self.file.flush()
            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def __exit__(self, *args):
        self.file.close()


def project_lock(directory, project_id):
    name = hashlib.sha256(project_id.encode()).hexdigest()
    return FileLock(directory/'locks'/f'project-{name}.lock')


def preview_lock(directory, wait=False):
    return FileLock(directory/'locks'/'previews.lock', wait=wait)

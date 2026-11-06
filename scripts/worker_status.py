"""Exit 0 if a local worker owns the OS lock, 1 if no worker is active."""
from app.config import settings
import time
from app.persistence.repositories import Store

store = Store(settings.data_dir, settings.database_url, settings.db_pool_size)
with store.connect() as db:
    active = db.execute('SELECT 1 FROM workers WHERE updated_at>? LIMIT 1', (time.time()-30,)).fetchone()
store.close()
print('active' if active else 'inactive')
raise SystemExit(0 if active else 1)

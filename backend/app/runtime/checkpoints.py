"""Run checkpoint IO on threads; commit it while holding the task's lease row."""
import asyncio
from contextlib import asynccontextmanager
from langgraph.checkpoint.base import BaseCheckpointSaver


class CheckpointSaver(BaseCheckpointSaver):
    def __init__(self, store, saver):
        super().__init__(serde=saver.serde)
        self.store, self.saver = store, saver

    @property
    def config_specs(self):
        return self.saver.config_specs

    def get_next_version(self, current, channel):
        return self.saver.get_next_version(current, channel)

    def _call(self, method, *args, **kwargs):
        # The synchronous checkpoint operation completes on this same thread
        # before releasing the lease row. No event-loop dependency holds a lock.
        with self.store.connect():
            return getattr(self.saver, method)(*args, **kwargs)

    async def aget_tuple(self, config):
        return await self._async_call('get_tuple', config)

    async def _async_call(self, method, *args, **kwargs):
        task = asyncio.create_task(asyncio.to_thread(self._call, method, *args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task  # Finish the DB commit before releasing execution ownership.
            raise

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await self._async_call('put', config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=''):
        return await self._async_call('put_writes', config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id):
        return await self._async_call('delete_thread', thread_id)

    async def alist(self, config, *, filter=None, before=None, limit=None):
        rows = await asyncio.to_thread(lambda: list(self.saver.list(config, filter=filter, before=before, limit=limit)))
        for row in rows:
            yield row


@asynccontextmanager
async def checkpoint_saver(store):
    if store.postgres:
        from langgraph.checkpoint.postgres import PostgresSaver
        manager = PostgresSaver.from_conn_string(store.database_url)
    else:
        from langgraph.checkpoint.sqlite import SqliteSaver
        manager = SqliteSaver.from_conn_string(str(store.directory/'checkpoints.db'))
    with manager as saver:
        if store.postgres:
            def setup():
                with store.pool.connection() as connection:
                    connection.execute('SELECT pg_advisory_lock(91020603)')
                    connection.commit()
                    try:
                        saver.setup()
                    finally:
                        connection.execute('SELECT pg_advisory_unlock(91020603)')
                        connection.commit()
            await asyncio.to_thread(setup)
        yield CheckpointSaver(store, saver)

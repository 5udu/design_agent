"""Construct every model request from the session's active message projection."""
from dataclasses import dataclass
from app.agent.instructions import SYSTEM
from app.agent.observations import image_message
from app.context.images import hydrate_images
from app.context.compaction import Compactor
from app.context.meter import estimate_tokens
from app.domain.projects import project_facts
from app.persistence.locking import preview_lock
from app.session.projection import derive_nodes, balanced_units


@dataclass
class PreparedContext:
    messages: list
    estimated_tokens: int
    request_seq: int


class ContextBuilder:
    def __init__(self, store, model, assets, config, tools):
        self.store, self.assets, self.config, self.tools = store, assets, config, tools
        self.compactor = Compactor(store, model, config)

    def ensure_run(self, run):
        events = self.store.events(run['id'])
        finished = {e['data']['operation_id'] for e in events if e['kind'] == 'context_compaction_finished'}
        committed = {e['data']['operation_id'] for e in events if e['kind'] == 'context_compacted'}
        for event in events:
            if event['kind'] == 'context_compaction_started' and event['data']['operation_id'] not in finished:
                operation = event['data']['operation_id']
                self.store.append(run['id'], 'context_compaction_finished', {'operation_id': operation,
                    'status': 'succeeded' if operation in committed else 'failed', 'message': '已恢复中断的上下文整理记录'},
                    key='compact:'+operation+':recovered-end')
        legacy = self.store.event_by_key(run['session_id'], run['id']+':legacy')
        if legacy:
            self.store.append(run['id'], 'system_message', {'version': 'image-agent-v1'}, key=run['id']+':system',
                              model_data={'role': 'system', 'content': SYSTEM})
            return
        context = {'project': project_facts(self.store.get('projects', run['project_id'])),
                   'target_asset_id': run.get('target_asset_id')}
        self.store.seed_run(run, context, SYSTEM)
        if run.get('target_asset_id'):
            self.observe(run, run['target_asset_id'], key=run['id']+':reference')

    def observe(self, run, asset_id, *, key, step_id=None):
        with preview_lock(self.store.directory, wait=True):
            self._observe(run, asset_id, key=key, step_id=step_id)

    def _observe(self, run, asset_id, *, key, step_id=None):
        if self.store.event_by_key(run['session_id'], key):
            return
        try:
            asset, _ = self.assets.get(run['project_id'], asset_id)
            message, digest = image_message(asset, self.assets)
            self.store.append(run['id'], 'visual_observation', {
                'asset_id': asset_id, 'version_id': asset.get('version_id'), 'preview_sha256': digest},
                key=key, step_id=step_id, source='tool', model_data=message)
        except Exception as exc:
            self.store.append(run['id'], 'observation_failed', {'asset_id': asset_id, 'message': self.config.safe_error(exc)},
                key=key, step_id=step_id, source='tool', model_data={'role': 'user',
                'content': f'工具观察失败：素材 {asset_id} 图片无法加载，本次未看到图片，不可声称已检查。'})

    def nodes(self, session_id, through=None):
        return derive_nodes(self.store.session_events(session_id, through=through, private=True))

    async def prepare(self, run, step_id, *, force_compact=False, attempt=0):
        nodes = self.nodes(run['session_id'])
        _, pending = balanced_units(nodes)
        if pending:
            raise ValueError('存在未配对的工具调用，必须先恢复工具结果')
        budget = self.config.context_window_tokens-self.config.context_output_reserve-self.config.context_safety_tokens
        if budget <= 0:
            raise ValueError('上下文窗口不足以容纳输出预留和安全余量')
        threshold = min(budget, int(self.config.context_window_tokens*self.config.context_compact_ratio))
        tokens = estimate_tokens([m for node in nodes for m in node.messages], self.tools, self.store.directory)
        for _ in range(2):
            if tokens < threshold and not force_compact:
                break
            try:
                changed = await self.compactor.compact(run, nodes, step_id)
            except Exception as exc:
                self.store.append(run['id'], 'context_warning', {'message': self.config.safe_error(exc)}, step_id=step_id)
                changed = False
            if not changed:
                if force_compact:
                    raise ValueError('上下文溢出后无法安全压缩，已保留全部记录')
                break
            force_compact = False
            nodes = self.nodes(run['session_id'])
            tokens = estimate_tokens([m for node in nodes for m in node.messages], self.tools, self.store.directory)
        if tokens >= budget:
            raise ValueError('上下文仍超过请求预算，已保留历史和产物')
        through_seq = self.store.cursor(run['session_id'])
        request = self.store.append(run['id'], 'request_prepared', {
            'through_seq': through_seq, 'message_seqs': [node.seq for node in nodes],
            'estimated_input_tokens': tokens, 'context_window_tokens': self.config.context_window_tokens,
            'input_budget_tokens': budget, 'tools': self.tools, 'model': self.config.deepseek_model,
            'reasoning_effort': run.get('reasoning_effort', self.config.deepseek_reasoning_effort),
            'attempt': attempt}, key=step_id+f':request:{attempt}:{through_seq}', step_id=step_id)
        messages = [hydrate_images(message, self.store.directory) for node in nodes for message in node.messages]
        return PreparedContext(messages=messages, estimated_tokens=tokens, request_seq=request['seq'])

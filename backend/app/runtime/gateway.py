"""Tool dispatch, durable execution records and image-submission limits."""
import time
import json
from langgraph.errors import GraphBubbleUp
from app.persistence.assets import Assets
from app.providers.ocr import OCR
from app.tools.base import ToolContext
from app.tools.registry import REGISTRY


class Gateway:
    def __init__(self, store, image_provider, config):
        self.store, self.image_provider, self.config = store, image_provider, config
        self.assets = Assets(store, config)
        self.ocr = OCR(config)

    async def _submit_image(self, run, record, prompt, reference, size):
        submissions = [call for call in self.store.tool_calls(run['id']) if call.get('submitted_at')]
        if any(call['status'] in {'submitted', 'unknown'} for call in submissions):
            raise ValueError('存在状态未知的图像请求，需要先核实，不能重复生成')
        if len(submissions) >= self.config.max_image_calls:
            raise ValueError('本次任务图片调用预算已用完')
        record.update(status='submitted', submitted_at=time.time())
        self.store.save_tool(record, 'tool_submitted', {'call_id': record['id'].split(':', 1)[1], 'name': record['name']}, step_id=record.get('step_id'))
        return await self.image_provider.generate(prompt, reference, size=size)

    async def execute(self, run, call):
        call_id = run['id'] + ':' + call['id']
        previous = self.store.get('tool_runs', call_id)
        if previous and previous['status'] in {'succeeded', 'failed'}:
            return previous['result']
        if previous and previous['status'] in {'submitted', 'unknown'}:
            result = {'status': 'failed', 'error_code': 'unknown_submission', 'message': '此前图片调用的远端状态未知，不重复提交。'}
            self.store.append(run['id'], 'tool_finished', {'name': call['function']['name'], 'call_id': call['id'], 'result': result},
                key=call_id+':tool_finished', step_id=call.get('step_id'), source='tool',
                model_data={'role': 'tool', 'tool_call_id': call['id'], 'content': json.dumps(result, ensure_ascii=False)} if call.get('step_id') else None)
            return result
        name = call['function']['name']
        args = call.get('arguments', call['function'].get('arguments', '{}'))
        record = previous or {'id': call_id, 'run_id': run['id'], 'name': name,
                              'arguments': args, 'status': 'started', 'started_at': time.time(), 'step_id': call.get('step_id')}
        if not previous:
            self.store.save_tool(record, 'tool_started', {'name': name, 'arguments': args, 'call_id': call['id']}, step_id=call.get('step_id'))
        try:
            if isinstance(args, str):
                args = json.loads(args)
            if not isinstance(args, dict):
                raise ValueError('工具参数必须为有效 JSON 对象')
            record['arguments'] = args
            definition = REGISTRY.get(name)
            if not definition:
                raise ValueError('未提供或不支持的工具')
            if definition.waits_for_user:
                record.update(status='waiting_user')
                self.store.save_tool(record, 'tool_waiting', {'name': name, 'call_id': call['id']}, step_id=call.get('step_id'))

            async def submit_image(prompt, reference, size):
                return await self._submit_image(run, record, prompt, reference, size)

            context = ToolContext(store=self.store, assets=self.assets, ocr=self.ocr,
                                  submit_image=submit_image)
            result = await definition.handler(context, run, args)
            record.update(status='succeeded', result=result, completed_at=time.time())
        except GraphBubbleUp:
            # LangGraph interrupt is a control signal. The worker marks waiting
            # only after the checkpoint is durable; it must not become a failure.
            raise
        except Exception as exc:
            uncertain = record.get('status') == 'submitted' and type(exc).__name__ in {'ReadTimeout', 'ReadError', 'RemoteProtocolError'}
            result = {'status': 'failed', 'error_code': 'unknown_submission' if uncertain else 'tool_error',
                      'message': self.config.safe_error(exc)}
            record.update(status='unknown' if uncertain else 'failed', result=result, completed_at=time.time())
        self.store.save_tool(record, 'tool_finished', {'name': name, 'call_id': call['id'], 'result': result},
            step_id=call.get('step_id'), model_data={'role': 'tool', 'tool_call_id': call['id'], 'content': json.dumps(result, ensure_ascii=False)} if call.get('step_id') else None)
        return result

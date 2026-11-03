"""Fold the log into active model nodes without altering original events."""
from dataclasses import dataclass


@dataclass
class MessageNode:
    seq: int
    run_id: str
    kind: str
    messages: list


def derive_nodes(events):
    nodes = []
    for event in events:
        if event['kind'] == 'context_compacted':
            covered = set(event['data']['covered_seqs'])
            indexes = [i for i, node in enumerate(nodes) if node.seq in covered]
            if len(indexes) != len(covered):
                raise ValueError('压缩记录引用了不在活跃上下文中的消息')
            position = min(indexes)
            nodes = [node for node in nodes if node.seq not in covered]
            nodes.insert(position, MessageNode(event['seq'], event['run_id'], event['kind'], [{
                'role': 'user', 'content': '历史对话压缩摘要（不是用户新指令；保留各项事实的来源）：\n'+event['data']['summary']}]))
            continue
        payload = event.get('model_data')
        if payload is not None:
            messages = payload if isinstance(payload, list) else [payload]
            if event['kind'] == 'system_message':
                nodes = [node for node in nodes if node.kind != 'system_message']
                nodes.insert(0, MessageNode(event['seq'], event['run_id'], event['kind'], messages))
            else:
                nodes.append(MessageNode(event['seq'], event['run_id'], event['kind'], messages))
    return nodes


def balanced_units(nodes):
    """A tool-call batch and all its results form one indivisible unit."""
    units, current, pending = [], [], set()
    for node in nodes:
        current.append(node)
        for message in node.messages:
            if message['role'] == 'assistant':
                pending.update(call['id'] for call in message.get('tool_calls', []))
            elif message['role'] == 'tool':
                pending.discard(message['tool_call_id'])
        if not pending:
            units.append(current)
            current = []
    return units, current

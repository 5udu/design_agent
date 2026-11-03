"""Ask for missing user input and resume at the same checkpointed tool call."""
from langgraph.types import interrupt
from app.tools.base import ToolContext, ToolDefinition


async def ask_user(context: ToolContext, run: dict, args: dict) -> dict:
    question, options = args.get('question'), args.get('options', [])
    if not isinstance(question, str) or not question.strip() or not isinstance(options, list) or not all(isinstance(option, str) for option in options):
        raise ValueError('问题必须非空，options 必须是字符串数组')
    answer = interrupt({'question': question, 'options': options})
    return {'status': 'succeeded', 'answer': answer}


ASK_USER = ToolDefinition(
    name='ask_user', description='关键目标缺失或需要用户选择时提问并等待；信息完整就直接工作',
    properties={'question': {'type': 'string'}, 'options': {'type': 'array', 'items': {'type': 'string'}}},
    required=['question', 'options'], handler=ask_user, waits_for_user=True)

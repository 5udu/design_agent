"""Image generation, reference editing and optional text inspection."""
import base64
import io
from PIL import Image
from app.tools.base import ToolContext, ToolDefinition


IMAGE_SIZES = {'1:1': '2048x2048', '4:3': '2304x1728', '3:4': '1728x2304',
               '16:9': '2560x1440', '9:16': '1440x2560', '7:10': '1680x2400'}
ASPECT = {'type': 'string', 'enum': list(IMAGE_SIZES), 'description': '图片宽高比；默认1:1，按用户目标选择'}


def image_parameters(args: dict, title_key: str) -> tuple[str, str, str]:
    prompt, title = args.get('prompt'), args.get(title_key)
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000 or not isinstance(title, str) or not title.strip():
        raise ValueError('prompt 和图片标题必须是非空字符串，prompt 不超过 4000 字')
    ratio = args.get('aspect_ratio', '1:1')
    if ratio not in IMAGE_SIZES:
        raise ValueError('不支持的图片宽高比')
    return prompt, title, IMAGE_SIZES[ratio]


def save_image(context: ToolContext, run: dict, content: bytes, usage: dict,
               prompt: str, title: str, size: str, source: dict | None = None) -> dict:
    asset = context.assets.save_image(run['project_id'], run['id'], content,
        prompt=prompt, title=title, requested_size=size, usage=usage, source=source)
    asset_id, version_id = asset['id'], asset['version_id']
    context.store.update_run(run['id'], working_version_id=version_id)
    context.store.append(run['id'], 'artifact_created', {'asset': asset})
    return {'status': 'succeeded', 'asset_ids': [asset_id], 'version_id': version_id,
            'artifact': asset, 'quality_status': 'visual_review_required', 'usage': usage}


async def generate_image(context: ToolContext, run: dict, args: dict) -> dict:
    prompt, title, size = image_parameters(args, 'direction')
    content, usage = await context.submit_image(prompt, None, size)
    return save_image(context, run, content, usage, prompt, title, size)


async def edit_image(context: ToolContext, run: dict, args: dict) -> dict:
    prompt, title, size = image_parameters(args, 'title')
    source, source_path = context.assets.get(run['project_id'], args.get('reference_asset_id'))
    with Image.open(source_path) as picture:
        output = io.BytesIO()
        picture.save(output, format='PNG')
    reference = 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode()
    content, usage = await context.submit_image(prompt, reference, size)
    return save_image(context, run, content, usage, prompt, title, size, source)


async def inspect_image(context: ToolContext, run: dict, args: dict) -> dict:
    asset, path = context.assets.get(run['project_id'], args.get('asset_id'))
    with Image.open(path) as picture:
        picture.verify()
    result = {'status': 'succeeded', 'asset_ids': [asset['id']], 'width': asset.get('width'),
              'height': asset.get('height'), 'reference_asset_id': asset.get('reference_asset_id'),
              'quality_status': 'visual_review_required', 'note': '实际图片已返回给主模型观察，文件可读取不等于视觉要求全部达成'}
    if args.get('check_text'):
        result['ocr'] = await context.ocr.read(path)
    return result


GENERATE_IMAGE = ToolDefinition(
    name='generate_image', description='生成一张完整交付图片。产品需求生成产品本体效果图；一次调用一张。',
    properties={'prompt': {'type': 'string', 'description': '完整图片描述，明确产品形体、风格、构图、光照及需要出现的文字'},
                'direction': {'type': 'string', 'description': '这张图片的简短标题'}, 'aspect_ratio': ASPECT},
    required=['prompt', 'direction'], handler=generate_image)
EDIT_IMAGE = ToolDefinition(
    name='edit_image', description='基于指定图片和用户反馈编辑，返回完整新图片，保留原图与参考关系',
    properties={'reference_asset_id': {'type': 'string'}, 'prompt': {'type': 'string'},
                'title': {'type': 'string'}, 'aspect_ratio': ASPECT},
    required=['reference_asset_id', 'prompt', 'title'], handler=edit_image)
INSPECT_IMAGE = ToolDefinition(
    name='inspect_image', description='查看实际图片、尺寸与引用关系；只在需要核对文字时启用OCR',
    properties={'asset_id': {'type': 'string'}, 'check_text': {'type': 'boolean'}},
    required=['asset_id'], handler=inspect_image)

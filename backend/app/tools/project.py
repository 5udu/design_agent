"""Read confirmed project facts and image references."""
from app.domain.projects import project_facts
from app.tools.base import ToolContext, ToolDefinition


async def read_project(context: ToolContext, run: dict, args: dict) -> dict:
    project = context.store.get('projects', run['project_id'])
    images = context.store.list('assets', run['project_id'])
    version_id = context.store.get('runs', run['id']).get('working_version_id')
    version = context.store.get('versions', version_id) if version_id else None
    reference_id = version['asset_id'] if version else run.get('target_asset_id')
    current = context.assets.get(run['project_id'], reference_id)[0] if reference_id else None
    fields = ['id', 'version_id', 'title', 'kind', 'prompt', 'width', 'height', 'reference_asset_id', 'run_id']
    return {
        'status': 'succeeded', 'project': project_facts(project), 'current_image': current,
        'images': [{key: image[key] for key in fields if key in image} for image in images[-20:]],
        'asset_ids': [current['id']] if current else [],
        'capabilities': ['generate_image', 'reference_edit', 'actual_image_observation'],
        'session_id': run['session_id']}


READ_PROJECT = ToolDefinition(
    name='read_project', description='读取项目已知信息、当前指定图片及历史图片索引',
    properties={}, required=[], handler=read_project)

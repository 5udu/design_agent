"""Persist a finished run and select its final image outputs."""


def finish_run(store, run, summary, steps):
    calls = store.tool_calls(run['id'])
    errors = [call for call in calls if call['status'] in {'failed', 'unknown'}]
    current = store.get('runs', run['id']).get('working_version_id')
    version = store.get('versions', current) if current else None
    recovered = bool(version and version.get('run_id') == run['id'] and version.get('asset_id'))
    status = 'partial' if any(call['status'] == 'unknown' for call in errors) or (errors and not recovered) else 'succeeded'
    quality = version.get('quality_status', 'unverified') if version else 'unverified'
    created = [call['result']['artifact'] for call in calls
               if call.get('status') == 'succeeded' and call.get('result', {}).get('artifact', {}).get('kind') == 'product_render']
    replaced = {image.get('reference_asset_id') for image in created}
    outputs = [image['id'] for image in created if image['id'] not in replaced]
    if outputs and status == 'succeeded':
        project = store.get('projects', run['project_id'])
        project['current_image_id'] = outputs[-1]
        store.put('projects', project)
    store.transition(run['id'], 'run_status', {'status': status, 'quality_status': quality},
                     status=status, summary=summary, quality_status=quality,
                     steps=steps, output_asset_ids=outputs)

"""Isolated browser regression with local model/image doubles; no paid API calls.

Run with a Python that has Playwright installed. The server uses the project venv.
"""
import argparse
import asyncio
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def serve(port):
    from contextlib import asynccontextmanager
    import uvicorn
    from PIL import Image
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from app import main as api
    from app.agent.graph import build_graph
    from app.agent.instructions import SYSTEM
    from app.runtime.gateway import Gateway
    from app.runtime.worker import process_run

    class Images:
        calls = 0

        async def generate(self, prompt, reference=None, size=None):
            self.calls += 1
            output = io.BytesIO()
            Image.new('RGB', (512, 512), '#173f68' if reference else '#b83b32').save(output, format='PNG')
            return output.getvalue(), {'model': 'local-image-double'}

    class Model:
        async def summarize(self, messages, *, max_tokens):
            return '用户要求保留 boss 标志；之前的创作历史已完成。当前参考图由本轮明确指定。', {'model': 'local-summary-double'}

        async def complete(self, messages, tools, **options):
            run = next(r for r in api.store.list('runs') if r['status'] == 'running')
            calls = api.store.tool_calls(run['id'])
            if not calls:
                if '询问' in run['message']:
                    name, args = 'ask_user', {'question': '你希望保留什么颜色？', 'options': ['深蓝色', '红色']}
                elif '修改' in run['message']:
                    name, args = 'edit_image', {'reference_asset_id': run['target_asset_id'], 'prompt': '深蓝色饭盒', 'title': '深蓝色饭盒'}
                else:
                    name, args = 'generate_image', {'prompt': '红色饭盒，保留 boss 标志', 'direction': '红色饭盒'}
                return {'content': '正在处理你的创作要求。', 'reasoning_content': 'private-ui-double',
                        'tool_calls': [{'id': 'local-call', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}, {}
            return {'content': '已完成本次创作。', 'reasoning_content': 'private-ui-double'}, {}

    images = Images()
    project = api.store.put('projects', {'id': 'session-ui', 'name': 'Session 浏览器回归', 'brand': 'boss', 'product': '饭盒', 'approved_copy': []})
    for index in range(8):
        run = api.store.enqueue({'id': f'history-{index}', 'project_id': project['id'], 'status': 'queued',
                                'message': '保留 boss 标志。'+('较早的历史描述，用户已确认。'*150), 'reasoning_effort': 'off'},
                               {'project': {'name': project['name']}, 'target_asset_id': None}, SYSTEM)
        api.store.append(run['id'], 'assistant_message', {'text': '历史任务已完成。', 'phase': 'final'},
                         model_data={'role': 'assistant', 'content': '历史任务已完成。'})
        api.store.update_run(run['id'], status='succeeded', summary='历史任务已完成。')

    @asynccontextmanager
    async def lifespan(app):
        async with AsyncSqliteSaver.from_conn_string(str(api.settings.data_dir/'checkpoints.db')) as cp:
            graph = build_graph(api.store, Model(), Gateway(api.store, images, api.settings), api.settings, cp)

            async def work():
                while True:
                    run = api.store.claim()
                    if run:
                        await process_run(graph, api.store, run, api.settings)
                    else:
                        await asyncio.sleep(0.05)
            worker = asyncio.create_task(work())
            try:
                yield
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)

    api.app.router.lifespan_context = lifespan

    uvicorn.run(api.app, host='127.0.0.1', port=port, log_level='warning')


def verify():
    import httpx
    from playwright.sync_api import sync_playwright, expect
    output = ROOT/'outputs'/'session-context'
    output.mkdir(parents=True, exist_ok=True)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='session-workspace-ui-') as folder:
        environment = {**os.environ, 'DATA_DIR': folder, 'CONTEXT_WINDOW_TOKENS': '100000',
                       'CONTEXT_OUTPUT_RESERVE': '1000', 'CONTEXT_SAFETY_TOKENS': '500',
                       'CONTEXT_COMPACT_RATIO': '0.08', 'CONTEXT_RETAIN_TOKENS': '300',
                       'CONTEXT_SUMMARY_MAX_TOKENS': '256'}
        with (output/'server.log').open('w', encoding='utf8') as log:
            process = subprocess.Popen([str(ROOT/'.venv/Scripts/python.exe'), str(Path(__file__).resolve()), '--serve', '--port', str(port)],
                cwd=ROOT, env=environment, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            try:
                base = f'http://127.0.0.1:{port}'
                with httpx.Client(base_url=base, timeout=5) as client:
                    for _ in range(100):
                        try:
                            if client.get('/api/health').status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        if process.poll() is not None:
                            raise RuntimeError('Isolated server failed; inspect outputs/session-context/server.log')
                        time.sleep(0.1)
                    def wait(run_id, status):
                        for _ in range(150):
                            detail = client.get('/api/runs/'+run_id).json()
                            if detail['run']['status'] == status:
                                return detail
                            if detail['run']['status'] in {'failed', 'partial'}:
                                raise AssertionError(detail['run'])
                            time.sleep(0.1)
                        raise AssertionError('Task did not reach '+status)

                    with sync_playwright() as playwright:
                        browser = playwright.chromium.launch(channel='msedge', headless=True)
                        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                        errors = []
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        page.goto(base+'/?project=session-ui', wait_until='domcontentloaded')
                        expect(page.locator('.conversation')).to_have_count(8)
                        def send(text):
                            page.get_by_label('图像需求或修改意见').fill(text)
                            with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/runs')) as result:
                                page.get_by_role('button', name='发送需求', exact=True).click()
                            return result.value.json()['id']
                        first = send('UI生成：生成饭盒并保留 boss 标志')
                        generated = wait(first, 'succeeded')
                        article = page.locator(f'[data-run-id="{first}"].conversation')
                        expect(article.locator('.image-gallery img')).to_have_count(1)
                        expect(article.locator('.agent-trace')).to_have_js_property('open', False)
                        article.locator('.agent-trace > summary').click()
                        expect(article.locator('.trace-content')).to_contain_text('完整历史仍可查看')
                        expect(article.locator('.trace-result')).to_have_count(1)
                        page.screenshot(path=str(output/'workspace.png'), full_page=True)
                        page.reload(wait_until='domcontentloaded')
                        expect(page.locator('.conversation')).to_have_count(9)
                        edited = send('UI修改：把指定饭盒改成深蓝色')
                        edit = wait(edited, 'succeeded')
                        detail = client.get('/api/projects/session-ui?include_events=true').json()
                        output_asset = next(a for a in detail['assets'] if a['id'] == edit['run']['output_asset_ids'][0])
                        assert output_asset['reference_asset_id'] == generated['run']['output_asset_ids'][0]
                        expect(page.locator('.image-gallery img')).to_have_count(2)
                        question = send('UI询问：先询问颜色再继续，不生成图片')
                        wait(question, 'waiting_user')
                        expect(page.locator('.waiting-notice')).to_be_visible()
                        page.reload(wait_until='domcontentloaded')
                        expect(page.locator('.waiting-notice')).to_be_visible()
                        page.get_by_label('图像需求或修改意见').fill('深蓝色')
                        page.get_by_role('button', name='发送需求', exact=True).click()
                        wait(question, 'succeeded')
                        expect(page.locator('.waiting-notice')).to_have_count(0)
                        expect(page.locator('.reply-message')).to_contain_text('深蓝色')
                        latest = page.locator(f'[data-run-id="{question}"].conversation')
                        latest.locator('.agent-trace > summary').click()
                        expect(latest.locator('.trace-result')).to_have_count(1)
                        expect(latest.locator('.trace-result')).to_contain_text('已完成')
                        assert 'private-ui-double' not in page.content()
                        journal=client.get('/api/sessions/'+detail['session']['id']).json()['events']
                        assert sum(event['kind']=='tool_submitted' for event in journal) == 2
                        page.locator('.messages').evaluate('(element)=>element.scrollTo({top:0,behavior:"instant"})')
                        expect(page.get_by_role('button',name='回到底部',exact=True)).to_be_visible()
                        page.get_by_role('button',name='回到底部',exact=True).click()
                        expect(page.get_by_role('button',name='回到底部',exact=True)).to_have_count(0)
                        composer=page.get_by_label('图像需求或修改意见')
                        short_height=composer.bounding_box()['height']
                        composer.fill('\n'.join(['多行创作要求']*6))
                        assert composer.bounding_box()['height']>short_height
                        composer.fill('')
                        page.get_by_label('搜索项目').fill('不存在的项目')
                        expect(page.locator('.search-empty')).to_be_visible()
                        page.locator('.search-empty button').click()
                        expect(page.locator('.project-row')).to_have_count(1)
                        remove=page.get_by_role('button',name='删除项目：Session 浏览器回归',exact=True)
                        remove.click()
                        dialog=page.get_by_role('alertdialog')
                        expect(dialog.get_by_role('button',name='取消',exact=True)).to_be_focused()
                        dialog.get_by_role('button',name='关闭删除确认',exact=True).focus()
                        page.keyboard.press('Shift+Tab')
                        expect(dialog.get_by_role('button',name='删除项目',exact=True)).to_be_focused()
                        page.keyboard.press('Tab')
                        expect(dialog.get_by_role('button',name='关闭删除确认',exact=True)).to_be_focused()
                        dialog.get_by_role('button',name='取消',exact=True).click()
                        expect(dialog).to_have_count(0)
                        expect(remove).to_be_focused()
                        assert client.get('/api/projects/session-ui').status_code==200
                        remove.click()
                        def refuse_delete(route):
                            if route.request.method=='DELETE':
                                route.fulfill(status=503,content_type='application/json',body=json.dumps({'detail':'删除失败，请重试'}))
                            else:
                                route.continue_()
                        page.route('**/api/projects/session-ui',refuse_delete)
                        dialog.get_by_role('button',name='删除项目',exact=True).click()
                        expect(dialog.get_by_role('alert')).to_contain_text('删除失败')
                        page.unroute('**/api/projects/session-ui',refuse_delete)
                        page.screenshot(path=str(output/'delete-dialog.png'),full_page=True)
                        dialog.get_by_role('button',name='取消',exact=True).click()
                        def create(name):
                            page.get_by_role('button',name='新建项目',exact=True).click()
                            create_dialog=page.get_by_role('dialog')
                            expect(create_dialog.get_by_label('项目名称',exact=True)).to_be_focused()
                            create_dialog.get_by_label('项目名称',exact=True).fill(name)
                            with page.expect_response(lambda response:response.request.method=='POST' and response.url.endswith('/projects')) as result:
                                create_dialog.get_by_role('button',name='创建项目',exact=True).click()
                            project_id=result.value.json()['id']
                            expect(page.locator('.project-title')).to_contain_text(name)
                            expect(page.get_by_label('图像需求或修改意见')).to_be_enabled()
                            return project_id
                        keep=create('保留的项目')
                        pending=send('UI询问：删除前的等待任务')
                        wait(pending,'waiting_user')
                        expect(page.locator('.waiting-notice')).to_be_visible()
                        page.get_by_role('button',name='删除项目：保留的项目',exact=True).click()
                        expect(dialog.get_by_role('button',name='删除项目',exact=True)).to_be_disabled()
                        expect(dialog).to_contain_text('请先完成或结束任务')
                        dialog.get_by_role('button',name='取消',exact=True).click()
                        page.get_by_role('button',name='结束此任务',exact=True).click()
                        wait(pending,'cancelled')
                        expect(page.locator('.waiting-notice')).to_have_count(0)
                        current=create('另一个项目')
                        other=browser.new_page(viewport={'width':1440,'height':1000})
                        other.on('pageerror',lambda error:errors.append(str(error)))
                        other.goto(base+'/?project=session-ui',wait_until='domcontentloaded')
                        expect(other.locator('.conversation')).to_have_count(11)
                        page.get_by_role('button',name='删除项目：Session 浏览器回归',exact=True).click()
                        dialog.get_by_role('button',name='删除项目',exact=True).click()
                        expect(dialog).to_have_count(0)
                        expect(page.get_by_role('button',name='删除项目：Session 浏览器回归',exact=True)).to_have_count(0)
                        expect(page.locator('.project-title')).to_contain_text('另一个项目')
                        expect(other.locator('.project-title')).to_contain_text('另一个项目')
                        assert client.get('/api/projects/'+keep).status_code==200
                        assert client.get('/api/assets/'+generated['run']['output_asset_ids'][0]+'/file').status_code==404
                        other.close()
                        page.get_by_role('button',name='删除项目：另一个项目',exact=True).click()
                        dialog.get_by_role('button',name='删除项目',exact=True).click()
                        expect(dialog).to_have_count(0)
                        expect(page.locator('.project-title')).to_contain_text('保留的项目')
                        assert client.get('/api/projects/'+current).status_code==404
                        page.get_by_role('button',name='删除项目：保留的项目',exact=True).click()
                        dialog.get_by_role('button',name='删除项目',exact=True).click()
                        expect(dialog).to_have_count(0)
                        expect(page.locator('.project-row')).to_have_count(0)
                        expect(page.locator('.welcome h1')).to_contain_text('想把什么变成图片')
                        assert page.evaluate('localStorage.getItem("packaging-project")') is None
                        assert '?project=' not in page.url
                        page.reload(wait_until='domcontentloaded')
                        expect(page.locator('.sidebar-empty')).to_be_visible()
                        fresh=create('重新开始')
                        page.set_viewport_size({'width': 390, 'height': 844})
                        page.get_by_role('button',name='打开项目列表',exact=True).click()
                        mobile_remove=page.get_by_role('button',name='删除项目：重新开始',exact=True)
                        expect(mobile_remove).to_have_css('opacity','1')
                        mobile_remove.click()
                        expect(dialog).to_be_visible()
                        page.screenshot(path=str(output/'delete-mobile.png'),full_page=True)
                        page.keyboard.press('Escape')
                        expect(dialog).to_have_count(0)
                        mobile_remove.click()
                        dialog.get_by_role('button',name='删除项目',exact=True).click()
                        expect(dialog).to_have_count(0)
                        expect(page.locator('.project-sidebar')).not_to_have_class('project-sidebar is-open')
                        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
                        assert client.get('/api/projects').json()==[]
                        assert client.get('/api/projects/'+fresh).status_code==404
                        page.set_viewport_size({'width':390,'height':420})
                        composer=page.get_by_label('图像需求或修改意见')
                        expect(composer).to_be_visible()
                        box=composer.bounding_box()
                        assert box['y']+box['height']<=420
                        assert not errors, errors
                        browser.close()
                    report = {'status': 'passed', 'paid_requests': 0, 'local_image_calls': 2,
                              'history_runs': 8, 'compaction_and_trace': True, 'reload': True,
                              'reference_edit': True, 'ask_resume': True, 'private_reasoning_hidden': True,
                              'mobile_layout': True, 'delete_cancel_and_retry': True, 'delete_active_blocked': True,
                              'delete_current_and_noncurrent': True, 'delete_last_and_recreate': True,
                              'delete_other_tab_updates': True, 'mobile_delete': True, 'dialog_keyboard': True,
                              'search_empty': True, 'composer_autogrow': True, 'jump_to_latest': True,
                              'short_viewport_composer': True, 'browser_errors': errors}
                    (output/'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
                    print(json.dumps(report))
            finally:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
                elif process.poll() is None:
                    process.terminate()
                process.wait(timeout=10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--port', type=int, default=8012)
    arguments = parser.parse_args()
    if arguments.serve:
        serve(arguments.port)
    else:
        verify()

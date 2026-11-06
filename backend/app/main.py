import asyncio
import json
import sqlite3
import time
import secrets
import hmac
import logging
from contextlib import asynccontextmanager
from typing import get_args
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel, Field, ConfigDict
from app.auth import Auth, COOKIE, public_user
from psycopg import IntegrityError as PostgresIntegrityError
from fastapi.staticfiles import StaticFiles
from app.config import ROOT, settings
from app.domain.models import ProjectCreate, RunCreate, ResumeInput, ReasoningEffort
from app.persistence.repositories import Store, uid
from app.persistence.assets import Assets
from app.persistence.projects import delete_project as remove_project
from app.persistence.locking import LockBusy
from app.agent.instructions import SYSTEM
from app.domain.projects import project_facts

store = Store(settings.data_dir, settings.database_url, settings.db_pool_size)
application_store = store
assets = Assets(store, settings)


@asynccontextmanager
async def lifespan(app):
    yield
    application_store.close()


app = FastAPI(title='图像生成与编辑 Agent', version='0.3.0', lifespan=lifespan)


@app.middleware('http')
async def security_headers(request: Request, call_next):
    started = time.monotonic()
    request_id = secrets.token_hex(8)
    if request.url.path.startswith('/api/') and request.method not in {'GET', 'HEAD', 'OPTIONS'}:
        origin = request.headers.get('origin')
        if request.headers.get('sec-fetch-site') == 'cross-site' or (origin and origin.rstrip('/') != settings.app_origin.rstrip('/')):
            return JSONResponse({'detail': '请求来源不受信任'}, status_code=403)
        if request.url.path != '/api/auth/login':
            session = await asyncio.to_thread(Auth(store).session, request.cookies.get(COOKIE))
            if not session:
                return JSONResponse({'detail': '请先登录'}, status_code=401)
            if not hmac.compare_digest(request.headers.get('x-csrf-token', '').encode(), session['csrf_token'].encode()):
                return JSONResponse({'detail': '登录校验已失效，请刷新页面'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Request-ID'] = request_id
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
        logging.getLogger('uvicorn.error').info('request id=%s method=%s path=%s status=%s elapsed_ms=%s', request_id, request.method, request.url.path, response.status_code, round((time.monotonic()-started)*1000))
    return response


def current_user(request: Request):
    session = Auth(store).session(request.cookies.get(COOKIE))
    if not session:
        raise HTTPException(401, '请先登录')
    if session['user']['must_change_password'] and request.url.path not in {'/api/auth/me', '/api/auth/password', '/api/auth/logout'}:
        raise HTTPException(403, '请先修改初始密码')
    return session['user']


def admin_user(user=Depends(current_user)):
    if not user['is_admin']:
        raise HTTPException(403, '需要管理员权限')
    return user


def authorized_project(project_id, user, write=False, owner=False):
    project = require('projects', project_id)
    role = Auth(store).role(user, project)
    if not role:
        raise HTTPException(404, '项目不存在或无权访问')
    if (owner and role != 'owner') or (write and role == 'viewer'):
        raise HTTPException(403, '没有执行此操作的权限')
    return {**project, 'role': role}


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


class PasswordInput(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class UserInput(LoginInput):
    display_name: str = Field(default='', max_length=100)
    is_admin: bool = False


class MemberInput(BaseModel):
    role: str = Field(pattern='^(editor|viewer)$')


class UserUpdate(BaseModel):
    disabled: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=256)


@app.post('/api/auth/login')
def login(body: LoginInput, request: Request):
    result, error = Auth(store).login(body.username, body.password, request.client.host if request.client else 'unknown', settings.session_ttl_seconds)
    if error:
        raise HTTPException(429 if error == 'limited' else 401, '登录尝试过多，请 15 分钟后重试' if error == 'limited' else '账号或密码不正确')
    response = JSONResponse({'user': result['user'], 'csrf_token': result['csrf_token']})
    response.set_cookie(COOKIE, result['token'], max_age=settings.session_ttl_seconds, httponly=True, secure=settings.cookie_secure, samesite='lax', path='/')
    return response


@app.get('/api/auth/me')
def me(request: Request, user=Depends(current_user)):
    return Auth(store).session(request.cookies.get(COOKIE))


@app.post('/api/auth/logout')
def logout(request: Request, user=Depends(current_user)):
    Auth(store).logout(request.cookies[COOKIE])
    response = JSONResponse({'ok': True})
    response.delete_cookie(COOKIE, path='/', secure=settings.cookie_secure, httponly=True, samesite='lax')
    return response


@app.post('/api/auth/password')
def password(body: PasswordInput, user=Depends(current_user)):
    try:
        Auth(store).change_password(user['id'], body.current_password, body.new_password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return {'ok': True}


@app.get('/api/admin/users')
def users(user=Depends(admin_user)):
    with store.connect() as db:
        return [public_user(row) for row in db.execute('SELECT * FROM users ORDER BY username')]


@app.post('/api/admin/users', status_code=201)
def create_user(body: UserInput, user=Depends(admin_user)):
    try:
        return Auth(store).create_user(body.username, body.password, body.display_name, body.is_admin, user['id'])
    except (sqlite3.IntegrityError, PostgresIntegrityError):
        raise HTTPException(409, '账号已存在') from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@app.patch('/api/admin/users/{user_id}')
def update_user(user_id: str, body: UserUpdate, user=Depends(admin_user)):
    target = Auth(store).user(user_id)
    if not target:
        raise HTTPException(404, '账号不存在')
    if body.disabled is not None:
        if user_id == user['id'] or target['is_admin']:
            raise HTTPException(409, '管理员账号不能通过此入口停用')
        with store.connect() as db:
            db.execute('UPDATE users SET disabled=? WHERE id=?', (int(body.disabled), user_id))
            db.execute('DELETE FROM auth_sessions WHERE user_id=?', (user_id,))
            Auth(store).audit(user['id'], 'user_disabled' if body.disabled else 'user_enabled', user_id, db=db)
    if body.password:
        Auth(store).set_password(user_id, body.password, user['id'])
    return Auth(store).user(user_id)


@app.get('/api/admin/operations')
def operations(user=Depends(admin_user)):
    with store.connect() as db:
        counts = {r['status']: r['count'] for r in db.execute('SELECT status,COUNT(*) AS count FROM runs GROUP BY status')}
        audit = [dict(row) for row in db.execute('SELECT id,actor_id,action,target_id,created_at FROM audit_log ORDER BY created_at DESC LIMIT 100')]
        workers = [dict(row) for row in db.execute('SELECT id,updated_at FROM workers WHERE updated_at>?', (time.time()-30,))]
        oldest = db.execute("SELECT MIN(created_at) FROM runs WHERE status='queued'").fetchone()[0]
    return {'runs': counts, 'audit': audit, 'workers': workers, 'oldest_queue_seconds': max(0, time.time()-oldest) if oldest else 0}


def require(table, key):
    item = store.get(table, key)
    if not item:
        raise HTTPException(404, '记录不存在')
    return item


@app.get('/api/health/live')
def live():
    return {'status': 'ok'}


@app.get('/api/health/ready')
def ready():
    try:
        with store.connect() as db:
            db.execute('SELECT 1').fetchone()
        return {'status': 'ok'}
    except Exception:
        raise HTTPException(503, '存储服务暂不可用') from None


@app.get('/api/health')
def health(user=Depends(current_user)):
    return {'status': 'ok', 'models': {'agent': settings.deepseek_model, 'image': settings.seedream_model},
            'agent_settings': {'thinking': 'disabled' if settings.deepseek_reasoning_effort == 'off' else 'enabled',
                               'reasoning_effort': settings.deepseek_reasoning_effort,
                               'supported_reasoning_efforts': list(get_args(ReasoningEffort))},
            'credentials': {'agent_configured': bool(settings.deepseek_api_key), 'image_configured': bool(settings.seedream_api_key)},
            'capabilities': ['product_image_generation','reference_image_editing','session_history','agent_trace','context_compaction','project_deletion'],
            'pending': ['broader_image_quality_evaluation']}


@app.get('/api/projects')
def projects(limit: int = 100, offset: int = 0, user=Depends(current_user)):
    if not 1 <= limit <= 200 or offset < 0:
        raise HTTPException(400, '分页参数无效')
    return Auth(store).visible_projects(user, limit, offset)


@app.post('/api/projects', status_code=201)
def create_project(body: ProjectCreate, user=Depends(current_user)):
    project = store.put('projects', {'id': uid(), **body.model_dump(), 'owner_id': user['id'], 'created_at': time.time()})
    Auth(store).audit(user['id'], 'project_created', project['id'])
    return {**project, 'role': 'owner'}


@app.delete('/api/projects/{project_id}')
def delete_project(project_id: str, user=Depends(current_user)):
    authorized_project(project_id, user, owner=True)
    try:
        result = remove_project(store, project_id)
        Auth(store).audit(user['id'], 'project_deleted', project_id)
        return result
    except KeyError:
        raise HTTPException(404, '项目不存在或已删除') from None
    except (ValueError, LockBusy) as exc:
        raise HTTPException(409, str(exc)) from None


@app.get('/api/projects/{project_id}')
def project_detail(project_id: str,include_events:bool=False, user=Depends(current_user)):
    project = authorized_project(project_id, user)
    try:
        session = store.ensure_session(project_id)
    except KeyError:
        raise HTTPException(404, '项目不存在或已删除') from None
    cursor = store.cursor(session['id'])
    result={'project': project, 'session': session, 'cursor': cursor, 'assets': store.list('assets', project_id), 'versions': store.list('versions', project_id), 'runs': store.list('runs', project_id)}
    if include_events:
        events = store.session_events(session['id'], through=cursor)
        result['conversations']=[{'run':r,'events':[e for e in events if e['run_id']==r['id']]} for r in result['runs']]
    return result


@app.post('/api/projects/{project_id}/runs', status_code=202)
def create_run(project_id: str, body: RunCreate, user=Depends(current_user)):
    project=authorized_project(project_id, user, write=True)
    reference=body.target_asset_id
    if reference is None:reference=project.get('current_image_id')
    if reference is None:
        images=store.list('assets',project_id)
        if images:reference=images[-1]['id']
    target=None
    if reference:
        try:asset,_=assets.get(project_id,reference);target=asset.get('version_id')
        except ValueError as exc:raise HTTPException(400,str(exc)) from None
    active = [r for r in store.list('runs', project_id) if r['status'] in {'queued', 'running', 'waiting_user'}]
    if active:
        raise HTTPException(409, '当前项目已有进行中的任务，请先继续或结束它')
    run = {'id': uid(), 'project_id': project_id, 'user_id': user['id'], 'message': body.message,'target_asset_id':reference,'working_version_id':target,
           'reasoning_effort': body.reasoning_effort if body.reasoning_effort is not None else settings.deepseek_reasoning_effort,
           'status': 'queued', 'quality_status': 'unverified', 'created_at': time.time()}
    try:
        run = store.enqueue(run, {'project': project_facts(project), 'target_asset_id': reference}, SYSTEM)
    except (sqlite3.IntegrityError, PostgresIntegrityError):
        raise HTTPException(409, '当前项目已有进行中的任务') from None
    except KeyError:
        raise HTTPException(404, '项目不存在或已删除') from None
    Auth(store).audit(user['id'], 'run_queued', run['id'])
    return run


@app.get('/api/runs/{run_id}')
def run_detail(run_id: str, user=Depends(current_user)):
    run = require('runs', run_id)
    authorized_project(run['project_id'], user)
    return {'run': run, 'events': store.events(run_id), 'tool_runs': store.tool_calls(run_id)}


@app.post('/api/runs/{run_id}/resume', status_code=202)
def resume_run(run_id: str, body: ResumeInput, user=Depends(current_user)):
    authorized_project(require('runs', run_id)['project_id'], user, write=True)
    try:
        result = store.resume(run_id, body.answer)
        Auth(store).audit(user['id'], 'run_resumed', run_id)
        return result
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@app.post('/api/runs/{run_id}/cancel')
def cancel_run(run_id: str, user=Depends(current_user)):
    authorized_project(require('runs', run_id)['project_id'], user, write=True)
    try:
        result = store.cancel(run_id)
        Auth(store).audit(user['id'], 'run_cancelled', run_id)
        return result
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@app.get('/api/sessions/{session_id}')
def session_detail(session_id: str, after: int = 0, user=Depends(current_user)):
    with store.connect() as db:
        row = db.execute('SELECT data FROM sessions WHERE id=?', (session_id,)).fetchone()
    if not row:
        raise HTTPException(404, '对话不存在')
    authorized_project(json.loads(row['data'])['project_id'], user)
    cursor = store.cursor(session_id)
    return {'session': json.loads(row['data']), 'cursor': cursor,
            'events': store.session_events(session_id, after, through=cursor)}


@app.get('/api/sessions/{session_id}/events')
async def events(session_id: str, request: Request, after: int = 0, user=Depends(current_user)):
    session = await asyncio.to_thread(session_detail, session_id, after, user)
    session = session['session']
    last = request.headers.get('last-event-id', '')
    cursor = max(after, int(last) if last.isdigit() else 0)
    project = await asyncio.to_thread(store.get, 'projects', session['project_id'])
    initial_role = await asyncio.to_thread(Auth(store).role, user, project) if project else None

    async def stream():
        nonlocal cursor
        last_role = initial_role
        while not await request.is_disconnected():
            identity = await asyncio.to_thread(Auth(store).session, request.cookies.get(COOKIE))
            project = await asyncio.to_thread(store.get, 'projects', session['project_id'])
            role = await asyncio.to_thread(Auth(store).role, identity['user'], project) if identity and project else None
            if not identity or identity['user']['must_change_password'] or (project and not role):
                yield 'event: access_revoked\ndata: {}\n\n'
                return
            if not project:
                yield 'event: project_deleted\ndata: '+json.dumps({'project_id': session['project_id']})+'\n\n'
                return
            if role != last_role:
                yield 'event: permissions_changed\ndata: '+json.dumps({'role': role})+'\n\n'
                last_role = role
            batch = await asyncio.to_thread(store.session_events, session_id, cursor)
            for event in batch:
                cursor = event['seq']
                yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            yield ': heartbeat\n\n'
            await asyncio.sleep(settings.sse_poll_seconds)
    return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.get('/api/assets/{asset_id}/file')
def asset_file(asset_id: str,download:bool=False, user=Depends(current_user)):
    asset = require('assets', asset_id)
    authorized_project(asset['project_id'], user)
    path = (settings.data_dir / asset['path']).resolve()
    if not path.is_relative_to(settings.data_dir.resolve()) or not path.is_file():
        raise HTTPException(404, '图片文件不存在')
    return FileResponse(path, media_type='image/png',filename=f'{asset_id}.png' if download else None)


@app.get('/api/projects/{project_id}/members')
def members(project_id: str, user=Depends(current_user)):
    project = authorized_project(project_id, user)
    with store.connect() as db:
        rows = [dict(row) for row in db.execute('SELECT u.id,u.username,u.display_name,u.disabled,m.role FROM project_members m JOIN users u ON u.id=m.user_id WHERE m.project_id=? ORDER BY u.username', (project_id,))]
    owner = Auth(store).user(project['owner_id'])
    return [{'id': owner['id'], 'username': owner['username'], 'display_name': owner['display_name'], 'disabled': owner['disabled'], 'role': 'owner'}, *rows]


def member_by_username(username, allow_disabled=False):
    with store.connect() as db:
        row = db.execute('SELECT * FROM users WHERE username=?'+('' if allow_disabled else ' AND disabled=0'), (username.lower(),)).fetchone()
    if not row:
        raise HTTPException(404, '员工账号不存在或已停用')
    return public_user(row)


@app.put('/api/projects/{project_id}/members/{username}')
def set_member(project_id: str, username: str, body: MemberInput, user=Depends(current_user)):
    project = authorized_project(project_id, user, owner=True)
    target = member_by_username(username)
    if target['id'] == project['owner_id']:
        raise HTTPException(409, '请通过转交项目修改负责人')
    with store.connect() as db:
        db.execute('INSERT INTO project_members VALUES(?,?,?) ON CONFLICT(project_id,user_id) DO UPDATE SET role=excluded.role', (project_id, target['id'], body.role))
        Auth(store).audit(user['id'], 'membership_changed', project_id, {'user_id': target['id'], 'role': body.role}, db)
    return {'ok': True}


@app.delete('/api/projects/{project_id}/members/{username}')
def remove_member(project_id: str, username: str, user=Depends(current_user)):
    authorized_project(project_id, user, owner=True)
    target = member_by_username(username, allow_disabled=True)
    with store.connect() as db:
        db.execute('DELETE FROM project_members WHERE project_id=? AND user_id=?', (project_id, target['id']))
        Auth(store).audit(user['id'], 'membership_removed', project_id, {'user_id': target['id']}, db)
    return {'ok': True}


@app.post('/api/projects/{project_id}/owner/{username}')
def transfer_project(project_id: str, username: str, user=Depends(current_user)):
    authorized_project(project_id, user, owner=True)
    target = member_by_username(username)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        store.lock_row(db, 'projects', project_id)
        project = json.loads(db.execute('SELECT data FROM projects WHERE id=?', (project_id,)).fetchone()['data'])
        if project['owner_id'] != user['id']:
            raise HTTPException(409, '负责人已经变更，请刷新页面')
        project['owner_id'] = target['id']
        store._put(db, 'projects', project)
        db.execute('DELETE FROM project_members WHERE project_id=? AND user_id=?', (project_id, target['id']))
        if target['id'] != user['id']:
            db.execute("INSERT INTO project_members VALUES(?,?,'editor') ON CONFLICT(project_id,user_id) DO UPDATE SET role='editor'", (project_id, user['id']))
        Auth(store).audit(user['id'], 'project_transferred', project_id, {'owner_id': target['id']}, db)
    return {'ok': True}


dist = ROOT / 'frontend' / 'dist'
if dist.is_dir():
    app.mount('/', StaticFiles(directory=dist, html=True), name='studio')

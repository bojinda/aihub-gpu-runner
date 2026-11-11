"""Install against the real pinned ComfyUI 0.15.1 server/PromptQueue.

The adapter is part of the deployed server, not an external proxy. It covers
direct/manual submissions and internal queue puts and keeps read-only clients.
"""
import hashlib
import os
from pathlib import Path
import time

from .core import RecoveryRequired
from .target_barrier import TargetBarrier, verify_submission

PINNED={'server.py':'3e261b8e88536bf5e48c0dd6f1cd3717f00653ed4e7b5fcd8e71cc2f35b71dce',
        'execution.py':'08d452334b526b7a7b81b34e0c074b057815bdc9bfd864db0960ec0b9f17e2cd'}


def install():
    import server, execution
    from aiohttp import web
    root=Path(os.environ['AIHUB_WALLPAPER_BARRIER'])
    key=(root/'submission.key').read_bytes()
    if len(key)<32:raise RuntimeError('strong_submission_key_required')
    hashes={name:hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for name,module in [('server.py',server),('execution.py',execution)]}
    if hashes!=PINNED:raise RuntimeError('unqualified_comfy_source')
    barrier=TargetBarrier(root,create=True)
    generation=barrier.register(hashes)
    app=server.PromptServer.instance
    queue=app.prompt_queue
    if queue is None:raise RuntimeError('comfy_queue_not_initialized')
    original={name:getattr(queue,name) for name in ('put','get','task_done','wipe_queue','delete_queue_item',
                                                 'wipe_history','delete_history_item','set_flag')}

    def update_queue(s):
        running,pending=queue.get_current_queue()
        s['running']={str(k):v[1] for k,v in queue.currently_running.items()}
        s['pending']=[v[1] for v in pending]

    def put(item):
        with barrier.lock.held():
            s=barrier.read()
            if s['mode']!='open':raise RecoveryRequired('wallpaper_recovery_maintenance')
            item=list(item);item[3]=dict(item[3])
            token=item[3].pop('aihub_owned_submission',None)
            claim=verify_submission(key,token,s['generation']) if token else None
            if item[1] in s['prompts']:raise RecoveryRequired('duplicate_prompt_identity')
            if claim and any(v.get('nonce')==claim['nonce'] for v in s['prompts'].values()):
                raise RecoveryRequired('owned_submission_ticket_already_used')
            s['prompts'][item[1]]={'identity':claim['identity'] if claim else None,
                                  'nonce':claim['nonce'] if claim else None}
            s.setdefault('queue_intents',{})[item[1]]='put'
            s['epoch']+=1
            barrier.store.write('target.json',s)  # intent before queue admission
            original['put'](tuple(item));update_queue(s)
            s['queue_intents'].pop(item[1]);barrier.store.write('target.json',s)

    def get(timeout=None):
        deadline=None if timeout is None else time.monotonic()+timeout
        while True:
            with barrier.lock.held():
                s=barrier.read()
                if s['mode']=='open':
                    result=original['get'](timeout=0)
                    if result is not None:
                        update_queue(s);barrier.store.write('target.json',s);return result
            if deadline is not None and time.monotonic()>=deadline:return None
            time.sleep(.02)

    def done(*args,**kwargs):
        with barrier.lock.held():
            s=barrier.read();result=original['task_done'](*args,**kwargs)
            update_queue(s);barrier.store.write('target.json',s);return result

    def mutate(name):
        def call(*args,**kwargs):
            with barrier.lock.held():
                s=barrier.read()
                if s['mode']!='open':raise RecoveryRequired('wallpaper_recovery_maintenance')
                s['epoch']+=1;barrier.store.write('target.json',s)
                value=original[name](*args,**kwargs)
                update_queue(s);barrier.store.write('target.json',s);return value
        return call

    queue.put=put;queue.get=get;queue.task_done=done
    for name in ('wipe_queue','delete_queue_item','wipe_history','delete_history_item','set_flag'):
        setattr(queue,name,mutate(name))

    @web.middleware
    async def maintenance(request, handler):
        path=request.path
        readonly=(path in ('/','/ws','/embeddings','/models','/extensions','/view','/system_stats','/features',
                          '/prompt','/object_info','/api/jobs','/history','/queue','/aihub/recovery/status',
                          '/settings','/users','/userdata') or
                  any(path.startswith(p) for p in ('/models/','/view_metadata/','/object_info/','/api/jobs/',
                        '/history/','/settings/','/userdata/','/assets/','/scripts/','/extensions/','/fonts/','/css/')) or
                  isinstance(request.match_info.route.resource,web.StaticResource))
        # Unknown custom GET routes can mutate; give them a short request permit
        # too. Standard UI/history/observer routes and idle core WS stay usable.
        if request.method=='OPTIONS' or request.method in ('GET','HEAD') and readonly:
            return await handler(request)
        permit=None
        try:
            permit=barrier.begin_mutation()
            return await handler(request)
        except RecoveryRequired as exc:
            return web.json_response({'error':str(exc)},status=409)
        finally:
            if permit is not None:barrier.end_mutation(permit)
    app.app.middlewares.insert(0,maintenance)

    @app.routes.get('/aihub/recovery/status')
    async def status(request):
        # No secret, token or workflow data is returned. The runner authenticates
        # authority through its private shared journal, not this public GET.
        s=barrier.read()
        return web.json_response({'schema':1,'generation':generation['id'],'maintenance':s['mode'],
                                  'epoch':s['epoch'],'adapter':'comfy-0.15.1-v1'})
    return barrier

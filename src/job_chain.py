"""FIFO follow-up jobs; a paused job holds the rest of its project chain."""
import json
from core import dump

def candidates(store,pid,stage,rid=None):
    if stage not in {'translate','review','cloud'}:raise ValueError('Некорректный этап')
    statuses={'translate':"('empty')",'review':"('translated')",'cloud':"('translated','edited')"}[stage]
    query=f"SELECT DISTINCT r.id,e.job FROM errors e JOIN jobs j ON j.id=e.job JOIN records r ON r.id=e.record JOIN files f ON f.id=r.file WHERE e.project=? AND f.project=? AND j.stage=? AND e.resolved=0 AND r.manual=0 AND r.status IN {statuses}"
    args=[pid,pid,stage]
    if rid:query+=' AND r.id=?';args.append(int(rid))
    with store.db() as db:return db.execute(query+' ORDER BY e.job DESC',args).fetchall()

def plan(store,pid):
    return {stage:len({row['id'] for row in candidates(store,pid,stage)}) for stage in ['translate','review','cloud']}

def bulk_budget(store,pid,stage,settings):
    from error_workflow import budget
    ids=list(dict.fromkeys(row['id'] for row in candidates(store,pid,stage)))
    if not ids:raise ValueError('Нет подходящих ошибок этого этапа')
    estimates=[budget(store,pid,rid,stage,settings) for rid in ids]
    result=estimates[0].copy()
    result.update(records=len(ids),fits_count=sum(b['fits_full'] for b in estimates),fits_full=all(b['fits_full'] for b in estimates),estimated_full=max(b['estimated_full'] for b in estimates),estimated_minimum=max(b['estimated_minimum'] for b in estimates),recommended=max(b['recommended'] for b in estimates))
    return result

def schedule(store,pid):
    from contextlib import nullcontext
    with getattr(store,'launch_lock',nullcontext()):
        return _schedule(store,pid)

def _schedule(store,pid):
    if getattr(store,'closing',False):return None
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        if not db.execute('SELECT id FROM projects WHERE id=? AND deleting=0',(pid,)).fetchone():return None
        if db.execute("SELECT id FROM jobs WHERE project=? AND (state IN ('running','queued','paused','held') OR worker_active=1)",(pid,)).fetchone():return None
        first=db.execute("SELECT id FROM jobs WHERE project=? AND state='waiting' ORDER BY id LIMIT 1",(pid,)).fetchone()
        if not first:return None
        jid=first[0];db.execute("UPDATE jobs SET state='queued' WHERE id=? AND state='waiting'",(jid,))
    try:
        from app import launch
        launch(store,jid)
    except Exception:
        with store.db() as db:db.execute("UPDATE jobs SET state='paused',worker_active=0,error='Не удалось запустить обработчик; очередь сохранена' WHERE id=?",(jid,))
    return jid

def enqueue_retry(store,data):
    from app import validate_job_settings
    from error_workflow import validate_overrides
    pid=int(data['project']);stage=data['stage'];rid=data.get('record');mode=data.get('mode','original')
    if mode not in {'original','custom'}:raise ValueError('Некорректный повтор')
    rows=candidates(store,pid,stage,rid)
    with store.db() as db:
        if not rows:raise ValueError('Нет подходящих ошибок; ручные правки защищены')
        ids=list(dict.fromkeys(r[0] for r in rows));origin=db.execute('SELECT * FROM jobs WHERE id=?',(rows[0]['job'],)).fetchone()
        old=json.loads(db.execute('SELECT settings FROM projects WHERE id=?',(pid,)).fetchone()[0])
        prior=json.loads(origin['settings'])
        if rid:
            override=db.execute('SELECT settings FROM queue_overrides WHERE job=? AND record=?',(origin['id'],int(rid))).fetchone()
            if override:prior.update(json.loads(override[0]))
    settings={**data.get('settings',{}),**prior} if mode=='original' else {**prior,**data['settings'],**validate_overrides(data['overrides'])}
    provider=origin['provider'] if mode=='original' else data['provider']
    settings=validate_job_settings(store,pid,provider,settings,old)
    settings['_retry_origin']=origin['id']
    jid=store.create_job(pid,stage,provider,settings,retry=True,record_ids=ids,defer=True)
    schedule(store,pid)
    return {'job':jid,'records':len(ids),'queued':True}

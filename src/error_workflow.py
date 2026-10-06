"""Project-scoped retries and offline context estimates."""
import math,json
from core import SHARED,dump
from providers import Provider
from speaker_context import context_entry
from long_text import compact,estimate_tokens

def resume_settings(old,new):
    result=dict(old)
    for key,default,lo,hi in [('context',8192,1024,131072),('max_output',1200,100,100000),('max_calls',1000,1,1000000)]:
        value=int(new.get(key,old.get(key,default)))
        if not lo<=value<=hi:raise ValueError('Недопустимая настройка: '+key)
        result[key]=value
    for key in ['model','cloud_model','endpoint','source_language','target_language','mcp_session','mcp_model_hint']:
        if key in new:result[key]=new[key]
    return result

def requeue(db,job,ids):
    ids=list(set(map(int,ids)));changed=0;added=0
    for rid in ids:
        valid=db.execute("SELECT r.id FROM records r JOIN files f ON f.id=r.file WHERE r.id=? AND f.project=? AND r.manual=0 AND r.status<>'preserved' AND EXISTS(SELECT 1 FROM errors e JOIN jobs j ON j.id=e.job WHERE e.record=r.id AND e.project=f.project AND e.resolved=0 AND j.stage=?)",(rid,job['project'],job['stage'])).fetchone()
        if not valid:continue
        db.execute('DELETE FROM queue_overrides WHERE job=? AND record=?',(job['id'],rid))
        q=db.execute('SELECT state FROM queue WHERE job=? AND record=?',(job['id'],rid)).fetchone()
        if not q:db.execute('INSERT INTO queue(job,record) VALUES (?,?)',(job['id'],rid));added+=1
        elif q[0]!='pending':db.execute("UPDATE queue SET state='pending',finished_at=0 WHERE job=? AND record=?",(job['id'],rid));changed+=1
    db.execute('UPDATE jobs SET done=max(0,done-?),total=total+? WHERE id=?',(changed,added,job['id']))

def retry_errors(store,data,dispatch):
    pid=int(data['project']);stage=data['stage'];custom=data.get('overrides')
    if custom is not None:
        custom=validate_overrides(custom)
        if not data.get('record'):raise ValueError('Другие параметры доступны для одной строки')
    if stage not in {'translate','review','cloud'}:raise ValueError('Некорректный этап')
    with store.db() as db:
        query="SELECT DISTINCT e.record FROM errors e JOIN jobs j ON j.id=e.job JOIN records r ON r.id=e.record JOIN files f ON f.id=r.file WHERE e.project=? AND f.project=? AND j.stage=? AND e.resolved=0 AND r.manual=0 AND r.status<>'preserved'"
        args=[pid,pid,stage]
        if data.get('record'):query+=' AND e.record=?';args.append(int(data['record']))
        ids=[r[0] for r in db.execute(query,args)]
        active=db.execute("SELECT * FROM jobs WHERE project=? AND state IN ('running','queued','paused')",(pid,)).fetchone()
    if not ids:raise ValueError('Нет подходящих ошибок; ручные правки защищены')
    if active:
        if active['state']!='paused':raise ValueError('Сначала приостановите задачу и дождитесь завершения текущего запроса')
        if active['stage']!=stage or active['provider']!=data['provider']:raise ValueError('Для другого этапа или провайдера завершите либо отмените текущую задачу')
        if custom is not None:
            return dispatch(store,'control',{'id':active['id'],'mode':'resume','retry_records':ids,'record_overrides':custom,'preserve_project_settings':True})
        return dispatch(store,'control',{'id':active['id'],'mode':'resume','settings':data['settings'],'neighbors':data['settings'],'limits':data['settings'],'auto_foreign':data['settings'].get('auto_foreign',True),'retry_records':ids})
    return dispatch(store,'job',{**data,'retry':True,'record_ids':ids,'file':None,**({'record_overrides':custom,'persist_settings':False} if custom is not None else {})})

def budget(store,pid,rid,stage,settings):
    if stage not in {'translate','review','cloud'}:raise ValueError('Некорректный этап')
    settings={**settings,**resume_settings({},settings)}
    with store.db() as db:
        project=db.execute('SELECT * FROM projects WHERE id=?',(pid,)).fetchone()
        if not project or not db.execute('SELECT 1 FROM records r JOIN files f ON f.id=r.file WHERE r.id=? AND f.project=?',(rid,pid)).fetchone():raise ValueError('Строка не принадлежит проекту')
    provider=Provider(dict(project),'local',settings,diagnostic=True)
    entry=compact(context_entry(store,rid,settings=settings))
    modes=['translate'] if stage=='translate' else ['critic','verify']
    def footprint(item):
        values=[]
        for mode in modes:
            target=dict(item)
            if mode=='verify':target['original']=target.pop('russian','');target['candidate']=target['original']
            values.append(estimate_tokens(provider.prompt(mode))+estimate_tokens(dump({'items':[target]})))
        return max(values)+int(settings['max_output'])+384
    full=footprint(entry)
    minimum=dict(entry);minimum.update(context_before=[],context_after=[],confirmed_character_examples=[],english=entry['english'][:80],russian=entry.get('russian','')[:80])
    minimum['fragment_notice']='One fragment of the SAME record. Translate/check only english. Preserve voice and facts; do not import reference text or complete the following fragment.'
    minimum.update(fragment_before='',fragment_after='')
    min_tokens=footprint(minimum)
    return {'configured':settings['context'],'estimated_full':full,'estimated_minimum':min_tokens,'recommended':math.ceil(max(full,min_tokens)*1.1/1024)*1024,'fits_full':full<=settings['context'],'approximate':True,'output_reserve':settings['max_output']}


def validate_overrides(values):
    if not isinstance(values,dict) or set(values)!={'context','context_before','context_after'}:raise ValueError('Укажите контекст и число фраз до и после')
    from speaker_context import neighbor_settings
    parsed=resume_settings({},values)
    return {'context':parsed['context'],**neighbor_settings(values)}

def save_overrides(db,jid,ids,values):
    values=validate_overrides(values)
    if len(ids)!=1:raise ValueError('Другие параметры доступны для одной строки')
    rid=int(ids[0])
    if not db.execute('SELECT 1 FROM queue WHERE job=? AND record=?',(jid,rid)).fetchone():raise ValueError('Строка не в очереди этой задачи')
    db.execute('INSERT OR REPLACE INTO queue_overrides VALUES (?,?,?)',(jid,rid,dump(values)))

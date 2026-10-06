import json, os, threading, time
from pathlib import Path
from core import Store, now, dump
from providers import Provider, ServiceUnavailable, process_record
from speaker_context import context_entry
from long_text import JobStopped

def run(store, jid, provider_factory=Provider):
    with store.db() as db:
        job=db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
        if not job or job['state'] not in {'queued','paused'}: raise ValueError('Задача уже запущена или завершена')
        project=dict(db.execute('SELECT * FROM projects WHERE id=?',(job['project'],)).fetchone())
        claimed=db.execute("UPDATE jobs SET state='running',worker_active=1,pid=?,heartbeat=?,error='' WHERE id=? AND state IN ('queued','paused')",(os.getpid(),time.time(),jid))
        if not claimed.rowcount: raise ValueError('Задача уже выполняется в другом окне')
    settings=json.loads(job['settings']);settings['_studio_db']=str(store.path);settings['_studio_job']=jid;settings['_studio_project']=job['project']
    logdir=Path(project['root'])/'translation_tools'/'studio'; logdir.mkdir(parents=True,exist_ok=True)
    log=(logdir/f'job-{jid}.log').open('a',encoding='utf-8')
    stop=threading.Event()
    def heartbeat():
        while not stop.wait(5):
            with store.db() as db: db.execute('UPDATE jobs SET heartbeat=? WHERE id=?',(time.time(),jid))
    thread=threading.Thread(target=heartbeat,daemon=True); thread.start()
    def emit(s): print(s,flush=True); log.write(now()+' '+s+'\n'); log.flush()
    emit(f'JOB {jid} — {job["stage"]} — {project["name"]}')
    started=time.monotonic(); processed=0
    try:
        provider=None;effective_settings=None
        while True:
            with store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                state=db.execute('SELECT state FROM jobs WHERE id=?',(jid,)).fetchone()[0]
                if state!='running': emit('INCOMPLETE: '+state); return
                q=db.execute("SELECT record FROM queue WHERE job=? AND state='pending' ORDER BY priority,record LIMIT 1",(jid,)).fetchone()
                if not q:
                    failed=db.execute("SELECT count(*) FROM queue WHERE job=? AND state IN ('error','protected')",(jid,)).fetchone()[0]
                    db.execute('UPDATE jobs SET state=?,error=? WHERE id=?',('incomplete' if failed else 'done',f'{failed} строк требуют внимания' if failed else '',jid))
                    emit(('INCOMPLETE' if failed else 'DONE')+f' — {failed} строк требуют внимания')
                    return
                limit_lines=int(settings.get('run_lines',0));limit_minutes=float(settings.get('run_minutes',0))
                if (limit_lines and processed>=limit_lines) or (limit_minutes and time.monotonic()-started>=limit_minutes*60):
                    reason='Лимит запуска достигнут; очередь сохранена'
                    db.execute("UPDATE jobs SET state='paused',error=? WHERE id=?",(reason,jid));emit('PAUSED: '+reason);return
                rid=q[0]; db.execute("UPDATE jobs SET current=?,fragment_done=0,fragment_total=0,fragment_preview='' WHERE id=?",(rid,jid))
            from foreign_language import check_record
            r=check_record(store,rid,settings,worker_job=jid)
            if r['preserve_kind']:
                outcome='done';emit(f'LITERAL #{rid}: '+r['preserve_reason'])
            elif r['manual'] and not (job['stage']=='review' and settings.get('marked_review') and settings.get('review_manual')):
                outcome='protected'; emit(f'PROTECTED #{rid}: ручная правка сохранена')
            else:
                if settings.get('marked_review') and r['flag']!=settings['marked_review']:
                    with store.db() as db:db.execute("UPDATE queue SET state='protected' WHERE job=? AND record=?",(jid,rid));db.execute('UPDATE jobs SET done=done+1 WHERE id=?',(jid,))
                    emit(f'SKIPPED #{rid}: пометка снята или изменена');continue
                with store.db() as db:override=db.execute('SELECT settings FROM queue_overrides WHERE job=? AND record=?',(jid,rid)).fetchone()
                record_settings={**settings,**(json.loads(override[0]) if override else {})}
                if override:emit(f"PARAMETERS #{rid}: context={record_settings['context']}, before={record_settings['context_before']}, after={record_settings['context_after']}")
                if record_settings!=effective_settings:
                    calls=getattr(provider,'calls',0);provider=provider_factory(project,job['provider'],record_settings);provider.calls=calls;provider.progress=emit;effective_settings=record_settings
                entry=context_entry(store,rid,settings=record_settings)
                if r['flag']:entry['human_review_flag']=r['flag']
                emit(f'SOURCE #{rid}: {r["source"]}')
                try:
                    last=None
                    for attempt in range(2):
                        try: translated,reason=process_record(provider,job['stage'],entry); last=None; break
                        except ServiceUnavailable: raise
                        except (ValueError,KeyError,TypeError) as e: last=e; entry['validation_feedback']=str(e)[:500]
                    if last: raise last
                    with store.db() as db:
                        if db.execute('SELECT state FROM jobs WHERE id=?',(jid,)).fetchone()[0]!='running': emit('INCOMPLETE: остановлено'); return
                    status={'translate':'translated','review':'edited','cloud':'verified'}[job['stage']]
                    store.update(rid,r['revision'],translated,status,job['provider']+':'+(getattr(provider,'last_model','') or settings.get('cloud_model' if job['provider']=='cloud' else 'model','')),reason)
                    emit('TRANSLATION: '+translated); outcome='done'
                except JobStopped as e:
                    emit('INCOMPLETE: '+str(e));return
                except ServiceUnavailable as e:
                    with store.db() as db:
                        if db.execute('SELECT state FROM jobs WHERE id=?',(jid,)).fetchone()[0]!='running':emit('INCOMPLETE: остановлено');return
                    store.error(jid,rid,str(e))
                    with store.db() as db: db.execute("UPDATE jobs SET state='paused',error=? WHERE id=? AND state='running'",(str(e),jid))
                    emit('INCOMPLETE: '+str(e)); return
                except Exception as e:
                    with store.db() as db:
                        if db.execute('SELECT state FROM jobs WHERE id=?',(jid,)).fetchone()[0]!='running':emit('INCOMPLETE: остановлено');return
                    store.error(jid,rid,str(e)); outcome='error'; emit(f'ERROR #{rid}: {e} — продолжаю')
            with store.db() as db:
                db.execute('UPDATE queue SET state=? WHERE job=? AND record=?',(outcome,jid,rid))
                processed+=1; rate=processed*60/max(.01,time.monotonic()-started)
                db.execute('UPDATE jobs SET done=done+1,rate=? WHERE id=?',(rate,jid))
                progress=db.execute('SELECT done,total FROM jobs WHERE id=?',(jid,)).fetchone()
            emit(f'PROGRESS {progress[0]}/{progress[1]} | {rate:.1f} lines/min')
    except Exception as e:
        with store.db() as db: db.execute("UPDATE jobs SET state='paused',error=? WHERE id=? AND state='running'",(str(e)[:1000],jid))
        emit('INCOMPLETE: '+str(e))
    finally:
        stop.set(); thread.join(timeout=10); log.close()
        with store.db() as db:db.execute('UPDATE jobs SET worker_active=0 WHERE id=?',(jid,))
        from job_chain import schedule
        schedule(store,job['project'])

import argparse, base64, json, os, secrets, subprocess, sys, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from core import Store, ROOT, APP_HOME, SHARED, dump
from providers import models, secret
from speaker_context import neighbor_settings

DEFAULT_DB=APP_HOME/'data'/'studio.sqlite3'

def run_limits(settings):
    result={}
    for key,maximum in [('run_lines',1000000),('run_minutes',10080)]:
        value=float(settings.get(key,0) or 0)
        if not 0<=value<=maximum or not __import__('math').isfinite(value):raise ValueError('Недопустимый лимит запуска')
        if key=='run_lines' and value!=int(value):raise ValueError('Число строк должно быть целым')
        result[key]=int(value) if key=='run_lines' else value
    return result

def record_context(store,rid,radius=10):
    from speaker_context import context_entry
    r=store.record(rid);radius=max(1,min(30,int(radius)))
    with store.db() as db:
        rows=[dict(x) for x in db.execute('SELECT r.*,f.path,f.kind FROM records r JOIN files f ON f.id=r.file WHERE r.file=? AND r.scene=? AND r.position BETWEEN ? AND ? ORDER BY r.position',(r['file'],r['scene'],r['position']-radius,r['position']+radius))]
        before_count=db.execute('SELECT count(*) FROM records WHERE file=? AND scene=? AND position<?',(r['file'],r['scene'],r['position'])).fetchone()[0]
        after_count=db.execute('SELECT count(*) FROM records WHERE file=? AND scene=? AND position>?',(r['file'],r['scene'],r['position'])).fetchone()[0]
    locator=json.loads(r['locator']);location={'file':r['path'],'scene':r['scene'],'speaker':r['speaker'],'line':locator.get('line',-1)+1 if 'line' in locator else None,'json_path':locator.get('path'),'row':locator.get('row',-1)+1 if 'row' in locator else None}
    # A Ren'Py translation block often points to the actual game script.
    if 'line' in locator:
        with store.db() as db:f=db.execute('SELECT original FROM files WHERE id=?',(r['file'],)).fetchone()
        previous=f[0].splitlines()[:locator['line']+1]
        for line in reversed(previous):
            match=__import__('re').match(r'\s*# (game/.+\.rpy):(\d+)',line)
            if match:location['source_file']=match[1];location['source_line']=int(match[2]);break
            if line.strip().startswith('translate '):break
    return {'target':rid,'location':location,'rows':rows,'metadata':context_entry(store,rid),'before_count':before_count,'after_count':after_count,'order':'file_position'}

def launch(store,jid):
    from contextlib import nullcontext
    with getattr(store,'launch_lock',nullcontext()):
        if getattr(store,'closing',False):raise ValueError('Приложение закрывается; очередь сохранена')
        return _launch(store,jid)

def _launch(store, jid):
    if getattr(sys,'frozen',False):
        executable=APP_HOME/'LocalizationWorker.exe';pointer=APP_HOME/'worker_version.txt'
        if pointer.is_file():
            name=pointer.read_text(encoding='utf-8-sig').strip()
            if Path(name).name==name and name.startswith('LocalizationWorker_') and name.endswith('.exe') and (APP_HOME/name).is_file():executable=APP_HOME/name
        args=[str(executable)]
    else: args=[sys.executable,str(ROOT/'app.py')]
    args+=['--worker',str(jid),'--db',str(store.path)]
    folder=store.path.parent/'worker-logs'; folder.mkdir(parents=True,exist_ok=True)
    with (folder/f'job-{jid}-process.log').open('ab') as output:
        process=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=output,stderr=output,
                         creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))

    with store.db() as db:db.execute('UPDATE jobs SET pid=? WHERE id=?',(process.pid,jid))

def job_log(store, jid):
    with store.db() as db:
        job=db.execute('SELECT j.id,p.root FROM jobs j JOIN projects p ON p.id=j.project WHERE j.id=?',(jid,)).fetchone()
    if not job: raise ValueError('Задача не найдена')
    path=Path(job['root'])/'translation_tools'/'studio'/f'job-{jid}.log'
    if not path.exists(): path=store.path.parent/'worker-logs'/f'job-{jid}-process.log'
    if not path.exists(): return {'text':''}
    with path.open('rb') as stream:
        size=stream.seek(0,2); start=max(0,size-32768); stream.seek(start)
        if start: stream.readline()
        lines=stream.read().decode('utf-8',errors='replace').splitlines()
    return {'text':'\n'.join(lines[-80:])}

def state(store, project,editing=False):
    store.archive_completed(project if editing else 0)
    with store.db() as db:
        projects=[dict(r) for r in db.execute("SELECT p.*,EXISTS(SELECT 1 FROM jobs j WHERE j.project=p.id AND j.state IN ('running','queued')) active FROM projects p WHERE p.hidden=0 AND p.deleting=0 ORDER BY p.id")]
        if project not in {p['id'] for p in projects}:project=projects[0]['id'] if projects else 0
        if not project: return {'projects':projects,'files':[],'counts':{},'jobs':[],'project':None}
        files=[dict(r) for r in db.execute('SELECT f.id,f.path,f.kind,count(r.id) total,sum(r.status=\'translated\') translated,sum(r.status=\'edited\') edited,sum(r.status=\'verified\') verified FROM files f LEFT JOIN records r ON r.file=f.id WHERE f.project=? GROUP BY f.id ORDER BY f.path',(project,))]
        counts=dict(db.execute('SELECT r.status,count(*) FROM records r JOIN files f ON f.id=r.file WHERE f.project=? GROUP BY r.status',(project,)).fetchall())
        jobs=[dict(r) for r in db.execute('SELECT * FROM jobs WHERE project=? ORDER BY id DESC LIMIT 10',(project,))]
        errors=db.execute('SELECT count(*) FROM errors WHERE project=? AND resolved=0',(project,)).fetchone()[0]
        pending=db.execute("SELECT count(*) FROM queue q JOIN jobs j ON j.id=q.job WHERE j.project=? AND j.state IN ('queued','running','paused') AND q.state='pending' AND NOT EXISTS(SELECT 1 FROM preserved_records p WHERE p.record=q.record)",(project,)).fetchone()[0]
        flags=dict(db.execute('SELECT m.kind,count(*) FROM record_marks m JOIN records r ON r.id=m.record JOIN files f ON f.id=r.file WHERE f.project=? GROUP BY m.kind',(project,)).fetchall())
    return {'projects':projects,'project':project,'files':files,'counts':counts,'jobs':jobs,'errors':errors,'pending':pending,'flags':flags}

def queue_page(store,project):
    from process_view import queue_page as upcoming
    return upcoming(store,project)

def records(store, file, offset=0, search='', status=''):
    q="FROM records r LEFT JOIN record_marks m ON m.record=r.id WHERE r.file=?"; args=[file]
    if search: q+=' AND (source LIKE ? OR text LIKE ?)'; args += ['%'+search+'%']*2
    if status: q+=' AND status=?'; args.append(status)
    with store.db() as db:
        total=db.execute('SELECT count(*) '+q,args).fetchone()[0]
        rows=[dict(r) for r in db.execute("SELECT r.*,coalesce(m.kind,'') flag,m.at flagged_at,m.was_manual flag_previous_manual,coalesce(json_extract((SELECT result FROM language_checks WHERE record=r.id),'$.reason'),'') language_note,coalesce((SELECT reason FROM preserved_records WHERE record=r.id),'') preserve_reason,coalesce((SELECT kind FROM preserved_records WHERE record=r.id),'') preserve_kind "+q+' ORDER BY position LIMIT 50 OFFSET ?',args+[max(0,int(offset))])]
    return {'rows':rows,'total':total,'offset':offset}

def validate_job_settings(store,pid,provider,settings,old):
    if provider not in {'local','cloud','mcp'}:raise ValueError('Некорректный провайдер')
    if not isinstance(settings.get('auto_foreign',True),bool):raise ValueError('Некорректная настройка определения языка')
    if 'speaker_profiles' in old:settings['speaker_profiles']=old['speaker_profiles']
    if len(str(settings.get('review_instruction','')))>4000:raise ValueError('Инструкция редактору слишком длинная')
    settings.update(run_limits(settings))
    for key,default,minimum,maximum in [('context',8192,1024,131072),('max_calls',1000,1,1000000),('max_output',1200,100,100000)]:
        value=int(settings.get(key,default))
        if not minimum<=value<=maximum: raise ValueError('Недопустимая настройка: '+key)
        settings[key]=value
    if (provider=='local' and not settings.get('model')) or not settings.get('source_language') or not settings.get('target_language'): raise ValueError('Выберите модель и языки')
    if provider=='mcp':
        from mcp_server import connections
        if not any(c['session']==settings.get('mcp_session') and c['sampling'] for c in connections(store)):raise ValueError('Выбранный MCP-клиент не подключён или не поддерживает генерацию (sampling)')
    if provider=='cloud':
        if not secret(pid): raise ValueError('Сначала сохраните API-ключ')
        if not settings.get('endpoint') or not settings.get('cloud_model'): raise ValueError('Задайте API URL и облачную модель')
    return settings

def api(store, action, data):
    if action=='translate-preserved':
        from preserved_workflow import active_job,submit
        from contextlib import nullcontext
        with getattr(store,'launch_lock',nullcontext()):
            if getattr(store,'closing',False):raise ValueError('Приложение закрывается')
            payload=dict(data);pid=int(data['project'])
            if data['mode']!='manual' and not active_job(store,pid):
                with store.db() as db:
                    row=db.execute('SELECT settings FROM projects WHERE id=?',(pid,)).fetchone()
                    if not row:raise ValueError('Проект не найден')
                payload['settings']=validate_job_settings(store,pid,data['provider'],dict(data['settings']),json.loads(row[0]))
                payload['settings'].update(neighbor_settings(payload['settings']))
                payload['_new_job_validated']=True
            result=submit(store,payload)
            if result.get('created'):
                try:launch(store,result['job'])
                except Exception:
                    with store.db() as db:db.execute("UPDATE jobs SET state='paused',error='Не удалось запустить обработчик' WHERE id=?",(result['job'],))
                    raise ValueError('Не удалось запустить обработчик; строка сохранена в очереди на паузе')
            return result

    if action=='preserved-check':
        from preservation import symbolic
        from foreign_language import detect
        pid=int(data['project']);rid=int(data['record'])
        with store.db() as db:
            if not db.execute('SELECT 1 FROM records r JOIN files f ON f.id=r.file WHERE r.id=? AND f.project=? AND r.status=\'preserved\'',(rid,pid)).fetchone():raise ValueError('Строка не принадлежит этой папке проекта')
        row=store.record(rid)
        if symbolic(row['source']):explanation='Строка состоит из символов, паузы или служебных вставок. Оригинал можно сохранить без перевода; ручная проверка обычно не требуется.'
        else:
            result=detect(row['source'],data.get('source_language','English'))
            explanation=result.get('reason') or 'Автоматическая проверка не подтверждает другой язык с достаточной уверенностью. Решение автора или человека может быть верным; посмотрите контекст.'
        return {'explanation':explanation,'inference':False,'changed':False}
    if action=='error-budget':
        from error_workflow import budget
        return budget(store,int(data['project']),int(data['record']),data['stage'],data['settings'])
    if action=='retry-errors':
        from error_workflow import retry_errors
        return retry_errors(store,data,api)
    if action=='process-settings':
        from process_view import save_preferences
        return save_preferences(store,int(data['project']),data['preferences'])
    if action=='language-preview':
        from foreign_language import detect
        if not isinstance(data.get('text'),str) or len(data['text'])>10000:raise ValueError('Для предпросмотра нужно до 10000 символов')
        return detect(data['text'],data.get('source_language','English'))
    if action=='preserve':return store.preserve(int(data['id']),int(data['revision']),data['kind'],data['reason'])
    if action=='restore-preserved':return store.restore_preserved(int(data['id']),int(data['revision']))
    if action=='clear-marks':return store.clear_marks(int(data['project']),data['kind'])
    if action=='mark':return store.mark(int(data['id']),int(data['revision']),data['kind'])
    if action=='complete-project':return store.complete_project(int(data['project']))
    if action=='delete-project':
        from project_lifecycle import delete_project
        return delete_project(store,int(data['project']))
    if action=='hide-project':return store.hide_project(int(data['project']),True)
    if action=='restore-project':return store.hide_project(int(data['project']),False)
    if action=='project':
        from game_detection import inspect_project,import_detected
        p=store.project(data['root']);report=inspect_project(store,p['id']);paths=report['auto_import']
        if data.get('scan') and report['engine']['id'] not in {'multiple'}:
            paths=paths+[item['path'] for item in report['candidates'] if '/' not in item['path'] and item['supported'] and item['role'] in {'corpus','text'}]
        result=import_detected(store,p['id'],paths)
        return {'project':p,'import':result,'analysis':{'engine':report['engine'],'partial':report['partial']}}
    if action=='analyze-project':
        from game_detection import inspect_project
        return inspect_project(store,int(data['project']))
    if action=='import-detected':
        from game_detection import import_detected
        return import_detected(store,int(data['project']),data['paths'])
    if action=='import': return store.import_files(int(data['project']),data['paths'])
    if action=='upload':
        with store.db() as db: p=db.execute('SELECT root FROM projects WHERE id=?',(int(data['project']),)).fetchone()
        folder=Path(p[0])/'translation_tools'/'studio'/'imports'; folder.mkdir(parents=True,exist_ok=True)
        paths=[]
        for f in data['files']:
            name=Path(f['name']).name
            if Path(name).suffix.lower() not in {'.txt','.json','.jsonl','.csv','.rpy'}: raise ValueError('Неподдерживаемый файл')
            dest=folder/name; content=f['text']
            if dest.exists() and dest.read_text(encoding='utf-8')!=content: dest=folder/(str(time.time_ns())+'-'+name)
            if not dest.exists(): dest.write_text(content,encoding='utf-8')
            paths.append(str(dest))
        return store.import_files(int(data['project']),paths)
    if action=='save': return store.update(int(data['id']),int(data['revision']),data['text'],'verified' if data.get('verified') else 'translated','human','Ручная проверка' if data.get('verified') else 'Ручная правка',manual=True)
    if action=='undo': return store.undo(int(data['id']),int(data['revision']))
    if action=='speaker-profiles':
        profiles=data['profiles']
        if not isinstance(profiles,dict) or len(profiles)>500:raise ValueError('Профили должны быть объектом с идентификаторами говорящих')
        for key,p in profiles.items():
            if not key.strip() or not isinstance(p,dict):raise ValueError('Укажите идентификатор и поля персонажа')
            if any(not isinstance(value,str) for value in p.values()):raise ValueError('Поля профиля должны содержать текст')
        if len(dump(profiles))>100000:raise ValueError('Профили слишком большие')
        with store.db() as db:
            row=db.execute('SELECT settings FROM projects WHERE id=?',(int(data['project']),)).fetchone()
            if not row:raise ValueError('Проект не найден')
            settings=json.loads(row[0]);settings['speaker_profiles']=profiles
            db.execute('UPDATE projects SET settings=? WHERE id=?',(dump(settings),int(data['project'])))
        return {'ok':True}
    if action=='unlock':
        with store.db() as db:
            db.execute('UPDATE records SET manual=0,revision=revision+1 WHERE id=?',(int(data['id']),))
        return {'ok':True}
    if action=='proposal':
        with store.db() as db: p=dict(db.execute('SELECT * FROM proposals WHERE id=? AND state=\'pending\'',(int(data['id']),)).fetchone())
        if data.get('accept'):
            store.update(p['record'],p['revision'],p['text'],'verified' if data.get('verified') else 'translated','human / MCP:'+p['reviewer'],p['reason'],manual=True)
        with store.db() as db: db.execute('UPDATE proposals SET state=? WHERE id=?',('accepted' if data.get('accept') else 'rejected',p['id']))
        return {'ok':True}
    if action=='settings':
        pid=int(data['project']); settings={**data['settings'],**neighbor_settings(data['settings'])}; key=data.get('key')
        if not isinstance(settings.get('auto_foreign',True),bool):raise ValueError('Некорректная настройка определения языка')
        with store.db() as db:
            old=json.loads(db.execute('SELECT settings FROM projects WHERE id=?',(pid,)).fetchone()[0])
        if 'speaker_profiles' in old:settings['speaker_profiles']=old['speaker_profiles']
        if key: secret(pid,key)
        with store.db() as db:db.execute('UPDATE projects SET settings=? WHERE id=?',(dump(settings),pid))
        return {'ok':True,'key_saved':bool(secret(pid))}
    if action=='job':
        pid=int(data['project']); settings=data['settings']
        with store.db() as db:
            old=json.loads(db.execute('SELECT settings FROM projects WHERE id=?',(pid,)).fetchone()[0])
        settings=validate_job_settings(store,pid,data['provider'],dict(settings),old)
        jid=store.create_job(pid,data['stage'],data['provider'],settings,data.get('file'),bool(data.get('retry')),data.get('mark_kind'),bool(data.get('allow_manual')),data.get('record_ids'))
        with store.db() as db:
            if data.get('record_overrides') is not None:
                from error_workflow import save_overrides
                save_overrides(db,jid,data.get('record_ids',[]),data['record_overrides'])
            if data.get('persist_settings',True):db.execute('UPDATE projects SET settings=? WHERE id=?',(dump(settings),pid))
        try: launch(store,jid)
        except Exception:
            with store.db() as db: db.execute("UPDATE jobs SET state='paused',error='Не удалось запустить обработчик' WHERE id=?",(jid,))
            raise ValueError('Не удалось запустить обработчик; задача сохранена')
        return {'job':jid}
    if action=='control':
        jid=int(data['id']); mode=data['mode']
        with store.db() as db:
            job=db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
            if not job: raise ValueError('Задача не найдена')
            if mode=='resume':
                if job['state']!='paused': raise ValueError('Продолжить можно задачу на паузе')
                if time.time()-job['heartbeat']<10: raise ValueError('Дождитесь завершения текущего запроса перед продолжением')
                settings=json.loads(job['settings'])
                from error_workflow import resume_settings,requeue
                settings=resume_settings(settings,data.get('settings',{}))
                if data.get('retry_records'):requeue(db,job,data['retry_records'])
                if data.get('record_overrides') is not None:
                    from error_workflow import save_overrides
                    save_overrides(db,jid,data.get('retry_records',[]),data['record_overrides'])
                settings.update(run_limits(data.get('limits',settings)))
                settings.update(neighbor_settings(data.get('neighbors',settings)))
                if 'auto_foreign' in data:
                    if not isinstance(data['auto_foreign'],bool):raise ValueError('Некорректная настройка определения языка')
                    settings['auto_foreign']=data['auto_foreign']
                db.execute('UPDATE jobs SET settings=? WHERE id=?',(dump(settings),jid))
                if not data.get('preserve_project_settings'):
                    saved=json.loads(db.execute('SELECT settings FROM projects WHERE id=?',(job['project'],)).fetchone()[0]);saved.update({k:v for k,v in settings.items() if not k.startswith('_')});db.execute('UPDATE projects SET settings=? WHERE id=?',(dump(saved),job['project']))
                db.execute("UPDATE jobs SET state='queued' WHERE id=?",(jid,))
            elif mode in {'pause','cancel'}: db.execute('UPDATE jobs SET state=? WHERE id=?',('paused' if mode=='pause' else 'cancelled',jid))
            else: raise ValueError('Некорректная команда')
        if mode=='resume':
            store.classify_preserved(job['project'])
            launch(store,jid)
        return {'ok':True}
    if action=='export': return {'path':store.export(int(data['file']),data['destination'])}
    raise ValueError('Неизвестная команда')

def serve(store,port,open_browser,ready=None):
    token_path=store.path.with_name('server.token')
    if token_path.exists(): token=token_path.read_text(encoding='ascii').strip()
    else:
        token=secrets.token_urlsafe(32); token_path.write_text(token,encoding='ascii')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def respond(self,data,status=200,kind='application/json; charset=utf-8'):
            body=data.encode() if isinstance(data,str) else dump(data).encode()
            self.send_response(status); self.send_header('Content-Type',kind); self.send_header('Content-Length',str(len(body))); self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff'); self.send_header('Referrer-Policy','no-referrer'); self.end_headers(); self.wfile.write(body)
        def guard(self):
            if self.headers.get('Host')!=f'127.0.0.1:{port}': raise ValueError('Недопустимый адрес сервера')
            origin=self.headers.get('Origin')
            if origin and origin!=f'http://127.0.0.1:{port}': raise ValueError('Недопустимый источник запроса')
        def do_GET(self):
            try:
                self.guard(); url=urlparse(self.path); q={k:v[0] for k,v in parse_qs(url.query).items()}
                if url.path=='/':
                    html=(ROOT/'index.html').read_text(encoding='utf-8').replace('__TOKEN__',token)
                    return self.respond(html,kind='text/html; charset=utf-8')
                if url.path=='/ui_extensions.js': return self.respond((ROOT/'ui_extensions.js').read_text(encoding='utf-8'),kind='text/javascript; charset=utf-8')
                if url.path=='/project_ui.js': return self.respond((ROOT/'project_ui.js').read_text(encoding='utf-8'),kind='text/javascript; charset=utf-8')
                if url.path=='/workflow_ui.js': return self.respond((ROOT/'workflow_ui.js').read_text(encoding='utf-8'),kind='text/javascript; charset=utf-8')
                if url.path=='/detection_ui.js': return self.respond((ROOT/'detection_ui.js').read_text(encoding='utf-8'),kind='text/javascript; charset=utf-8')
                if url.path=='/process_ui.js': return self.respond((ROOT/'process_ui.js').read_text(encoding='utf-8'),kind='text/javascript; charset=utf-8')
                if self.headers.get('X-Studio-Token')!=token: return self.respond({'error':'Нужен токен приложения'},403)
                if url.path=='/api/state': data=state(store,int(q.get('project',0)),q.get('editing')=='1')
                elif url.path=='/api/completed-projects':data=store.completed_projects()
                elif url.path=='/api/hidden-projects':data=store.hidden_projects()
                elif url.path=='/api/records': data=records(store,int(q['file']),int(q.get('offset',0)),q.get('search',''),q.get('status',''))
                elif url.path=='/api/queue': data=queue_page(store,int(q['project']))
                elif url.path=='/api/process-history':
                    from process_view import history_page
                    data=history_page(store,int(q['project']),int(q.get('offset',0)))
                elif url.path=='/api/process-settings':
                    from process_view import preferences
                    data=preferences(store,int(q['project']))
                elif url.path=='/api/preserved':
                    from preservation import folder
                    data=folder(store,int(q['project']),int(q.get('offset',0)))
                elif url.path=='/api/marked':
                    from process_view import marks_page
                    data=marks_page(store,int(q['project']),q['kind'],int(q.get('offset',0)))
                elif url.path=='/api/connections':
                    from mcp_server import connections
                    data={'mcp':connections(store),'application':f'127.0.0.1:{port}'}
                elif url.path=='/api/models': data=models()
                elif url.path=='/api/history':
                    with store.db() as db: data=[dict(r) for r in db.execute('SELECT * FROM history WHERE record=? ORDER BY id DESC LIMIT 50',(int(q['id']),))]
                elif url.path=='/api/proposals':
                    with store.db() as db: data=[dict(r) for r in db.execute("SELECT p.*,r.source,r.text current FROM proposals p JOIN records r ON r.id=p.record JOIN files f ON f.id=r.file WHERE f.project=? AND p.state='pending' ORDER BY p.id DESC LIMIT 100",(int(q['project']),))]
                elif url.path=='/api/current': data=store.record(int(q['id']))
                elif url.path=='/api/context': data=record_context(store,int(q['id']),int(q.get('radius',10)))
                elif url.path=='/api/project-analysis':
                    from game_detection import saved_report
                    data={'report':saved_report(store,int(q['project']))}
                elif url.path=='/api/job-log': data=job_log(store,int(q['id']))
                elif url.path=='/api/errors':
                    with store.db() as db: data=[dict(r) for r in db.execute('SELECT e.*,r.source,r.text,j.stage FROM errors e LEFT JOIN records r ON r.id=e.record LEFT JOIN jobs j ON j.id=e.job WHERE e.project=? AND e.resolved=0 ORDER BY e.id DESC LIMIT 100 OFFSET ?',(int(q['project']),int(q.get('offset',0))))]
                else: return self.respond({'error':'Не найдено'},404)
                self.respond(data)
            except Exception as e: self.respond({'error':str(e)},400)
        def do_POST(self):
            try:
                self.guard()
                if self.headers.get('X-Studio-Token')!=token: return self.respond({'error':'Нужен токен приложения'},403)
                length=int(self.headers.get('Content-Length',0))
                if length>20_000_000: raise ValueError('Размер запроса больше 20 МБ')
                data=json.loads(self.rfile.read(length)); self.respond(api(store,self.path.removeprefix('/api/'),data))
            except Exception as e: self.respond({'error':str(e)},400)
    # Workers survive closing the app; recover abandoned queue on restart.
    with store.db() as db:
        db.execute("UPDATE jobs SET state='paused',error='Обработчик завершился; продолжите задачу' WHERE state IN ('running','queued') AND heartbeat<?",(time.time()-60,))
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    port=server.server_port
    url=f'http://127.0.0.1:{port}/'; print('Localization Studio: '+url,flush=True)
    if open_browser: webbrowser.open(url)
    if ready: ready(server)
    server.serve_forever()

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--db',default=str(DEFAULT_DB)); parser.add_argument('--port',type=int,default=8765); parser.add_argument('--open',action='store_true'); parser.add_argument('--worker',type=int); parser.add_argument('--import-project'); args=parser.parse_args()
    store=Store(args.db)
    if args.worker:
        from worker import run
        run(store,args.worker)
    elif args.import_project: print(dump(api(store,'project',{'root':args.import_project,'scan':True})))
    else: serve(store,args.port,args.open)

"""Persistent, versioned localization workspace. Only stdlib dependencies."""
from __future__ import annotations
import ast, collections, csv, hashlib, json, re, shutil, sqlite3, sys, time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_HOME = Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else ROOT.parent
SHARED = ROOT / 'translation_tools'
sys.path.insert(0, str(SHARED))
from local_editor import refusal_error
from localize import source_scene_map

def now(): return time.strftime('%Y-%m-%d %H:%M:%S')
def dump(x): return json.dumps(x, ensure_ascii=False)
TOKEN = re.compile(r'\[[^\[\]\n]+\]|\{[^{}\n]+\}|%\([^)]+\)[#0+\-\d.]*[sdif]|%(?:[#0+\-\d.]*[sdif])(?=\W|$)|\\[nrt]')

def validate(source, text):
    if not isinstance(text, str) or not text.strip(): raise ValueError('Пустой перевод')
    if collections.Counter(TOKEN.findall(source)) != collections.Counter(TOKEN.findall(text)):
        raise ValueError('Не совпадают теги или переменные')
    if any(source.count(c)!=text.count(c) for c in '\n\r\t'): raise ValueError('Не совпадают переносы строк или табуляция')
    error = refusal_error(source, text)
    if error: raise ValueError(error)

def literal(s):
    # Ren'Py permits unknown escape sequences (e.g. \%). Preserve them.
    try: return ast.literal_eval(s)
    except (ValueError, SyntaxError): raise ValueError('Не удалось прочитать строковый литерал')

SPAN = re.compile(r'"(?:\\.|[^"\\])*"')
def extract(path, text=None):
    if text is None: text = path.read_text(encoding='utf-8-sig')
    suffix = path.suffix.lower()
    if suffix == '.rpy':
        lines = text.splitlines(keepends=True)
        records = []; source = None; speaker = ''; scene = 'strings'; old = None
        game=next((p for p in path.parents if p.name=='game'),None)
        native_scenes=source_scene_map(path) if '/tl/' not in path.as_posix() else {}
        for i, line in enumerate(lines):
            s = line.strip(); match = SPAN.search(line)
            if s.startswith('translate '): scene = s.rstrip(':'); source = old = None
            if s.startswith('label '): scene = s.rstrip(':')
            reference=re.match(r'# (game/.+\.rpy):(\d+)',s)
            if reference and game:
                original=(game.parent/reference[1]).resolve()
                if original.is_relative_to(game.resolve()):
                    key=source_scene_map(original).get(int(reference[2]))
                    if key: scene=dump(key)
            if native_scenes: scene=dump(native_scenes.get(i+1,('start',())))
            if s.startswith('#') and match:
                prefix = s[1:].strip().split('"', 1)[0].strip()
                if prefix.split(' ', 1)[0] in {'voice','sound','music'}: source = None; continue
                source = literal(match.group()); speaker = prefix
                continue
            if s.startswith('old ') and match: old = literal(match.group()); continue
            if s.startswith('new ') and match and old is not None:
                records.append(dict(source=old, text=literal(match.group()), speaker='interface', scene=scene,
                    locator={'line':i,'start':match.start(),'end':match.end(),'line_hash':hashlib.sha256(line.encode()).hexdigest()}))
                old = None; continue
            if not match or s.startswith(('#','translate ','old ','new ')): continue
            prefix = s.split('"',1)[0].strip()
            if source is not None and not s.startswith('$'):
                records.append(dict(source=source, text=literal(match.group()), speaker=speaker, scene=scene,
                    locator={'line':i,'start':match.start(),'end':match.end(),'line_hash':hashlib.sha256(line.encode()).hexdigest()}))
                source = None
            elif not '/tl/' in path.as_posix() and (prefix == '' or re.fullmatch(r'[\w]+(?:\s+[\w]+)*',prefix)) and prefix.split(' ',1)[0] not in {'voice','sound','music','image','define','default','play','queue','stop','show','scene','hide','menu','jump','call','return'}:
                records.append(dict(source=literal(match.group()),text='',speaker=prefix,scene=scene,
                    locator={'line':i,'start':match.start(),'end':match.end(),'line_hash':hashlib.sha256(line.encode()).hexdigest()}))
        return records, text, 'renpy'
    if suffix in {'.json','.jsonl'}:
        data = json.loads(text) if suffix == '.json' else [json.loads(l) for l in text.splitlines() if l.strip()]
        records=[]
        def walk(x, loc):
            if isinstance(x,dict) and isinstance(x.get('source'),str):
                records.append(dict(source=x['source'],text=x.get('translation',''),speaker=str(x.get('speaker') or x.get('speaker_id') or x.get('character') or x.get('actor') or ''),scene=str(x.get('scene','')),locator={'path':loc,'field':'translation','utterance_kind':x.get('utterance_kind') or x.get('line_type'),'preserve_original':x.get('preserve_original') is True,'preserve_reason':x.get('preserve_reason'),'original_language':x.get('original_language')}))
            elif isinstance(x,dict):
                for k,v in x.items(): walk(v,loc+[k])
            elif isinstance(x,list):
                for k,v in enumerate(x): walk(v,loc+[k])
            elif isinstance(x,str): records.append(dict(source=x,text='',speaker='',scene='',locator={'path':loc,'replace':True}))
        walk(data,[])
        return records,text,'jsonl' if suffix == '.jsonl' else 'json'
    if suffix == '.csv':
        rows=list(csv.DictReader(text.splitlines()))
        if not rows or 'source' not in rows[0]: raise ValueError('CSV должен содержать столбец source; translation необязателен')
        return [dict(source=r['source'],text=r.get('translation',''),speaker=r.get('speaker') or r.get('speaker_id') or r.get('character') or r.get('actor') or '',scene=r.get('scene',''),locator={'row':i,'utterance_kind':r.get('utterance_kind') or r.get('line_type'),'preserve_original':str(r.get('preserve_original','')).lower() in {'true','1','yes'},'preserve_reason':r.get('preserve_reason'),'original_language':r.get('original_language')}) for i,r in enumerate(rows)],text,'csv'
    if suffix == '.txt':
        return [dict(source=l.strip(),text='',speaker='',scene='',locator={'line':i}) for i,l in enumerate(text.splitlines()) if l.strip()],text,'text'
    raise ValueError('Поддерживаются .rpy, .txt, .json, .jsonl и .csv')

class Store:
    def __init__(self, path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY,name TEXT,root TEXT UNIQUE,settings TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,project INTEGER,path TEXT,kind TEXT,hash TEXT,original TEXT,UNIQUE(project,path));
            CREATE TABLE IF NOT EXISTS records(id INTEGER PRIMARY KEY,file INTEGER,position INTEGER,source TEXT,text TEXT DEFAULT '',speaker TEXT,scene TEXT,locator TEXT,status TEXT DEFAULT 'empty',revision INTEGER DEFAULT 0,manual INTEGER DEFAULT 0,reviewer TEXT DEFAULT '',UNIQUE(file,position,source));
            CREATE INDEX IF NOT EXISTS records_file ON records(file,position);
            CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY,record INTEGER,revision INTEGER,text TEXT,status TEXT,reviewer TEXT,reason TEXT,at TEXT);
            CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY,project INTEGER,stage TEXT,provider TEXT,settings TEXT,state TEXT,done INTEGER DEFAULT 0,total INTEGER DEFAULT 0,current INTEGER,error TEXT DEFAULT '',pid INTEGER,heartbeat REAL DEFAULT 0,created TEXT);
            CREATE TABLE IF NOT EXISTS queue(job INTEGER,record INTEGER,state TEXT DEFAULT 'pending',PRIMARY KEY(job,record));
            CREATE TABLE IF NOT EXISTS errors(id INTEGER PRIMARY KEY,project INTEGER,job INTEGER,record INTEGER,message TEXT,at TEXT,resolved INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS proposals(id INTEGER PRIMARY KEY,record INTEGER,revision INTEGER,text TEXT,reason TEXT,reviewer TEXT,at TEXT,state TEXT DEFAULT 'pending');
            CREATE TABLE IF NOT EXISTS revision_snapshots(record INTEGER,revision INTEGER,text TEXT,status TEXT,manual INTEGER,reviewer TEXT,PRIMARY KEY(record,revision));
            CREATE TABLE IF NOT EXISTS mcp_connections(session TEXT PRIMARY KEY,pid INTEGER,client TEXT,version TEXT,heartbeat REAL,connected INTEGER DEFAULT 1);
            CREATE TABLE IF NOT EXISTS mcp_requests(id TEXT PRIMARY KEY,session TEXT,job INTEGER,request TEXT,state TEXT DEFAULT 'pending',response TEXT DEFAULT '',created REAL);
            CREATE TABLE IF NOT EXISTS fragment_cache(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE IF NOT EXISTS fragment_plans(key TEXT PRIMARY KEY,parts TEXT);
            CREATE TABLE IF NOT EXISTS fragment_manifests(record INTEGER,source_hash TEXT,text_hash TEXT,parts TEXT,PRIMARY KEY(record,source_hash,text_hash));
            CREATE TABLE IF NOT EXISTS project_analysis(project INTEGER PRIMARY KEY,report TEXT,at TEXT);
            CREATE TABLE IF NOT EXISTS process_preferences(project INTEGER PRIMARY KEY,max_phrases INTEGER DEFAULT 200,max_seconds REAL DEFAULT 600,page_size INTEGER DEFAULT 50);
            CREATE TABLE IF NOT EXISTS language_checks(record INTEGER PRIMARY KEY,source_hash TEXT,base_language TEXT,result TEXT);
            CREATE TABLE IF NOT EXISTS preserved_records(record INTEGER PRIMARY KEY,kind TEXT,reason TEXT,previous_revision INTEGER);
            CREATE TABLE IF NOT EXISTS queue_overrides(job INTEGER,record INTEGER,settings TEXT,PRIMARY KEY(job,record));
            CREATE TABLE IF NOT EXISTS cache_owners(project INTEGER,key TEXT,PRIMARY KEY(project,key));
            CREATE TABLE IF NOT EXISTS id_counters(name TEXT PRIMARY KEY,next_id INTEGER);
            CREATE TABLE IF NOT EXISTS schema_versions(name TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS record_marks(record INTEGER PRIMARY KEY,kind TEXT,at REAL,was_manual INTEGER,protected_revision INTEGER);
            ''')
            if 'origin' not in {r[1] for r in db.execute('PRAGMA table_info(record_marks)')}:db.execute("ALTER TABLE record_marks ADD COLUMN origin TEXT DEFAULT 'unknown'")
            for column in ['completed','keep_visible','deleting']:
                if column not in {r[1] for r in db.execute('PRAGMA table_info(projects)')}:db.execute(f'ALTER TABLE projects ADD COLUMN {column} INTEGER DEFAULT 0')
            for table in ['projects','jobs','files','records']:
                db.execute('INSERT OR IGNORE INTO id_counters VALUES (?,?)',(table,db.execute(f'SELECT coalesce(max(id),0)+1 FROM {table}').fetchone()[0]))
            if 'hidden' not in {r[1] for r in db.execute('PRAGMA table_info(projects)')}:db.execute('ALTER TABLE projects ADD COLUMN hidden INTEGER DEFAULT 0')
            if 'preserve_override' not in {r[1] for r in db.execute('PRAGMA table_info(records)')}:db.execute('ALTER TABLE records ADD COLUMN preserve_override INTEGER DEFAULT 0')
            if 'independent' not in {r[1] for r in db.execute('PRAGMA table_info(record_marks)')}:
                db.execute('ALTER TABLE record_marks ADD COLUMN independent INTEGER DEFAULT 1')
                db.execute('UPDATE records SET manual=(SELECT was_manual FROM record_marks WHERE record=records.id) WHERE EXISTS(SELECT 1 FROM record_marks WHERE record=records.id AND protected_revision=records.revision)')
            if 'rate' not in {r[1] for r in db.execute('PRAGMA table_info(jobs)')}:
                db.execute('ALTER TABLE jobs ADD COLUMN rate REAL DEFAULT 0')
            columns={r[1] for r in db.execute('PRAGMA table_info(mcp_connections)')}
            if 'sampling' not in columns:db.execute('ALTER TABLE mcp_connections ADD COLUMN sampling INTEGER DEFAULT 0')
            if 'model' not in columns:db.execute("ALTER TABLE mcp_connections ADD COLUMN model TEXT DEFAULT ''")
            job_columns={r[1] for r in db.execute('PRAGMA table_info(jobs)')}
            if 'worker_active' not in job_columns:db.execute('ALTER TABLE jobs ADD COLUMN worker_active INTEGER DEFAULT 0')
            if 'budget_json' not in {r[1] for r in db.execute('PRAGMA table_info(errors)')}:db.execute("ALTER TABLE errors ADD COLUMN budget_json TEXT DEFAULT '{}'")
            for name,definition in [('fragment_done','INTEGER DEFAULT 0'),('fragment_total','INTEGER DEFAULT 0'),('fragment_preview',"TEXT DEFAULT ''")]:
                if name not in job_columns:db.execute(f'ALTER TABLE jobs ADD COLUMN {name} {definition}')
            if 'finished_at' not in {r[1] for r in db.execute('PRAGMA table_info(queue)')}:
                db.execute('ALTER TABLE queue ADD COLUMN finished_at REAL DEFAULT 0')
                old=db.execute("SELECT q.job,q.record,coalesce(max(h.at),j.created) at FROM queue q JOIN jobs j ON j.id=q.job LEFT JOIN history h ON h.record=q.record AND h.at>=j.created WHERE q.state='done' GROUP BY q.job,q.record").fetchall()
                for row in old:
                    try:stamp=time.mktime(time.strptime(row['at'],'%Y-%m-%d %H:%M:%S'))
                    except (ValueError,TypeError):stamp=time.time()
                    db.execute('UPDATE queue SET finished_at=? WHERE job=? AND record=?',(stamp,row['job'],row['record']))
            if 'priority' not in {r[1] for r in db.execute('PRAGMA table_info(queue)')}:
                db.execute('ALTER TABLE queue ADD COLUMN priority INTEGER DEFAULT 0')
            if 'at' not in {r[1] for r in db.execute('PRAGMA table_info(preserved_records)')}:
                db.execute('ALTER TABLE preserved_records ADD COLUMN at REAL DEFAULT 0')
                db.execute("UPDATE preserved_records SET at=coalesce((SELECT max(CAST(strftime('%s',h.at) AS REAL)) FROM history h WHERE h.record=preserved_records.record AND h.status='preserved'),0)")
            db.executescript("""CREATE TRIGGER IF NOT EXISTS queue_finished_timestamp AFTER UPDATE OF state ON queue
                WHEN OLD.state='pending' AND NEW.state IN ('done','error','protected')
                BEGIN UPDATE queue SET finished_at=CAST(strftime('%s','now') AS REAL) WHERE job=NEW.job AND record=NEW.record; END;""")
        with self.db() as db:needs_preservation=not db.execute("SELECT 1 FROM schema_versions WHERE name='preservation-v1'").fetchone()
        if needs_preservation:
            self.classify_preserved()
            with self.db() as db:db.execute("INSERT OR IGNORE INTO schema_versions VALUES ('preservation-v1')")
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=30); db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA journal_mode=WAL'); db.execute('PRAGMA foreign_keys=ON')
            yield db
            db.commit()
        except BaseException:
            db.rollback(); raise
        finally: db.close()
    def project(self, root):
        p=Path(root).resolve()
        if not p.is_dir(): raise ValueError('Папка проекта не найдена')
        with self.db() as db:
            existing=db.execute('SELECT * FROM projects WHERE root=? COLLATE NOCASE',(str(p),)).fetchone()
            if existing:
                if existing['deleting']:raise ValueError('Сначала завершите удаление проекта')
                db.execute('UPDATE projects SET hidden=0,completed=0,keep_visible=1 WHERE id=?',(existing['id'],))
                return dict(db.execute('SELECT * FROM projects WHERE id=?',(existing['id'],)).fetchone())
            pid=self.next_id(db,'projects')
            db.execute('INSERT OR IGNORE INTO projects(id,name,root) VALUES (?,?,?)',(pid,p.name,str(p)))
            return dict(db.execute('SELECT * FROM projects WHERE root=?',(str(p),)).fetchone())
    def hide_project(self,pid,hidden=True):
        with self.db() as db:
            if not db.execute('UPDATE projects SET hidden=?,completed=0,keep_visible=1 WHERE id=? AND deleting=0',(int(hidden),pid)).rowcount:raise ValueError('Проект не найден')
        return {'ok':True}
    @staticmethod
    def next_id(db,table):
        if table not in {'projects','jobs','files','records'}:raise ValueError('Некорректный счётчик')
        return db.execute(f'UPDATE id_counters SET next_id=max(next_id,(SELECT coalesce(max(id),0)+1 FROM {table}))+1 WHERE name=? RETURNING next_id-1',(table,)).fetchone()[0]
    def archive_completed(self,editing_project=0):
        with self.db() as db:
            db.execute("UPDATE projects SET keep_visible=0 WHERE EXISTS(SELECT 1 FROM files f JOIN records r ON r.file=f.id WHERE f.project=projects.id AND r.status='empty')")
            db.execute("UPDATE projects SET hidden=1,completed=1 WHERE id<>? AND hidden=0 AND keep_visible=0 AND deleting=0 AND EXISTS(SELECT 1 FROM jobs completed_job WHERE completed_job.project=projects.id AND completed_job.stage='translate' AND completed_job.state='done') AND EXISTS(SELECT 1 FROM files f JOIN records r ON r.file=f.id WHERE f.project=projects.id) AND NOT EXISTS(SELECT 1 FROM files f JOIN records r ON r.file=f.id WHERE f.project=projects.id AND r.status='empty') AND NOT EXISTS(SELECT 1 FROM jobs j WHERE j.project=projects.id AND j.state IN ('queued','running','paused','waiting','held'))",(editing_project,))
    def complete_project(self,pid):
        with self.db() as db:
            if not db.execute('SELECT id FROM projects WHERE id=? AND deleting=0',(pid,)).fetchone():raise ValueError('Проект не найден')
            total,empty=db.execute("SELECT count(*),coalesce(sum(r.status='empty'),0) FROM records r JOIN files f ON f.id=r.file WHERE f.project=?",(pid,)).fetchone()
            if not total or empty:raise ValueError('В проекте ещё есть строки без перевода')
            db.execute('UPDATE projects SET hidden=1,completed=1,keep_visible=0 WHERE id=?',(pid,))
        return {'ok':True}
    def completed_projects(self):
        with self.db() as db:return [dict(r) for r in db.execute('SELECT * FROM projects WHERE completed=1 AND deleting=0 ORDER BY id')]
    def delete_project_data(self,pid):
        with self.db() as db:
            if not db.execute('SELECT id FROM projects WHERE id=? AND deleting=1',(pid,)).fetchone():raise ValueError('Проект не подготовлен к удалению')
            record_query='SELECT r.id FROM records r JOIN files f ON f.id=r.file WHERE f.project=?'
            for table in ['history','revision_snapshots','proposals','record_marks','preserved_records','language_checks','fragment_manifests']:
                db.execute(f'DELETE FROM {table} WHERE record IN ({record_query})',(pid,))
            db.execute('DELETE FROM mcp_requests WHERE job IN (SELECT id FROM jobs WHERE project=?)',(pid,))
            db.execute('DELETE FROM queue_overrides WHERE job IN (SELECT id FROM jobs WHERE project=?)',(pid,))
            db.execute('DELETE FROM queue WHERE job IN (SELECT id FROM jobs WHERE project=?)',(pid,))
            for table in ['fragment_cache','fragment_plans']:
                db.execute(f'DELETE FROM {table} WHERE key IN (SELECT key FROM cache_owners WHERE project=? AND NOT EXISTS(SELECT 1 FROM cache_owners other WHERE other.key=cache_owners.key AND other.project<>?))',(pid,pid))
            db.execute('DELETE FROM cache_owners WHERE project=?',(pid,))
            db.execute(f'DELETE FROM records WHERE id IN ({record_query})',(pid,))
            for table in ['files','jobs','errors','project_analysis','process_preferences']:db.execute(f'DELETE FROM {table} WHERE project=?',(pid,))
            db.execute('DELETE FROM projects WHERE id=?',(pid,))
        return {'deleted':True}
    def hidden_projects(self):
        with self.db() as db:return [dict(r) for r in db.execute("SELECT p.*,EXISTS(SELECT 1 FROM jobs j WHERE j.project=p.id AND j.state IN ('running','queued')) active FROM projects p WHERE (p.hidden=1 AND p.completed=0) OR p.deleting=1 ORDER BY p.id")]

    def import_files(self, project, paths):
        counts={'files':0,'added':0,'errors':[]}
        for entry in paths:
            p=Path(entry).resolve()
            try:
                records,original,kind=extract(p)
                if not records: raise ValueError('Диалоги или строки не найдены')
                digest=hashlib.sha256(original.encode()).hexdigest()
                with self.db() as db:
                    old=db.execute('SELECT * FROM files WHERE project=? AND path=?',(project,str(p))).fetchone()
                    if old and old['hash'] != digest:
                        raise ValueError('Файл изменился после импорта. Добавьте его копию как новую версию; существующие правки сохранены')
                    db.execute('INSERT OR IGNORE INTO files(id,project,path,kind,hash,original) VALUES (?,?,?,?,?,?)',(self.next_id(db,'files'),project,str(p),kind,digest,original))
                    fid=db.execute('SELECT id FROM files WHERE project=? AND path=?',(project,str(p))).fetchone()[0]
                    for i,r in enumerate(records):
                        value=r['text'] if isinstance(r['text'],str) else ''
                        state='translated' if value and value!=r['source'] else 'empty'
                        counts['added']+=db.execute('INSERT OR IGNORE INTO records(id,file,position,source,text,speaker,scene,locator,status) VALUES (?,?,?,?,?,?,?,?,?)',(self.next_id(db,'records'),fid,i,r['source'],value,r['speaker'],r['scene'],dump(r['locator']),state)).rowcount
                self.classify_preserved(project)
                counts['files']+=1
            except Exception as e:
                counts['errors'].append({'path':str(p),'message':str(e)})
                with self.db() as db: db.execute('INSERT INTO errors(project,message,at) VALUES (?,?,?)',(project,str(p)+': '+str(e),now()))
        return counts
    def record(self, rid):
        with self.db() as db:
            r=db.execute("SELECT r.*,f.project,f.path,coalesce(m.kind,'') flag,m.at flagged_at,m.was_manual flag_previous_manual,coalesce((SELECT reason FROM preserved_records WHERE record=r.id),'') preserve_reason,coalesce((SELECT kind FROM preserved_records WHERE record=r.id),'') preserve_kind FROM records r JOIN files f ON f.id=r.file LEFT JOIN record_marks m ON m.record=r.id WHERE r.id=?",(rid,)).fetchone()
            if not r: raise ValueError('Строка не найдена')
            result=dict(r)
            check=db.execute('SELECT result FROM language_checks WHERE record=?',(rid,)).fetchone()
            result['language_note']=json.loads(check[0]).get('reason','') if check else ''
            return result
    def preserve(self,rid,revision,kind,reason,worker_job=None):
        if kind not in {'symbols','foreign'} or not isinstance(reason,str) or not reason.strip() or len(reason)>1000:raise ValueError('Укажите причину сохранения оригинала (до 1000 символов)')
        with self.db() as db:
            r=db.execute('SELECT * FROM records WHERE id=?',(rid,)).fetchone()
            if not r or r['revision']!=revision:raise ValueError('Строка изменилась; обновите её')
            if not r['source'].strip():raise ValueError('Пустая строка')
            if db.execute("SELECT 1 FROM jobs j JOIN queue q ON q.job=j.id WHERE j.state='running' AND j.current=? AND q.record=? AND q.state='pending' AND j.id<>?",(rid,rid,worker_job or -1)).fetchone():raise ValueError('Дождитесь завершения обработки этой строки')
            if worker_job and not db.execute("SELECT 1 FROM jobs WHERE id=? AND current=? AND state='running'",(worker_job,rid)).fetchone():raise ValueError('Обработчик больше не владеет строкой')
            prior=db.execute('SELECT previous_revision FROM preserved_records WHERE record=?',(rid,)).fetchone()
            db.execute('INSERT OR IGNORE INTO revision_snapshots VALUES (?,?,?,?,?,?)',(rid,revision,r['text'],r['status'],r['manual'],r['reviewer']))
            db.execute('INSERT OR REPLACE INTO preserved_records(record,kind,reason,previous_revision,at) VALUES (?,?,?,?,?)',(rid,kind,reason.strip(),prior[0] if prior else revision,time.time()))
            db.execute("UPDATE records SET text=source,status='preserved',revision=revision+1,reviewer='literal' WHERE id=?",(rid,))
            db.execute('INSERT INTO history(record,revision,text,status,reviewer,reason,at) VALUES (?,?,?,?,?,?,?)',(rid,revision+1,r['source'],'preserved','literal',reason.strip(),now()))
            pending=db.execute("SELECT job FROM queue WHERE record=? AND state='pending' AND job<>?",(rid,worker_job or -1)).fetchall()
            db.execute("UPDATE queue SET state='done' WHERE record=? AND state='pending' AND job<>?",(rid,worker_job or -1))
            for job in pending:db.execute('UPDATE jobs SET done=done+1 WHERE id=?',(job[0],))
            db.execute('UPDATE errors SET resolved=1 WHERE record=?',(rid,))
        return self.record(rid)
    def restore_preserved(self,rid,revision):
        with self.db() as db:
            r=db.execute('SELECT * FROM records WHERE id=?',(rid,)).fetchone();p=db.execute('SELECT * FROM preserved_records WHERE record=?',(rid,)).fetchone()
            if not r or r['revision']!=revision or not p:raise ValueError('Строка изменилась или уже возвращена')
            old=db.execute('SELECT * FROM revision_snapshots WHERE record=? AND revision=?',(rid,p['previous_revision'])).fetchone()
            db.execute('DELETE FROM preserved_records WHERE record=?',(rid,))
            text,status,manual,reviewer=(old['text'],old['status'],old['manual'],old['reviewer']) if old else ('','empty',r['manual'],'')
            if r['manual'] and r['text']!=r['source']:text,status,manual,reviewer=r['text'],'translated',1,r['reviewer']
            db.execute('UPDATE records SET text=?,status=?,manual=?,reviewer=?,preserve_override=1,revision=revision+1 WHERE id=?',(text,status,manual,reviewer,rid))
            if status=='empty':
                jobs=db.execute("SELECT j.id FROM jobs j JOIN queue q ON q.job=j.id WHERE q.record=? AND q.state='done' AND j.stage='translate' AND j.state IN ('queued','running','paused')",(rid,)).fetchall()
                for job in jobs:
                    db.execute("UPDATE queue SET state='pending',finished_at=0 WHERE job=? AND record=?",(job[0],rid));db.execute('UPDATE jobs SET done=max(0,done-1) WHERE id=?',(job[0],))

        return self.record(rid)
    def classify_preserved(self,project=None):
        from preservation import symbolic
        with self.db() as db:
            query="SELECT r.* FROM records r JOIN files f ON f.id=r.file WHERE r.preserve_override=0 AND r.manual=0 AND r.status<>'preserved' AND (r.status='empty' OR r.text=r.source)"
            rows=[dict(r) for r in db.execute(query+(' AND f.project=?' if project else ''),[project] if project else [])]
        for r in rows:
            locator=json.loads(r['locator']);kind='symbols' if symbolic(r['source']) else 'foreign' if locator.get('preserve_original') is True else ''
            if kind:
                reason='Символы, пауза или служебные вставки: оригинал сохранён без перевода' if kind=='symbols' else locator.get('preserve_reason') or 'Авторская пометка: оригинал сохранён'+(' (язык: '+str(locator['original_language'])+')' if locator.get('original_language') else '')
                try:self.preserve(r['id'],r['revision'],kind,reason)
                except ValueError:pass
    def mark(self,rid,revision,kind,origin='human'):
        if origin not in {'human','language'}:raise ValueError('Некорректный источник пометки')
        if kind not in {'','bad','review'}:raise ValueError('Некорректная пометка')
        with self.db() as db:
            r=db.execute('SELECT * FROM records WHERE id=?',(rid,)).fetchone();previous=db.execute('SELECT * FROM record_marks WHERE record=?',(rid,)).fetchone()
            if not r or r['revision']!=revision:raise ValueError('Строка изменилась; обновите её перед пометкой')
            db.execute('INSERT OR IGNORE INTO revision_snapshots VALUES (?,?,?,?,?,?)',(rid,revision,r['text'],r['status'],r['manual'],r['reviewer']))
            if kind:
                db.execute('INSERT OR REPLACE INTO record_marks(record,kind,at,was_manual,protected_revision,origin) VALUES (?,?,?,?,?,?)',(rid,kind,time.time(),r['manual'],revision+1,origin))
            else:
                db.execute('DELETE FROM record_marks WHERE record=?',(rid,))
            db.execute('UPDATE records SET revision=revision+1 WHERE id=?',(rid,))
        return self.record(rid)
    def update(self, rid, revision, text, status, reviewer, reason='', manual=False):
        r=self.record(rid)
        if status not in {'translated','edited','verified'} and not (status=='preserved' and manual and r['preserve_kind']):raise ValueError('Некорректный этап')
        if r['preserve_kind']:
            if not manual:raise ValueError('Оригинал оставлен без перевода')
            status='preserved'
        validate(r['source'],text)
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO revision_snapshots VALUES (?,?,?,?,?,?)',(rid,revision,r['text'],r['status'],r['manual'],r['reviewer']))
            cursor=db.execute('UPDATE records SET text=?,status=?,reviewer=?,revision=revision+1,manual=? WHERE id=? AND revision=?',(text,status,reviewer,int(manual or r['manual']),rid,revision))
            if not cursor.rowcount: raise ValueError('Строка уже изменена. Обновите её перед сохранением')
            db.execute('INSERT INTO history(record,revision,text,status,reviewer,reason,at) VALUES (?,?,?,?,?,?,?)',(rid,revision+1,text,status,reviewer,reason,now()))
            db.execute('UPDATE errors SET resolved=1 WHERE record=?',(rid,))
        return self.record(rid)
    def undo(self,rid,revision):
        with self.db() as db:
            r=db.execute('SELECT * FROM records WHERE id=?',(rid,)).fetchone()
            previous=db.execute('SELECT * FROM revision_snapshots WHERE record=? AND revision=?',(rid,revision-1)).fetchone()
            last_edit=db.execute("SELECT * FROM history WHERE record=? AND reviewer LIKE 'human%' ORDER BY id DESC LIMIT 1",(rid,)).fetchone()
            if r and last_edit and r['revision']>last_edit['revision'] and r['text']==last_edit['text']:
                previous=db.execute('SELECT * FROM revision_snapshots WHERE record=? AND revision=?',(rid,last_edit['revision']-1)).fetchone()
            if not r or r['revision']!=revision: raise ValueError('Строка изменилась; обновите её перед отменой')
            if not previous or not r['reviewer'].startswith('human'): raise ValueError('Нет сохранённой ручной правки для отмены')
            db.execute('UPDATE records SET text=?,status=?,manual=?,reviewer=?,revision=revision+1 WHERE id=? AND revision=?',(previous['text'],previous['status'],previous['manual'],previous['reviewer'],rid,revision))
            db.execute('INSERT INTO history(record,revision,text,status,reviewer,reason,at) VALUES (?,?,?,?,?,?,?)',(rid,revision+1,previous['text'],previous['status'],'human','Отмена сохранённой правки',now()))
        return self.record(rid)
    def clear_marks(self,project,kind):
        if kind not in {'bad','review'}:raise ValueError('Некорректная папка пометок')
        with self.db() as db:
            ids=[r[0] for r in db.execute('SELECT m.record FROM record_marks m JOIN records r ON r.id=m.record JOIN files f ON f.id=r.file WHERE f.project=? AND m.kind=?',(project,kind))]
            db.executemany('DELETE FROM record_marks WHERE record=?',[(rid,) for rid in ids])
            db.executemany('UPDATE records SET revision=revision+1 WHERE id=?',[(rid,) for rid in ids])
        return {'cleared':len(ids)}
    def create_job(self, project, stage, provider, settings, file=None, retry=False, mark_kind=None,allow_manual=False,record_ids=None,defer=False):
        if stage not in {'translate','review','cloud'} or provider not in {'local','cloud','mcp'}: raise ValueError('Некорректный режим')
        if stage=='cloud' and provider not in {'cloud','mcp'}: raise ValueError('Зелёная проверка требует внешнего провайдера')
        from speaker_context import neighbor_settings
        self.classify_preserved(project)
        if not isinstance(settings.get('auto_foreign',True),bool):raise ValueError('Некорректная настройка определения языка')
        settings={**settings,**neighbor_settings(settings),'auto_foreign':settings.get('auto_foreign',True)}
        settings.pop('marked_review',None);settings.pop('review_manual',None)
        if mark_kind is not None and (mark_kind not in {'bad','review'} or stage!='review'):raise ValueError('Папки пометок доступны только для редактуры')
        query='SELECT r.id FROM records r JOIN files f ON f.id=r.file WHERE f.project=?'
        if not (mark_kind and allow_manual):query+=' AND r.manual=0'
        if mark_kind:settings.update(marked_review=mark_kind,review_manual=bool(allow_manual),review_pass=str(time.time_ns()))
        args=[project]
        if record_ids is not None:
            if not record_ids:raise ValueError('Нет выбранных строк')
            query+=' AND r.id IN ('+','.join('?' for _ in record_ids)+')';args.extend(record_ids)
        if file: query+=' AND r.file=?'; args.append(file)
        if mark_kind:
            query+=" AND r.status<>'preserved' AND r.text<>'' AND EXISTS(SELECT 1 FROM record_marks m WHERE m.record=r.id AND m.kind=?)";args.append(mark_kind)
        else:
            query += ' AND r.status IN '+ ('(\'empty\')' if stage=='translate' else "('translated','edited')" if stage=='cloud' else "('translated')")
        if retry: query+=' AND EXISTS(SELECT 1 FROM errors e WHERE e.record=r.id AND e.resolved=0)'
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT id FROM projects WHERE id=? AND deleting=0',(project,)).fetchone():raise ValueError('Проект удалён или удаляется')
            active=db.execute("SELECT id FROM jobs WHERE project=? AND (state IN ('queued','running','paused','waiting','held') OR worker_active=1)",(project,)).fetchone()
            if active and not defer: raise ValueError('Завершите или отмените текущую задачу перед новой')
            ids=[r[0] for r in db.execute(query+' ORDER BY r.file,r.position',args)]
            if not ids: raise ValueError('Нет подходящих строк (ручные правки защищены)')
            settings.setdefault('cache_namespace',f'project:{project}')
            jid=self.next_id(db,'jobs')
            db.execute('INSERT INTO jobs(id,project,stage,provider,settings,state,total,created) VALUES (?,?,?,?,?,?,?,?)',(jid,project,stage,provider,dump(settings),'waiting' if defer else 'queued',len(ids),now()))
            db.executemany('INSERT INTO queue(job,record) VALUES (?,?)',[(jid,r) for r in ids]); return jid
    def error(self, job, record, message):
        with self.db() as db:
            p=db.execute('SELECT project FROM jobs WHERE id=?',(job,)).fetchone()[0]
            db.execute('INSERT INTO errors(project,job,record,message,at) VALUES (?,?,?,?,?)',(p,job,record,message[:2000],now()))
        if record:
            try:
                from error_workflow import budget
                with self.db() as db:
                    j=db.execute('SELECT * FROM jobs WHERE id=?',(job,)).fetchone();settings=json.loads(j['settings'])
                    override=db.execute('SELECT settings FROM queue_overrides WHERE job=? AND record=?',(job,record)).fetchone()
                    if override:settings.update(json.loads(override[0]))
                estimate=budget(self,p,record,j['stage'],settings)
                with self.db() as db:db.execute('UPDATE errors SET budget_json=? WHERE job=? AND record=? AND resolved=0',(dump(estimate),job,record))
            except (ValueError,KeyError,TypeError):pass
    def export(self, fid, destination, apply=False):
        with self.db() as db:
            f=dict(db.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()); records=[dict(r) for r in db.execute('SELECT * FROM records WHERE file=? ORDER BY position',(fid,))]
        p=Path(f['path']); target=Path(destination).resolve()
        if apply and f['kind'] not in {'renpy','csv','json','jsonl'}: raise ValueError('Для TXT доступен только экспорт корпуса')
        if apply and (target!=p.resolve() or f['kind']=='renpy' and '/tl/' not in p.as_posix()): raise ValueError('В игру можно записывать только существующий слой перевода. Исходные Ren’Py скрипты не переписываются')
        if apply:
            if hashlib.sha256(p.read_text(encoding='utf-8-sig').encode()).hexdigest()!=f['hash']: raise ValueError('Игровой файл изменён с момента импорта. Запись остановлена')
            raise ValueError('Запись в игру в этой версии отключена: экспортируйте отдельный файл и проверьте его в игре')
        if target == p.resolve(): raise ValueError('Экспортируйте в другой файл; оригинал защищён')
        if f['kind']=='renpy' and '/tl/' in p.as_posix():
            lines=f['original'].splitlines(keepends=True)
            for r in records:
                if r['status']=='empty': continue
                validate(r['source'],r['text']); loc=json.loads(r['locator']); line=lines[loc['line']]
                lines[loc['line']]=line[:loc['start']]+json.dumps(r['text'],ensure_ascii=False)+line[loc['end']:]
            output=''.join(lines)
        else:
            output=dump([{'id':r['id'],'source':r['source'],'translation':r['text'],'status':r['status'],'speaker':r['speaker'],'scene':r['scene']} for r in records])
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists(): shutil.copy2(target,target.with_name(target.name+'.backup-'+str(time.time_ns())))
        target.write_text(output,encoding='utf-8'); return str(target)

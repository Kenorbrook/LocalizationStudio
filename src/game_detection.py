"""Offline, bounded, read-only inspection. Evidence is not an extraction adapter."""
import codecs,csv,io,json,os,re,time
from collections import Counter
from pathlib import Path
from core import dump,now

SKIP={'renpy','lib','node_modules','.git','.svn','.venv','venv','__pycache__','.godot','library','temp','obj','logs','saves','cache','caches','translation_tools','build','dist','release'}
TEXT={'.rpy','.txt','.csv','.json','.jsonl','.gd','.tscn','.tres','.cs','.unity','.prefab','.ks','.tjs','.po','.pot','.xml','.srt','.vtt'}
ARCHIVES={'.rpa','.rpyc','.pck','.pak','.utoc','.ucas','.xp3','.assets','.bundle','.unity3d','.rgssad','.rgss2a','.rgss3a','.wolf','.wod','.win'}
NAMES={'renpy':"Ren’Py",'unity':'Unity','godot':'Godot','rpgmv':'RPG Maker MV','rpgmz':'RPG Maker MZ','kirikiri':'KiriKiri / TVP','unreal':'Unreal Engine','gamemaker':'GameMaker'}
DOCS={'renpy':'https://www.renpy.org/doc/html/translation.html','unity':'https://github.com/Unity-Technologies/UnityDataTools/blob/main/Documentation/playerbuild-format.md','godot':'https://docs.godotengine.org/en/stable/tutorials/export/exporting_pcks.html'}

def read_prefix(path,limit=65536):
    with path.open('rb') as stream:data=stream.read(limit+1)
    truncated=len(data)>limit
    return codecs.getincrementaldecoder('utf-8-sig')('strict').decode(data[:limit],final=not truncated),truncated

def corpus_samples(data):
    samples=[];stack=[data];visited=0
    while stack and visited<10000:
        value=stack.pop();visited+=1
        if isinstance(value,dict):
            if isinstance(value.get('source'),str):
                if value['source'].strip():samples.append(value['source'][:180])
            else:stack.extend(reversed(list(value.values())))
        elif isinstance(value,list):stack.extend(reversed(value))
        if len(samples)>=3:break
    return samples

def probe(path,relative,size):
    item={'path':relative,'size':size,'format':path.suffix.lower()[1:],'role':'candidate','supported':False,'samples':[],'reason':'Нужен адаптер этого формата','sample_truncated':False}
    try:
        text,truncated=read_prefix(path);item['sample_truncated']=truncated;item['encoding']='UTF-8'
        if '\x00' in text:raise UnicodeError('Бинарные данные вместо текста UTF-8')
    except (OSError,UnicodeError) as e:
        item['reason']='Не удалось прочитать как UTF-8: '+type(e).__name__;item['encoding']='unknown';return item
    suffix=path.suffix.lower();low=relative.lower();parts=low.split('/')
    if suffix=='.rpy':
        item['role']='translation' if 'tl' in parts else 'script'
        item['format']='Ren’Py script'
        item['supported']=True;item['reason']='Доступен импорт .rpy; экспорт слоя game/tl — .rpy, исходного скрипта — корпус JSON'
        for line in text.splitlines():
            match=re.match(r'\s*(?:#\s*)?(?:old\s+|[\w]+\s+)?"((?:\\.|[^"\\])*)"',line)
            if match and not re.match(r'\s*(?:#\s*)?(?:voice|play|queue|image|define|default|show|scene)\b',line):item['samples'].append(match[1][:180])
            if len(item['samples'])>=3:break
        item['renpy_syntax']=bool(re.search(r'(?m)^\s*(?:label\s+\w+\s*:|translate\s+\w+\s+\w+\s*:|screen\s+\w+\s*[(:]|define\s+\w+\s*=\s*Character\s*\()',text))
        item['language_actions']=list(dict.fromkeys(re.findall(r'\bLanguage\s*\(\s*(None|"[^"\n]*"|\x27[^\x27\n]*\x27)\s*\)',text)))[:12]
        item['preferences_screen']=bool(re.search(r'(?m)^\s*screen\s+preferences\s*\(',text))
    elif suffix in {'.json','.jsonl'}:
        if not truncated:
            try:
                data=json.loads(text) if suffix=='.json' else [json.loads(line) for line in text.splitlines() if line.strip()]
                item['samples']=corpus_samples(data)
                if item['samples']:item.update(role='corpus',supported=True,reason='Подтверждены записи со строковым полем source; импорт и экспорт корпуса JSON')
                elif isinstance(data,list) and any(isinstance(row,dict) and 'events' in row for row in data if row):item['reason']='Структурированные данные игры; общий импорт всех строк может захватить имена ресурсов'
            except (ValueError,RecursionError):item['reason']='Не удалось разобрать JSON; требуется проверка формата'
        else:item['reason']='Большой JSON: содержимое не разобрано целиком; формат нужно подтвердить'
    elif suffix=='.csv':
        try:
            rows=csv.DictReader(io.StringIO(text))
            if 'source' in (rows.fieldnames or []):
                item.update(role='corpus',supported=True,reason='Подтверждён столбец source; импорт и экспорт корпуса JSON')
                for row in rows:
                    if row.get('source','').strip():item['samples'].append(row['source'][:180])
                    if len(item['samples'])>=3:break
        except csv.Error:item['reason']='Некорректный CSV'
    elif suffix=='.txt':
        item.update(role='text',supported=True,reason='Импорт построчного текста; выберите файл после просмотра образца. Экспорт — корпус JSON')
        item['samples']=[line.strip()[:180] for line in text.splitlines() if line.strip()][:3]
    else:
        item['samples']=[line.strip()[:180] for line in text.splitlines() if line.strip()][:3]
        item['role']='subtitle' if suffix in {'.srt','.vtt'} else 'script' if suffix in {'.gd','.cs','.ks','.tjs'} else 'resource'
    if size>8*1024*1024:item.update(supported=False,reason='Файл больше 8 МиБ: автоматический импорт через анализ отключён; нужен отдельный разбор')
    return item

def analyze(root,max_files=100000,max_seconds=15,max_candidates=3000,max_depth=14):
    root=Path(root).resolve()
    if not root.is_dir():raise ValueError('Папка игры не найдена')
    started=time.monotonic();inventory=[];directories=set();warnings=[];errors=[];stopped=False;incomplete=False;count=0
    stack=[(root,0)]
    while stack and not stopped:
        folder,depth=stack.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if count>=max_files or time.monotonic()-started>max_seconds:
                        stopped=True;warnings.append('Сканирование ограничено по времени/числу файлов; отчёт неполный');break
                    if entry.is_symlink():continue
                    try:
                        stat=entry.stat(follow_symlinks=False)
                        if getattr(stat,'st_file_attributes',0)&0x400:continue # Windows reparse points / junctions.
                        path=Path(entry.path);relative=path.relative_to(root).as_posix();low=relative.lower()
                        if entry.is_dir(follow_symlinks=False):
                            directories.add(low)
                            if entry.name.lower() not in SKIP:
                                if depth<max_depth:stack.append((path,depth+1))
                                else:incomplete=True;warnings.append('Пропущена слишком глубокая папка: '+relative)
                        elif entry.is_file(follow_symlinks=False):
                            inventory.append((path,relative,stat.st_size));count+=1
                    except OSError as e:
                        if len(errors)<30:errors.append(entry.name+': '+type(e).__name__)
        except OSError as e:
            if len(errors)<30:errors.append(str(folder)+': '+type(e).__name__)
    inventory.sort(key=lambda row:row[1].lower());names={rel.lower():path for path,rel,size in inventory};extensions=Counter(path.suffix.lower() for path,rel,size in inventory)
    signals={key:[] for key in NAMES};score=Counter();versions={}
    def signal(key,points,reason,paths):
        existing=next((proof for proof in signals[key] if proof['reason']==reason),None)
        if existing:existing['paths']=list(dict.fromkeys(existing['paths']+paths))[:8]
        else:score[key]+=points;signals[key].append({'reason':reason,'paths':paths[:8]})
    rpy=[rel for path,rel,size in inventory if path.suffix.lower()=='.rpy'];rpyc=[rel for path,rel,size in inventory if path.suffix.lower()=='.rpyc']
    runtime=[d+'/' for d in directories if d=='renpy' or d.endswith('/renpy')]
    if runtime:signal('renpy',5,'Папка среды Ren’Py',runtime)
    if rpy:signal('renpy',3,'Скрипты .rpy',rpy)
    if rpyc:signal('renpy',3,'Скомпилированные скрипты .rpyc',rpyc)
    for name,path in names.items():
        if name.endswith('projectsettings/projectversion.txt') and name[:-len('projectsettings/projectversion.txt')]+'assets' in directories:
            signal('unity',10,'Исходный проект Unity',[name[:-len('projectsettings/projectversion.txt')]+'Assets/',path.relative_to(root).as_posix()])
            try:
                match=re.search(r'm_EditorVersion:\s*(\S+)',read_prefix(path)[0])
                if match:versions['unity']=match[1]
            except (OSError,UnicodeError):pass
    managers=[rel for path,rel,size in inventory if path.name.lower() in {'globalgamemanagers','globalgamemanager.assets'} and any(p.lower().endswith('_data') for p in path.relative_to(root).parts)]
    if managers:signal('unity',8,'Данные Unity Player',managers)
    player=[rel for path,rel,size in inventory if path.name.lower() in {'unityplayer.dll','gameassembly.dll'} or path.name.lower().startswith('unityengine.') and path.suffix.lower()=='.dll']
    if player:signal('unity',4,'Библиотеки Unity',player)
    for path,rel,size in inventory:
        if time.monotonic()-started>max_seconds:
            stopped=True;warnings.append('Не все заголовки файлов просмотрены: лимит времени анализа');break
        low=rel.lower();name=path.name.lower()
        if name=='project.godot':
            try:
                if re.search(r'(?m)^config_version\s*=\s*\d+',read_prefix(path)[0]):signal('godot',10,'Настройки проекта Godot',[rel])
            except (OSError,UnicodeError):pass
        if name in {'rpg_core.js','rmmz_core.js'}:
            try:
                text,_=read_prefix(path)
                if 'RPGMAKER_NAME' in text or 'Utils' in text and 'RPG Maker' in text:signal('rpgmz' if name=='rmmz_core.js' else 'rpgmv',10,'Основной JavaScript движка RPG Maker',[rel])
            except (OSError,UnicodeError):pass
        if path.suffix.lower()=='.uproject':
            try:
                data=json.loads(read_prefix(path)[0])
                if isinstance(data,dict) and 'FileVersion' in data and 'EngineAssociation' in data:
                    signal('unreal',10,'Файл проекта Unreal Engine',[rel]);versions['unreal']=str(data['EngineAssociation'])
            except (OSError,UnicodeError,ValueError):pass
        if path.suffix.lower() in {'.rpa','.pck','.xp3','.unity3d','.bundle'} or name=='data.win':
            try:
                with path.open('rb') as stream:header=stream.read(16)
                if header.startswith(b'RPA-'):signal('renpy',5,'Заголовок архива RPA',[rel])
                elif header.startswith(b'GDPC'):signal('godot',9,'Заголовок пакета PCK',[rel])
                elif header.startswith(b'XP3\r\n \n\x1a\x8bg\x01'):signal('kirikiri',9,'Заголовок архива XP3',[rel])
                elif header.startswith(b'UnityFS'):signal('unity',6,'Заголовок пакета UnityFS',[rel])
                elif name=='data.win' and header.startswith(b'FORM'):signal('gamemaker',8,'Контейнер data.win с заголовком FORM',[rel])
            except OSError:pass
    if extensions['.ks'] and extensions['.tjs']:signal('kirikiri',5,'Совместно обнаружены сценарии .ks и .tjs',[rel for path,rel,size in inventory if path.suffix.lower() in {'.ks','.tjs'}])
    unrealpacks=[rel for path,rel,size in inventory if '/content/paks/' in '/'+rel.lower() and path.suffix.lower() in {'.pak','.utoc','.ucas'}]
    if unrealpacks and (any(d=='engine' or d.startswith('engine/binaries') for d in directories) or extensions['.utoc'] and extensions['.ucas']):signal('unreal',8,'Контейнеры в Content/Paks и структура Unreal',unrealpacks)
    candidates=[];archives=[];languages=set();actions=[];menus=[];sample_bytes=0;source_syntax=False
    ignored_names={'readme','license','licence','changelog','requirements','errors','log','traceback'}
    for path,rel,size in inventory:
        low=rel.lower();parts=low.split('/');suffix=path.suffix.lower()
        if suffix in ARCHIVES:
            if len(archives)<500:archives.append({'path':rel,'size':size,'format':suffix[1:],'extraction':'not_implemented'})
        if 'tl' in parts and suffix=='.rpy':
            pos=parts.index('tl')
            if pos+1<len(parts)-1:languages.add(rel.split('/')[pos+1])
        if suffix not in TEXT or path.stem.lower() in ignored_names or path.name.lower() in {'package.json','plugins.js','projectversion.txt','cmakelists.txt'}:continue
        if len(candidates)>=max_candidates:continue
        if sample_bytes>=8*1024*1024 or time.monotonic()-started>max_seconds:
            stopped=True
            candidates.append({'path':rel,'size':size,'format':suffix[1:],'role':'candidate','supported':False,'samples':[],'reason':'Лимит просмотра содержимого достигнут; файл ещё не проверен'});continue
        item=probe(path,rel,size);sample_bytes+=min(size,65536);candidates.append(item)
        if item.get('renpy_syntax'):source_syntax=True
        for action in item.get('language_actions',[]):
            if len(actions)<30:actions.append({'path':rel,'argument':action})
        if item.get('preferences_screen'):menus.append(rel)
    if source_syntax:signal('renpy',5,'Подтверждён синтаксис label / translate / screen / Character',rpy)
    if len(candidates)>=max_candidates:
        stopped=True;warnings.append('Достигнут лимит списка текстовых кандидатов; остальные файлы не включены')
    if sum(extensions[suffix] for suffix in ARCHIVES)>len(archives):
        incomplete=True;warnings.append('Список контейнеров ограничен 500 файлами; остальные не включены')
    if sum(path.suffix.lower() in TEXT for path,rel,size in inventory)>len(candidates):warnings.append('Образцы показаны для ограниченного набора файлов; служебные тексты исключены')
    engines=[{'id':key,'name':NAMES[key],'confidence':'high' if score[key]>=8 else 'tentative','version':versions.get(key),'evidence':signals[key]} for key in sorted(score,key=lambda k:(-score[k],k))]
    strong=[engine for engine in engines if engine['confidence']=='high']
    selected=strong[0] if len(strong)==1 else engines[0] if not strong and len(engines)==1 else None
    engine=selected or {'id':'multiple' if len(strong)>1 else 'unknown','name':'Несколько движков — выберите папку одной игры' if len(strong)>1 else 'Движок не определён','confidence':'conflict' if len(strong)>1 else 'unknown','version':None,'evidence':[]}
    supported=[item for item in candidates if item['supported']];auto=[]
    if engine['id']=='renpy' and engine['confidence']=='high':
        russian=[item for item in supported if item['format']=='Ren’Py script' and '/tl/russian/' in '/'+item['path'].lower()]
        auto=russian or [item for item in supported if item['format']=='Ren’Py script' and item['role']=='script' and (item['path'].lower().startswith('game/') or root.name.lower()=='game')]
    if engine['id']=='renpy':
        storage='Открытые .rpy, скомпилированные .rpyc и архивы .rpa. Содержимое архивов в этом анализе не извлекается.'
        replacement='Существующий game/tl/<язык> можно экспортировать отдельным .rpy. Для исходных сценариев сначала нужно создать штатный слой перевода Ren’Py; его генерация и установка пока не автоматизированы.'
        language='В просмотренных скриптах найден Language(...): '+', '.join(sorted({a['argument'] for a in actions})) if actions else 'В просмотренных фрагментах Language(...) не найден. Это не доказывает отсутствие переключателя: он может находиться дальше в файле или в архиве.'
        language+=' Для добавления нужен screen preferences и действия Language(None) / Language("russian"), затем проверка шрифта и запуска игры. Автоматическое добавление пока недоступно.'
    elif engine['id']=='unity':
        storage='Текст может быть в сценах, TextAsset, таблицах локализации, StreamingAssets или пакетах Unity. Найденные .assets / UnityFS требуют разбора объектов, а не замены всех бинарных строк.'
        replacement='Для данных Unity нужен отдельный адаптер и проверка версии/типа объекта. Сейчас можно импортировать подтверждённый внешний корпус; прямое изменение assets не реализовано.'
        language='Нужно определить существующую систему локализации и обработчик меню. Смена языка в готовой сборке зависит от игры; универсальное внедрение не реализовано.'
    elif engine['id']=='godot':
        storage='Открытые сценарии/сцены и таблицы переводов либо пакет PCK; извлечение PCK пока недоступно.'
        replacement='Нужен адаптер ресурсов и переводов Godot; сейчас доступен импорт внешнего корпуса, без изменения PCK.'
        language='Нужно проверить переводимые строки, ресурсы Translation и управление locale в этой игре. Автоматическое добавление меню не реализовано.'
    elif engine['id'] in {'rpgmv','rpgmz'}:
        storage='JSON в data / www/data: события карт, общие события и поля базы; JavaScript содержит системные надписи. Общий импорт всех JSON-строк захватит также ресурсы и идентификаторы.'
        replacement='Нужен адаптер команд событий и полей базы RPG Maker. Сейчас эти JSON не импортируются автоматически и не изменяются.'
        language='Переключение требует совместимого плагина/изменений загрузки данных и меню именно этой игры; не реализовано.'
    else:
        storage='Найдены отдельные кандидаты и контейнеры; расположение всего игрового текста ещё не подтверждено.'
        replacement='Подтверждённые корпуса source/translation и выбранные TXT можно импортировать. Для сценариев и архивов нужен отдельный адаптер; замена игровых файлов недоступна.'
        language='Система выбора языка не определена. Нужен разбор конкретного движка и меню.'
    return {'schema':1,'root':str(root),'at':now(),'engine':engine,'engines':engines,'scanned_files':count,'partial':stopped or incomplete or bool(errors) or sample_bytes>=8*1024*1024,'warnings':list(dict.fromkeys(warnings))[:30],'errors':errors,'candidates':candidates,'archives':archives,'languages':sorted(languages),'language_actions':actions,'preferences_screens':menus[:20],'auto_import':[item['path'] for item in auto],'storage':storage,'replacement':replacement,'language_switch':language,'capabilities':{'importable_files':len(supported),'archive_extraction':False,'game_write':False,'add_language_menu':False},'documentation':DOCS.get(engine['id']),'elapsed_seconds':round(time.monotonic()-started,2)}

def inspect_project(store,pid):
    with store.db() as db:project=db.execute('SELECT * FROM projects WHERE id=?',(pid,)).fetchone()
    if not project:raise ValueError('Проект не найден')
    report=analyze(project['root'])
    with store.db() as db:db.execute('INSERT OR REPLACE INTO project_analysis VALUES (?,?,?)',(pid,dump(report),report['at']))
    return report

def saved_report(store,pid):
    with store.db() as db:row=db.execute('SELECT report FROM project_analysis WHERE project=?',(pid,)).fetchone()
    return json.loads(row[0]) if row else None

def import_detected(store,pid,paths):
    report=saved_report(store,pid)
    if not report:raise ValueError('Сначала выполните анализ проекта')
    if not isinstance(paths,list) or len(paths)>3000 or any(not isinstance(path,str) for path in paths):raise ValueError('Некорректный список файлов')
    allowed={item['path'] for item in report['candidates'] if item['supported']};root=Path(report['root']).resolve();chosen=[]
    for relative in dict.fromkeys(paths):
        path=(root/relative).resolve()
        if relative not in allowed or not path.is_relative_to(root) or path.is_symlink():raise ValueError('Файл не входит в проверенные кандидаты проекта')
        if path.stat().st_size>8*1024*1024:raise ValueError('Размер файла изменился; повторите анализ')
        chosen.append(path)
    return store.import_files(pid,chosen)

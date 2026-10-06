"""Bounded literary fragments, exact separators, durable checkpoints and alignment."""
import hashlib,json,math,re
from core import Store,TOKEN,dump,validate

class OutputTruncated(ValueError):pass
class RequestTooLarge(ValueError):pass
class JobStopped(Exception):pass

def estimate_tokens(text):
    # Conservative heuristic for prose, not a claim to know a model's tokenizer.
    return math.ceil(len(text.encode('utf-8'))/2.5)+16

def digest(value):return hashlib.sha256(value.encode('utf-8')).hexdigest()

def split_source(text,limit):
    if limit<80:raise RequestTooLarge('После правил и контекста слишком мало места; увеличьте контекст модели')
    result=[]
    while len(text)>limit:
        protected=[(m.start(),m.end()) for m in TOKEN.finditer(text)]
        def safe(index):return not any(a<index<b for a,b in protected)
        candidates=[]
        for pattern in [r'\n\s*\n',r'(?<=[.!?。！？])\s+',r'\s+']:
            candidates=[m.end() for m in re.finditer(pattern,text[:limit+1]) if 0<m.end()<=limit and safe(m.end())]
            if candidates and max(candidates)>=limit*.35:break
        cut=max(candidates) if candidates else 0
        if not cut and safe(limit) and all('\u3040'<=c<='\u9fff' for c in text[max(0,limit-1):limit+1]):cut=limit
        if not cut:raise RequestTooLarge('Нельзя безопасно разделить длинное слово, тег или переменную; увеличьте контекст/лимит ответа')
        result.append(text[:cut]);text=text[cut:]
    if text:result.append(text)
    return result

def peel(text):
    prefix=text[:len(text)-len(text.lstrip())];body=text.strip();suffix=text[len(text.rstrip()):]
    return prefix,body,suffix

def compact(entry):
    result=dict(entry)
    for field in ('context_before','context_after'):
        rows=[]
        for line in entry.get(field,[]):
            if isinstance(line,str):line={'source':line}
            item=dict(line)
            if len(item.get('source',''))>300:item['source']=item['source'][:300];item['truncated_reference']=True
            rows.append(item)
        result[field]=rows
    result['confirmed_character_examples']=[dict(row) for row in entry.get('confirmed_character_examples',[]) if len(dump(row))<=1000][:4]
    return result

def fits(provider,entry,stage):
    checker=getattr(provider,'fits_request',None)
    if not checker:return True
    if stage=='translate':return checker('translate',[entry])
    check=dict(entry);check.pop('russian',None);check['original']=entry.get('russian','');check['candidate']=entry.get('russian','')
    return checker('critic',[entry]) and checker('verify',[check])

def process(provider,stage,entry,direct):
    output=int(getattr(provider,'settings',{}).get('max_output',1200))
    cap=max(80,min(1800,int(output*1.1)))
    prepared=compact(entry)
    small=dict(prepared);small['english']='';small['russian']=''
    if not fits(provider,prepared,stage):
        small.update(fragment_before=entry['english'][:250],fragment_after=entry['english'][-250:],fragment_notice='One fragment of the SAME record. Translate/check only english. Preserve voice and facts; do not import reference text or complete the following fragment.')
    # Discard distant references before attempting to fragment the target.
    while not fits(provider,small,stage):
        before=small.get('context_before',[]);after=small.get('context_after',[])
        if before and (not after or len(before)>=len(after)):before.pop(0)
        elif after:after.pop()
        elif small.get('confirmed_character_examples'):small['confirmed_character_examples'].pop(0)
        else:raise RequestTooLarge('Правила проекта не помещаются в контекст модели; увеличьте размер контекста')
    prepared['context_before']=small['context_before'];prepared['context_after']=small['context_after']
    prepared['confirmed_character_examples']=small['confirmed_character_examples']
    settings=getattr(provider,'settings',{});store=Store(settings['_studio_db']) if settings.get('_studio_db') else None
    source=entry['english'];draft=entry.get('russian','');manifest=None;saved_plan=None
    context_fingerprint={k:v for k,v in prepared.items() if k not in ('english','russian','validation_feedback')}
    config={k:settings.get(k) for k in ('model','cloud_model','endpoint','mcp_session','mcp_model_hint','source_language','target_language','context','max_output','context_before','context_after','marked_review','review_instruction','review_pass')}
    if settings.get('cache_namespace'):config['cache_namespace']=settings['cache_namespace']
    cache_prefix=dump([1,getattr(provider,'kind',''),stage,digest(source),digest(draft),context_fingerprint,config,getattr(provider,'rules',{}),getattr(provider,'profile',{}),getattr(provider,'policy','')])
    plan_key=digest(cache_prefix)
    def own_cache(key):
        if store and settings.get('_studio_project'):
            with store.db() as db:db.execute('INSERT OR IGNORE INTO cache_owners VALUES (?,?)',(settings['_studio_project'],key))
    own_cache(plan_key)
    if store and stage=='translate':
        with store.db() as db:saved=db.execute('SELECT parts FROM fragment_plans WHERE key=?',(plan_key,)).fetchone()
        if saved:
            saved_plan=json.loads(saved[0])
            if ''.join(p['source_raw'] for p in saved_plan)!=source:raise ValueError('Повреждён сохранённый план частей')
    if not saved_plan and max(len(entry['english']),len(entry.get('russian','')) if stage!='translate' else 0)<=cap and fits(provider,prepared,stage):
        try:return direct(provider,stage,prepared)
        except OutputTruncated:
            cap=max(80,cap//2)
    if store and stage!='translate':
        with store.db() as db:
            row=db.execute('SELECT parts FROM fragment_manifests WHERE record=? AND source_hash=? AND text_hash=?',(entry['id'],digest(source),digest(draft))).fetchone()
        if row:manifest=json.loads(row[0])
    if stage=='translate':plan=saved_plan or [{'source_raw':p} for p in split_source(source,cap)]
    elif manifest:plan=[{'source_raw':p['source_raw'],'text_raw':p['text_raw']} for p in manifest]
    else:
        source_lines=source.splitlines(keepends=True);draft_lines=draft.splitlines(keepends=True)
        if len(source_lines)!=len(draft_lines) or any(max(len(a),len(b))>cap for a,b in zip(source_lines,draft_lines)):
            raise RequestTooLarge('Длинный готовый перевод не имеет безопасного соответствия частей. Текст сохранён: увеличьте контекст/лимит ответа или разделите запись на совпадающие абзацы для проверки')
        plan=[{'source_raw':a,'text_raw':b} for a,b in zip(source_lines,draft_lines)]
    if store and stage=='translate' and not saved_plan:
        with store.db() as db:db.execute('INSERT OR REPLACE INTO fragment_plans VALUES (?,?)',(plan_key,dump(plan)))
    completed=[];reasons=[];index=0
    while index<len(plan):
        if store and settings.get('_studio_job'):
            with store.db() as db:job=db.execute('SELECT state FROM jobs WHERE id=?',(settings['_studio_job'],)).fetchone()
            if not job or job[0]!='running':raise JobStopped('Задача остановлена; готовые части сохранены в кэше')
        part=plan[index];raw=part['source_raw'];prefix,body,suffix=peel(raw)
        target=dict(prepared);target['english']=body;target['fragment_before']=source[:sum(len(p['source_raw']) for p in plan[:index])][-250:];target['fragment_after']=''.join(p['source_raw'] for p in plan[index+1:])[:250]
        if len(plan)==1:
            target.pop('fragment_before',None);target.pop('fragment_after',None)
        else:target['fragment_notice']='One fragment of the SAME record. Translate/check only english. Preserve voice and facts; do not import reference text or complete the following fragment.'
        if stage!='translate':
            draft_prefix,draft_body,draft_suffix=peel(part['text_raw']);target['russian']=draft_body
        key=digest(cache_prefix+dump([part,index,target.get('fragment_before',''),target.get('fragment_after','')]));cached=None
        own_cache(key)
        if store:
            with store.db() as db:r=db.execute('SELECT value FROM fragment_cache WHERE key=?',(key,)).fetchone()
            if r:cached=json.loads(r[0])
        try:
            if body:
                if not fits(provider,target,stage):raise RequestTooLarge('Фрагмент превышает бюджет запроса')
                if cached:text,reason=cached['text'],cached['reason']
                else:text,reason=direct(provider,stage,target)
                validate(body,text)
                rendered=(prefix if stage=='translate' else draft_prefix)+text+(suffix if stage=='translate' else draft_suffix)
            else:rendered=raw if stage=='translate' else part['text_raw'];text='';reason=''
        except (OutputTruncated,RequestTooLarge):
            if stage!='translate':raise RequestTooLarge('Часть готового перевода не помещается в запрос. Безопасное соответствие сохранено; увеличьте контекст или лимит ответа')
            smaller=split_source(raw,max(80,len(raw)//2))
            if len(smaller)<2:raise RequestTooLarge('Даже минимальный фрагмент не помещается в запрос; увеличьте контекст/лимит ответа')
            plan[index:index+1]=[{'source_raw':p} for p in smaller]
            if store:
                with store.db() as db:db.execute('INSERT OR REPLACE INTO fragment_plans VALUES (?,?)',(plan_key,dump(plan)))
            continue
        if store and body and not cached:
            with store.db() as db:db.execute('INSERT OR REPLACE INTO fragment_cache VALUES (?,?)',(key,dump({'text':text,'reason':reason})))
        completed.append({'source_raw':raw,'text_raw':rendered});reasons.append(reason);index+=1
        preview=''.join(p['text_raw'] for p in completed)
        if store and settings.get('_studio_job'):
            with store.db() as db:
                job=db.execute('SELECT state FROM jobs WHERE id=?',(settings['_studio_job'],)).fetchone()
                if not job or job[0]!='running':raise JobStopped('Задача остановлена; готовые части сохранены в кэше')
                db.execute('UPDATE jobs SET fragment_done=?,fragment_total=?,fragment_preview=? WHERE id=?',(index,len(plan),preview[-1500:],settings['_studio_job']))
        if getattr(provider,'progress',None):provider.progress(f'FRAGMENT #{entry["id"]} {index}/{len(plan)}'+(' | CACHE' if cached else ''))
    result=''.join(p['text_raw'] for p in completed);validate(source,result)
    if ''.join(p['source_raw'] for p in completed)!=source:raise ValueError('Нарушен порядок исходных частей')
    if store:
        with store.db() as db:db.execute('INSERT OR REPLACE INTO fragment_manifests VALUES (?,?,?,?)',(entry['id'],digest(source),digest(result),dump(completed)))
    return result,dump({'fragment_count':len(completed),'reasons':reasons})

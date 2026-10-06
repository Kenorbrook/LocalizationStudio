import json, tempfile, unittest
from pathlib import Path
from core import Store, extract, validate
from app import records, api, launch, job_log, state, queue_page, record_context, run_limits
from unittest.mock import patch
from providers import ServiceUnavailable, process_record
from worker import run
from mcp_server import handle, call

class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name); self.store=Store(self.root/'state.db'); self.project=self.store.project(str(self.root))['id']
    def tearDown(self): self.temp.cleanup()
    def corpus(self,rows):
        p=self.root/'corpus.json'; p.write_text(json.dumps(rows),encoding='utf-8'); result=self.store.import_files(self.project,[p]); self.assertFalse(result['errors'])
        with self.store.db() as db: return db.execute('SELECT id FROM files').fetchone()[0]
    def test_worker_launch_without_shell(self):
        self.corpus([{'source':'Hello'}]); jid=self.store.create_job(self.project,'translate','local',{})
        with patch('app.subprocess.Popen') as popen:
            popen.return_value.pid=1234
            launch(self.store,jid)
        with self.store.db() as db:self.assertEqual(db.execute('SELECT pid FROM jobs WHERE id=?',(jid,)).fetchone()[0],1234)
        args=popen.call_args.args[0]
        self.assertIn('--worker',args); self.assertNotIn('powershell.exe',args)
        self.assertEqual(popen.call_args.kwargs['creationflags'],0x08000000)
        self.assertEqual(popen.call_args.kwargs['stdin'],-3)
    def test_undo_saved_edit_and_revision(self):
        fid=self.corpus([{'source':'Hello','translation':'Привет'}]);r=records(self.store,fid)['rows'][0]
        saved=self.store.update(r['id'],r['revision'],'Здравствуйте','verified','human',manual=True)
        restored=self.store.undo(r['id'],saved['revision']);self.assertEqual(restored['text'],'Привет');self.assertEqual(restored['manual'],0)
        with self.assertRaises(ValueError):self.store.undo(r['id'],saved['revision'])
    def test_queue_blocks_and_project_isolation(self):
        fid=self.corpus([{'source':f'Line {i}'} for i in range(52)]);jid=self.store.create_job(self.project,'translate','local',{})
        self.assertEqual(len(queue_page(self.store,self.project)['rows']),50)
        with self.store.db() as db:
            db.execute("UPDATE queue SET state='done' WHERE job=? AND record IN (SELECT id FROM records WHERE position<50)",(jid,))
        page=queue_page(self.store,self.project);self.assertEqual(len(page['rows']),2);self.assertEqual(page['offset'],50)
        self.store.error(jid,page['rows'][0]['id'],'Test')
        other=self.root/'other';other.mkdir();other_id=self.store.project(str(other))['id']
        self.assertEqual(state(self.store,other_id)['errors'],0);self.assertEqual(queue_page(self.store,other_id)['rows'],[])
    def test_mcp_connection_identity(self):
        from mcp_server import connections
        handle(self.store,{'id':1,'method':'initialize','params':{'clientInfo':{'name':'Test client','version':'1.2'}}})
        clients=connections(self.store);self.assertEqual(clients[0]['client'],'Test client');self.assertIsNone(clients[0]['port'])
    def test_speaker_context_and_confirmed_examples(self):
        from speaker_context import context_entry
        fid=self.corpus([{'source':'I was ready.','translation':'Я была готова.','speaker':'a','scene':'one'}, {'source':'She was ready.','speaker':'b','scene':'one'}, {'source':'I am tired.','speaker':'a','scene':'one'}, {'source':'Other scene','speaker':'c','scene':'two'}])
        rows=records(self.store,fid)['rows'];self.store.update(rows[0]['id'],0,'Я была готова.','verified','human',manual=True)
        api(self.store,'speaker-profiles',{'project':self.project,'profiles':{'a':{'self_reference_gender':'женский','reference_gender':'женский','kind':'thought'}}})
        entry=context_entry(self.store,rows[2]['id'])
        self.assertEqual(entry['speaker_metadata']['profile']['self_reference_gender'],'женский')
        self.assertEqual(entry['context_before'][1]['speaker']['id'],'b');self.assertEqual(entry['context_after'],[])
        self.assertEqual(len(entry['confirmed_character_examples']),1)
        other=self.root/'other';other.mkdir();pid=self.store.project(str(other))['id'];self.assertNotIn('speaker_profiles',json.loads(state(self.store,pid)['projects'][-1]['settings']))
    def test_configurable_neighbors_use_originals_and_scene_boundaries(self):
        from speaker_context import context_entry
        fid=self.corpus([{'source':f'Original {i}','translation':'Перевод','scene':'one' if i%2==0 else 'other','speaker':'Pilot'} for i in range(61)])
        with self.store.db() as db:rid=db.execute('SELECT id FROM records WHERE position=30 AND file=?',(fid,)).fetchone()[0]
        e=context_entry(self.store,rid,settings={})
        self.assertEqual(len(e['context_before']),12);self.assertEqual(len(e['context_after']),8)
        self.assertEqual(e['context_before'][0]['source'],'Original 6');self.assertEqual(e['context_after'][-1]['source'],'Original 46')
        self.assertTrue(all('translation' not in r and 'Перевод' not in r['source'] for r in e['context_before']))
        disabled=context_entry(self.store,rid,settings={'context_before':0,'context_after':0});self.assertEqual(disabled['context_before'],[]);self.assertEqual(disabled['context_after'],[])
        small=context_entry(self.store,rid,settings={'context_before':2,'context_after':1});self.assertEqual([r['source'] for r in small['context_before']],['Original 26','Original 28']);self.assertEqual(small['context_after'][0]['source'],'Original 32')
    def test_neighbor_validation_and_project_persistence(self):
        from speaker_context import neighbor_settings
        self.assertEqual(neighbor_settings({}),{'context_before':12,'context_after':8})
        for value in [-1,101,2.5,None,float('nan'),float('inf'),'word']:
            with self.assertRaises(ValueError):neighbor_settings({'context_before':value})
        api(self.store,'settings',{'project':self.project,'settings':{'context_before':20,'context_after':3}})
        with self.store.db() as db:s=json.loads(db.execute('SELECT settings FROM projects WHERE id=?',(self.project,)).fetchone()[0])
        self.assertEqual(s['context_before'],20);self.assertEqual(s['context_after'],3)
    def test_resume_updates_neighbors_without_changing_model(self):
        self.corpus([{'source':'Hello'}]);jid=self.store.create_job(self.project,'translate','local',{'model':'Original','context':8192,'context_before':4,'context_after':4})
        with self.store.db() as db:db.execute("UPDATE jobs SET state='paused' WHERE id=?",(jid,))
        with patch('app.launch'):api(self.store,'control',{'id':jid,'mode':'resume','neighbors':{'context_before':12,'context_after':8,'model':'Unwanted'}})
        with self.store.db() as db:s=json.loads(db.execute('SELECT settings FROM jobs WHERE id=?',(jid,)).fetchone()[0])
        self.assertEqual(s['context_before'],12);self.assertEqual(s['context_after'],8);self.assertEqual(s['model'],'Original');self.assertEqual(s['context'],8192)
    def test_worker_uses_job_neighbor_settings(self):
        self.corpus([{'source':f'Line {i}'} for i in range(10)])
        jid=self.store.create_job(self.project,'translate','local',{'context_before':2,'context_after':1,'run_lines':1})
        class Translator:
            def __init__(self,*args):pass
        with patch('worker.process_record',return_value=('Привет','')) as process:run(self.store,jid,Translator)
        e=process.call_args.args[2];self.assertEqual(e['context_before'],[]);self.assertEqual(len(e['context_after']),1)
    def test_unknown_narration_does_not_inherit_protagonist(self):
        from speaker_context import speaker_info
        info=speaker_info({'speaker':'','kind':'renpy'},{'protagonist':{'gender':'мужчина'}})
        self.assertEqual(info['profile'],{});self.assertEqual(info['kind'],'narration_or_thought_unspecified')
        info=speaker_info({'speaker':'mi happy','kind':'renpy'},{'speakers':{'mi':{'gender':'мужчина'}}},{'mi':{'self_reference_gender':'женский'}})
        self.assertEqual(info['id'],'mi');self.assertEqual(info['profile']['gender'],'мужчина');self.assertEqual(info['profile']['self_reference_gender'],'женский')
    def test_subtitle_metadata_in_json_and_csv(self):
        path=self.root/'captions.json';path.write_text(json.dumps([{'source':'Hello','actor':'Pilot','line_type':'thought'}]),encoding='utf-8')
        rows,_,_=extract(path);self.assertEqual(rows[0]['speaker'],'Pilot');self.assertEqual(rows[0]['locator']['utterance_kind'],'thought')
        path=self.root/'captions.csv';path.write_text('source,character,line_type\nHello,Pilot,dialogue\n',encoding='utf-8')
        rows,_,_=extract(path);self.assertEqual(rows[0]['speaker'],'Pilot')
    def test_human_context_location_and_scene(self):
        fid=self.corpus([{'source':'First','speaker':'a','scene':'one'},{'source':'Target','speaker':'b','scene':'one'},{'source':'Other','speaker':'c','scene':'two'}]);r=records(self.store,fid)['rows'][1]
        context=record_context(self.store,r['id']);self.assertEqual(len(context['rows']),2);self.assertEqual(context['location']['speaker'],'b');self.assertEqual(context['location']['json_path'],[1])
        self.assertEqual(context['before_count'],1);self.assertEqual(context['after_count'],0);self.assertEqual(context['order'],'file_position')
    def test_context_empty_range_is_not_start_of_scene(self):
        rows=[{'source':'Earlier','scene':'one'}]+[{'source':f'Other {i}','scene':'two'} for i in range(15)]+[{'source':'Selected','scene':'one'}]
        fid=self.corpus(rows);target=records(self.store,fid)['rows'][-1]
        context=record_context(self.store,target['id'],10)
        self.assertEqual(len(context['rows']),1);self.assertEqual(context['before_count'],1)
        first=record_context(self.store,records(self.store,fid)['rows'][0]['id'],10);self.assertEqual(first['before_count'],0);self.assertEqual(first['after_count'],1)
    def test_run_line_limit_and_resume(self):
        self.corpus([{'source':'Hello'},{'source':'Goodbye'}])
        class Translator:
            def __init__(self,*a):pass
            def call(self,mode,entries):return {'items':[{'id':entries[0]['id'],'text':'Привет'}]}
        jid=self.store.create_job(self.project,'translate','local',{'run_lines':1});run(self.store,jid,Translator)
        with self.store.db() as db:
            job=db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone();self.assertEqual(job['state'],'paused');self.assertEqual(job['done'],1)
        run(self.store,jid,Translator)
        with self.store.db() as db:self.assertEqual(db.execute('SELECT state FROM jobs WHERE id=?',(jid,)).fetchone()[0],'done')
    def test_run_time_limit(self):
        self.corpus([{'source':'Hello'}])
        class Translator:
            def __init__(self,*a):pass
            def call(self,*a):raise AssertionError('Time limit must be checked before first record')
        jid=self.store.create_job(self.project,'translate','local',{'run_minutes':1})
        with patch('worker.time.monotonic',side_effect=[0,61]):run(self.store,jid,Translator)
        with self.store.db() as db:self.assertEqual(db.execute('SELECT done,state FROM jobs WHERE id=?',(jid,)).fetchone()[0],0)
    def test_run_limit_validation(self):
        self.assertEqual(run_limits({'run_lines':200})['run_lines'],200)
        for value in [-1,float('nan'),float('inf'),1.5]:
            with self.assertRaises(ValueError):run_limits({'run_lines':value})
    def test_resume_changes_limits_only(self):
        self.corpus([{'source':'Hello'}]);jid=self.store.create_job(self.project,'translate','local',{'model':'Original','source_language':'English','run_lines':10})
        with self.store.db() as db:db.execute("UPDATE jobs SET state='paused' WHERE id=?",(jid,))
        with patch('app.launch'):api(self.store,'control',{'id':jid,'mode':'resume','limits':{'run_lines':200,'run_minutes':0}})
        with self.store.db() as db:s=json.loads(db.execute('SELECT settings FROM jobs WHERE id=?',(jid,)).fetchone()[0])
        self.assertEqual(s['run_lines'],200);self.assertEqual(s['model'],'Original')
    def test_mcp_job_rejects_read_only_client(self):
        self.corpus([{'source':'Hello'}]);handle(self.store,{'id':1,'method':'initialize','params':{'clientInfo':{'name':'Read only'}}})
        from mcp_server import SESSION
        with self.assertRaisesRegex(ValueError,'sampling'):api(self.store,'job',{'project':self.project,'stage':'translate','provider':'mcp','settings':{'mcp_session':SESSION,'source_language':'English','target_language':'Russian'}})
    def test_job_log_tail_unicode(self):
        self.corpus([{'source':'Hello'}]); jid=self.store.create_job(self.project,'translate','local',{})
        folder=self.root/'translation_tools'/'studio';folder.mkdir(parents=True)
        (folder/f'job-{jid}.log').write_text('\n'.join(f'{i}: Перевод' for i in range(100)),encoding='utf-8')
        lines=job_log(self.store,jid)['text'].splitlines()
        self.assertEqual(len(lines),80);self.assertEqual(lines[-1],'99: Перевод')
    def test_percentage_and_placeholders(self):
        validate('100% lies. %s [name] {b}Hi{/b}','100% ложь. %s [name] {b}Привет{/b}')
        with self.assertRaises(ValueError): validate('Hello [name]','Привет')
        with self.assertRaises(ValueError): validate('Open the door','')
    def test_renpy_resource_filter_and_export(self):
        p=self.root/'game'/'tl'/'russian'/'scene.rpy'; p.parent.mkdir(parents=True)
        source='translate russian original_id:\n    # voice "audio.ogg"\n    voice "audio.ogg"\n    # e "Hello [name]"\n    e ""\ntranslate russian strings:\n    old "Back"\n    new "Назад"\n'
        p.write_text(source,encoding='utf-8'); result=self.store.import_files(self.project,[p]); self.assertEqual(result['added'],2)
        with self.store.db() as db: fid=db.execute('SELECT id FROM files').fetchone()[0]
        r=records(self.store,fid)['rows'][0]; self.store.update(r['id'],0,'Привет [name]','translated','human',manual=True)
        dest=self.root/'out.rpy'; self.store.export(fid,dest); exported=dest.read_text(encoding='utf-8'); self.assertIn('original_id:',exported); self.assertIn('voice "audio.ogg"',exported); self.assertIn('e "Привет [name]"',exported); self.assertEqual(p.read_text(encoding='utf-8'),source)
    def test_original_source_filter(self):
        p=self.root/'script.rpy'; p.write_text('label start:\n    voice "audio.ogg"\n    scene bg bedroom\n    e "Hello"\n    "Goodbye"\n    $ x = "technical"\n',encoding='utf-8'); r,_,_=extract(p); self.assertEqual([x['source'] for x in r],['Hello','Goodbye'])
    def test_source_scene_context(self):
        game=self.root/'game'; game.mkdir(); (game/'story.rpy').write_text('label opening:\n    e "First"\n    e "Second"\nlabel ending:\n    e "Last"\n',encoding='utf-8')
        tl=game/'tl'/'russian'/'story.rpy'; tl.parent.mkdir(parents=True); tl.write_text('translate russian first_id:\n    # game/story.rpy:2\n    # e "First"\n    e ""\ntranslate russian second_id:\n    # game/story.rpy:3\n    # e "Second"\n    e ""\ntranslate russian last_id:\n    # game/story.rpy:5\n    # e "Last"\n    e ""\n',encoding='utf-8')
        rs,_,_=extract(tl); self.assertEqual(rs[0]['scene'],rs[1]['scene']); self.assertNotEqual(rs[1]['scene'],rs[2]['scene'])
    def test_cloud_review_counts_current_version(self):
        fid=self.corpus([{'source':'Hi','translation':'Привет'}])
        class Reviewer:
            def __init__(self,*a): pass
            def call(self,mode,e): return {'items':[{'id':e[0]['id'],'decision':'keep'}]}
        jid=self.store.create_job(self.project,'cloud','cloud',{}); run(self.store,jid,Reviewer)
        r=records(self.store,fid)['rows'][0]; self.assertEqual(r['status'],'verified')
        changed=self.store.update(r['id'],r['revision'],'Здравствуйте','translated','human',manual=True); self.assertEqual(changed['status'],'translated')
    def test_manual_revision_and_reimport(self):
        fid=self.corpus([{'source':'Hello','translation':'Привет'}]); r=records(self.store,fid)['rows'][0]
        edited=self.store.update(r['id'],0,'Здравствуйте','verified','human',manual=True); self.assertEqual(edited['status'],'verified')
        with self.assertRaises(ValueError): self.store.update(r['id'],0,'Поздний ответ','translated','local')
        new=self.store.update(r['id'],1,'Привет!','translated','human',manual=True); self.assertEqual(new['status'],'translated'); self.assertEqual(new['manual'],1)
        again=self.store.import_files(self.project,[self.root/'corpus.json']); self.assertEqual(again['added'],0); self.assertEqual(self.store.record(r['id'])['text'],'Привет!')
    def test_error_does_not_stop_job(self):
        fid=self.corpus([{'source':'Broken [name]'},{'source':'Hello'}])
        class Fake:
            def __init__(self,*a): pass
            def call(self,mode,entries):
                e=entries[0]; return {'items':[{'id':e['id'],'text':'Ошибка' if '[name]' in e['english'] else 'Привет'}]}
        jid=self.store.create_job(self.project,'translate','local',{})
        run(self.store,jid,Fake)
        rs=records(self.store,fid)['rows']; self.assertEqual(rs[0]['status'],'empty'); self.assertEqual(rs[1]['text'],'Привет')
        with self.store.db() as db:
            j=db.execute('SELECT * FROM jobs').fetchone(); self.assertEqual(j['state'],'incomplete'); self.assertEqual(j['done'],2); self.assertEqual(db.execute('SELECT count(*) FROM errors WHERE resolved=0').fetchone()[0],1)
        retry=self.store.create_job(self.project,'translate','local',{},retry=True)
        with self.store.db() as db: self.assertEqual(db.execute('SELECT total FROM jobs WHERE id=?',(retry,)).fetchone()[0],1)
    def test_network_pause_resume(self):
        fid=self.corpus([{'source':'Hello'}])
        class Offline:
            def __init__(self,*a): pass
            def call(self,*a): raise ServiceUnavailable('offline')
        jid=self.store.create_job(self.project,'translate','local',{}); run(self.store,jid,Offline)
        with self.store.db() as db: self.assertEqual(db.execute('SELECT state FROM jobs').fetchone()[0],'paused'); self.assertEqual(db.execute('SELECT state FROM queue').fetchone()[0],'pending')
        class Online:
            def __init__(self,*a): pass
            def call(self,mode,e): return {'items':[{'id':e[0]['id'],'text':'Привет'}]}
        run(self.store,jid,Online); self.assertEqual(records(self.store,fid)['rows'][0]['text'],'Привет')
    def test_review_uncertain_never_green(self):
        class Fake:
            def call(self,mode,e): return {'items':[{'id':e[0]['id'],'decision':'uncertain','reason':'unknown'}]}
        with self.assertRaises(ValueError): process_record(Fake(),'cloud',{'id':1,'english':'Hi','russian':'Привет'})
    def test_review_keep_and_repair(self):
        class Keep:
            def call(self,mode,e): return {'items':[{'id':1,'decision':'keep'}]}
        self.assertEqual(process_record(Keep(),'review',{'id':1,'english':'Hi','russian':'Привет'})[0],'Привет')
        class Repair:
            def call(self,mode,e):
                return {'items':[{'id':1,**({'decision':'repair','error_type':'meaning','evidence':'Hi','reason':'Wrong greeting'} if mode=='critic' else {'text':'Привет'} if mode=='translate' else {'winner':'candidate','reason':'Correct greeting'})}]}
        self.assertEqual(process_record(Repair(),'review',{'id':1,'english':'Hi','russian':'Пока'})[0],'Привет')
    def test_pagination_and_extra_import(self):
        fid=self.corpus([{'source':str(i)} for i in range(127)]); page=records(self.store,fid,50); self.assertEqual(len(page['rows']),50); self.assertEqual(page['rows'][0]['source'],'50'); self.assertEqual(page['total'],127)
        extra=self.root/'extra.txt'; extra.write_text('New text\nSecond line',encoding='utf-8'); self.assertEqual(self.store.import_files(self.project,[extra])['added'],2)
    def test_mcp_proposals_require_acceptance(self):
        fid=self.corpus([{'source':'Hi'}]); r=records(self.store,fid)['rows'][0]
        init=handle(self.store,{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18'}}); self.assertIn('tools',init['result']['capabilities'])
        proposed=call(self.store,'propose_translation',{'id':r['id'],'revision':0,'text':'Привет','reason':'Meaning checked','reviewer':'test-cloud'})
        self.assertEqual(self.store.record(r['id'])['status'],'empty')
        api(self.store,'proposal',{'id':proposed['proposal'],'accept':True,'verified':True}); self.assertEqual(self.store.record(r['id'])['status'],'verified')

if __name__=='__main__': unittest.main()

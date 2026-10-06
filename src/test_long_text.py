import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from core import Store,validate
from long_text import process,split_source,OutputTruncated,RequestTooLarge,compact
from providers import Provider
from worker import run

class FakeProvider:
    def __init__(self,settings=None):self.settings={'max_output':100,**(settings or {})};self.calls=[]
    def fits_request(self,mode,entries):return True

class LongTextTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.store=Store(self.root/'test.db')
        self.project=self.store.project(str(self.root))['id']
    def tearDown(self):self.temp.cleanup()
    def entry(self,source):return {'id':1,'english':source,'russian':'','context_before':[],'context_after':[]}
    def provider(self):return FakeProvider({'_studio_db':str(self.store.path),'target_language':'English'})
    def direct(self,p,stage,e):
        p.calls.append(e['english']);return e['english'].replace('quiet','silent'),'checked'
    def test_exact_boundaries_and_protected_tokens(self):
        source=('A quiet story with [player_name] and {b}bold{/b} words.\n\n'*12)+'Last sentence.'
        parts=split_source(source,110);self.assertEqual(''.join(parts),source)
        self.assertTrue(all(len(p)<=110 for p in parts));validate(source,''.join(parts))
        self.assertTrue(all('[player_' not in p or '[player_name]' in p for p in parts))
    def test_long_indivisible_token_is_error(self):
        with self.assertRaises(RequestTooLarge):split_source('['+'variable_'*40+']',100)
    def test_short_record_does_not_spend_budget_on_fragment_metadata(self):
        p=FakeProvider({'max_output':1200})
        p.fits_request=lambda mode,entries:not any('fragment_notice' in e for e in entries)
        text,_=process(p,'translate',self.entry('A quiet evening.'),self.direct)
        self.assertEqual(text,'A silent evening.')
    def test_resume_uses_validated_fragment_cache(self):
        e=self.entry(('A quiet evening passed. The house was quiet.\n\n'*12));p=self.provider()
        def interrupted(p,stage,e):
            if len(p.calls)==2:raise ValueError('Synthetic interruption')
            return self.direct(p,stage,e)
        with self.assertRaises(ValueError):process(p,'translate',e,interrupted)
        p.calls=[];text,reason=process(p,'translate',e,self.direct)
        self.assertEqual(text,e['english'].replace('quiet','silent'))
        count=json.loads(reason)['fragment_count'];self.assertEqual(len(p.calls),count-2)
        with self.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM fragment_manifests').fetchone()[0],1)
    def test_full_source_invalidates_contextual_cache(self):
        e=self.entry('A quiet evening passed. '*20);p=self.provider();process(p,'translate',e,self.direct)
        p.calls=[];e['english']+=' New ending.';process(p,'translate',e,self.direct);self.assertGreater(len(p.calls),3)
    def test_truncated_output_retries_smaller_without_accepting_partial(self):
        e=self.entry('A quiet evening passed. '*20);p=FakeProvider({'max_output':1000})
        def bounded(p,stage,e):
            p.calls.append(e['english'])
            if len(e['english'])>120:raise OutputTruncated('Synthetic max tokens')
            return e['english'].replace('quiet','silent'),''
        text,reason=process(p,'translate',e,bounded);self.assertEqual(text,e['english'].replace('quiet','silent'))
        self.assertGreater(json.loads(reason)['fragment_count'],1)
    def test_review_uses_manifest_and_preserves_paragraphs(self):
        e=self.entry('A quiet evening passed.\n\n'*20);p=self.provider();text,_=process(p,'translate',e,self.direct)
        e['russian']=text;p.calls=[]
        def review(p,stage,item):p.calls.append(item['english']);return item['russian'],''
        revised,_=process(p,'review',e,review);self.assertEqual(revised,text);self.assertGreater(len(p.calls),1)
    def test_resume_preserves_adaptive_split_plan(self):
        e=self.entry('A quiet evening passed. '*30);p=self.provider();p.settings['max_output']=1000;accepted=[]
        def bounded(p,stage,item):
            if len(item['english'])>120:raise OutputTruncated('Synthetic truncation')
            if len(accepted)==2:raise ValueError('Synthetic outage')
            accepted.append(item['english']);return self.direct(p,stage,item)
        with self.assertRaises(ValueError):process(p,'translate',e,bounded)
        p.calls=[]
        def resumed(p,stage,item):
            if len(item['english'])>120:raise OutputTruncated('Synthetic truncation')
            return self.direct(p,stage,item)
        text,reason=process(p,'translate',e,resumed)
        self.assertEqual(text,e['english'].replace('quiet','silent'))
        self.assertEqual(len(p.calls),json.loads(reason)['fragment_count']-2)
    def test_review_instruction_and_new_pass_invalidate_fragment_cache(self):
        e=self.entry('A quiet evening passed. '*20);p=self.provider();text,_=process(p,'translate',e,self.direct);e['russian']=text
        p.settings.update(marked_review='bad',review_instruction='Check meaning.',review_pass='one')
        def review(p,stage,item):p.calls.append(item['english']);return item['russian'],''
        p.calls=[];process(p,'review',e,review);self.assertGreater(len(p.calls),1)
        p.calls=[];process(p,'review',e,review);self.assertEqual(p.calls,[])
        p.settings['review_instruction']='Check grammar.';process(p,'review',e,review);self.assertGreater(len(p.calls),1)
        p.calls=[];p.settings['review_pass']='two';process(p,'review',e,review);self.assertGreater(len(p.calls),1)
    def test_imported_long_review_without_alignment_preserves_text(self):
        e=self.entry('A quiet evening passed. '*20);e['russian']='Тихий вечер прошёл. '*20
        with self.assertRaisesRegex(RequestTooLarge,'соответствия'):process(self.provider(),'review',e,self.direct)
        self.assertEqual(e['russian'],'Тихий вечер прошёл. '*20)
    def test_pause_inside_fragment_keeps_queue_pending_and_checkpoint(self):
        source='A quiet evening passed. '*20;corpus=self.root/'corpus.json';corpus.write_text(json.dumps([{'source':source}]),encoding='utf-8');self.store.import_files(self.project,[corpus])
        jid=self.store.create_job(self.project,'translate','local',{'max_output':100,'target_language':'English'})
        store=self.store
        class PausingProvider(FakeProvider):
            def __init__(self,project,kind,settings):super().__init__(settings)
            def call(self,mode,items):
                with store.db() as db:db.execute("UPDATE jobs SET state='paused' WHERE id=?",(jid,))
                return {'items':[{'id':items[0]['id'],'text':items[0]['english']}]}
        run(self.store,jid,PausingProvider)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT state FROM queue').fetchone()[0],'pending')
            self.assertEqual(db.execute('SELECT count(*) FROM errors').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM fragment_cache').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT text FROM records').fetchone()[0],'')
    def test_confirmed_character_examples_not_removed(self):
        e=self.entry('Hello.');e['confirmed_character_examples']=[{'source':'I was ready.','text':'Я была готова.'}]
        self.assertEqual(len(compact(e)['confirmed_character_examples']),1)
    def test_large_passage_is_never_sent_over_request_budget(self):
        e=self.entry('A quiet evening passed.\n\n'*1000);p=FakeProvider({'max_output':1200})
        p.fits_request=lambda mode,items:not items or len(items[0].get('english',''))<=500
        def bounded(p,stage,item):
            self.assertLessEqual(len(item['english']),500)
            return self.direct(p,stage,item)
        text,reason=process(p,'translate',e,bounded)
        self.assertEqual(text,e['english'].replace('quiet','silent'));self.assertGreater(json.loads(reason)['fragment_count'],50)
    def test_expanded_neighbors_trim_distant_sources_to_fit(self):
        e=self.entry('Hello.');e['context_before']=[{'source':f'Before {i}'} for i in range(12)];e['context_after']=[{'source':f'After {i}'} for i in range(8)]
        p=FakeProvider();p.fits_request=lambda mode,entries:len(entries[0].get('context_before',[]))+len(entries[0].get('context_after',[]))<=6
        captured=[]
        def direct(p,stage,item):captured.append(item);return item['english'],''
        process(p,'translate',e,direct);self.assertEqual([r['source'] for r in captured[0]['context_before']],['Before 9','Before 10','Before 11']);self.assertEqual([r['source'] for r in captured[0]['context_after']],['After 0','After 1','After 2'])
    def test_provider_rejects_truncation_flag(self):
        p=Provider({'id':self.project,'root':str(self.root),'settings':'{}'},'cloud',{'context':8192,'max_output':1000})
        with patch('providers.secret',return_value='fixture'),patch('providers.json_request',return_value={'choices':[{'message':{'content':'{"items":[]}'},'finish_reason':'length'}]}):
            p.settings['endpoint']='https://fixture.invalid/v1'
            with self.assertRaises(OutputTruncated):p.call('translate',[self.entry('Hello.')])

if __name__=='__main__':unittest.main()

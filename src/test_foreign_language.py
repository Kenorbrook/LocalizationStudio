import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from core import Store
from foreign_language import detect,check_record
from worker import run

class ForeignLanguageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.store=Store(self.root/'test.db');self.pid=self.store.project(str(self.root))['id']
    def tearDown(self):self.temp.cleanup()
    def corpus(self,source):
        path=self.root/'data.json';path.write_text(json.dumps([{'source':source}]),encoding='utf-8');self.store.import_files(self.pid,[path]);return self.store.record(1)
    def test_real_offline_french_detection(self):
        with patch('socket.create_connection',side_effect=AssertionError('Must work offline')):
            result=detect('Bonjour, je suis très heureux de vous rencontrer et de parler avec vous aujourd’hui.','English')
        self.assertEqual(result['language'],'FRENCH');self.assertEqual(result['action'],'preserve')
    def test_real_offline_latin_detection(self):
        result=detect('Gallia est omnis divisa in partes tres, quarum unam incolunt Belgae.','English')
        self.assertEqual(result['language'],'LATIN');self.assertEqual(result['action'],'preserve')
    def test_english_names_and_loanwords_are_not_preserved(self):
        for text in ['John','John Michael Smith','Bonjour','Hello','He said bonjour and left the room.','The hotel restaurant is open for dinner.','A quiet evening passed.']:
            self.assertNotEqual(detect(text,'English')['action'],'preserve')
    def test_short_english_stage_directions_and_names_do_not_trigger_review(self):
        for text in ['(breathing heavily)',"Saeki's words startle me, too, for a moment.","Saeki’s words startle me, too, for a moment.",'John Michael Smith','(looking at him)','Minase Kitami','Hello there']:
            self.assertEqual(detect(text,'English')['action'],'none',text)
    def test_refresh_only_clears_proven_automatic_marks(self):
        from foreign_language import refresh_language_reviews
        self.corpus('(breathing heavily)');row=self.store.mark(1,0,'review',origin='language')
        with self.store.db() as db:db.execute('INSERT INTO language_checks VALUES (?,?,?,?)',(1,'old','English',json.dumps({'action':'review','language':'TAGALOG','reason':'old'})))
        result=refresh_language_reviews(self.store);self.assertEqual(result['removed_auto_marks'],1);self.assertEqual(self.store.record(1)['flag'],'');self.assertEqual(self.store.record(1)['language_note'],'')
    def test_refresh_preserves_human_and_unknown_marks(self):
        from foreign_language import refresh_language_reviews
        self.corpus('(breathing heavily)');self.store.mark(1,0,'review')
        for origin in ['human','unknown']:
            with self.store.db() as db:
                db.execute('UPDATE record_marks SET origin=?',(origin,));db.execute('INSERT OR REPLACE INTO language_checks VALUES (?,?,?,?)',(1,'old','English',json.dumps({'action':'review','reason':'old'})))
            result=refresh_language_reviews(self.store);self.assertEqual(result['removed_auto_marks'],0);self.assertEqual(self.store.record(1)['flag'],'review');self.assertEqual(self.store.record(1)['language_note'],'')
    def test_short_latin_is_marked_for_human_review(self):
        result=detect('Veni, vidi, vici.','English');self.assertEqual(result['language'],'LATIN');self.assertEqual(result['action'],'review')
    def test_mixed_english_and_french_is_not_preserved_wholesale(self):
        result=detect('I heard her say: Bonjour, je suis très heureux de vous rencontrer et de parler avec vous aujourd’hui. Then she left.','English')
        self.assertNotEqual(result['action'],'preserve')
    def test_project_main_language_not_preserved(self):
        text='Bonjour, je suis très heureux de vous rencontrer et de parler avec vous aujourd’hui.'
        self.assertEqual(detect(text,'French')['action'],'none')
    def test_foreign_worker_does_not_call_translation_model_and_counts_once(self):
        self.corpus('Bonjour, je suis très heureux de vous rencontrer.');jid=self.store.create_job(self.pid,'translate','local',{'source_language':'English'})
        class Forbidden:
            def __init__(self,*args):pass
            def call(self,*args):raise AssertionError('Foreign phrase must not be generated')
        result={'action':'preserve','language':'FRENCH','score':1,'reason':'Другой язык: французский. Оригинал сохранён.'}
        with patch('foreign_language.detect',return_value=result):run(self.store,jid,Forbidden)
        row=self.store.record(1);self.assertEqual(row['text'],row['source']);self.assertEqual(row['status'],'preserved');self.assertIn('французский',row['preserve_reason'])
        with self.store.db() as db:self.assertEqual(tuple(db.execute('SELECT done,total,state FROM jobs WHERE id=?',(jid,)).fetchone()),(1,1,'done'))
    def test_uncertain_flag_is_not_readded_after_manual_clear(self):
        self.corpus('Carpe diem.');result={'action':'review','language':'LATIN','score':.7,'reason':'Возможно латынь; проверить вручную'}
        with patch('foreign_language.detect',return_value=result) as detector:
            row=check_record(self.store,1,{});self.assertEqual(row['flag'],'review');self.assertIn('латынь',row['language_note'])
            self.store.mark(1,row['revision'],'');self.assertEqual(check_record(self.store,1,{})['flag'],'');self.assertEqual(detector.call_count,1)
    def test_disabled_and_restored_originals_are_not_reclassified(self):
        row=self.corpus('Carpe diem.')
        with patch('foreign_language.detect',side_effect=AssertionError('Disabled')):check_record(self.store,1,{'auto_foreign':False})
        row=self.store.preserve(1,row['revision'],'foreign','Авторский приём');self.store.restore_preserved(1,row['revision'])
        with patch('foreign_language.detect',side_effect=AssertionError('Explicit user override')):check_record(self.store,1,{})
    def test_already_translated_text_is_not_overwritten_by_detector(self):
        r=self.corpus('Carpe diem.');self.store.update(1,r['revision'],'Лови момент.','translated','local')
        with patch('foreign_language.detect',side_effect=AssertionError('Existing translation')):check_record(self.store,1,{})
        self.assertEqual(self.store.record(1)['text'],'Лови момент.')

if __name__=='__main__':unittest.main()

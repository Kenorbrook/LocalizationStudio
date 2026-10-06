import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from core import Store
from app import api
from preservation import folder
from process_view import queue_page

class PreservedWorkflowTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=Store(self.root/'test.db');self.pid=self.store.project(str(self.root))['id']
  p=self.root/'a.json';p.write_text(json.dumps([{'source':'Hello.'},{'source':'Goodbye.'},{'source':'A quiet evening.'}]))
  self.store.import_files(self.pid,[p]);self.settings={'model':'fixture','source_language':'English','target_language':'Russian','context':8192,'auto_foreign':False}
 def tearDown(self):self.tmp.cleanup()
 def preserve(self,rid):
  r=self.store.record(rid);return self.store.preserve(rid,r['revision'],'foreign','Author choice')
 def send(self,r,mode,**extra):
  with patch('app.launch') as launch:
   result=api(self.store,'translate-preserved',{'project':self.pid,'id':r['id'],'revision':r['revision'],'mode':mode,'provider':'local','settings':self.settings,**extra})
  return result,launch
 def test_front_then_end_match_preview_and_worker(self):
  jid=self.store.create_job(self.pid,'translate','local',self.settings);first=self.preserve(2);last=self.preserve(3)
  self.send(first,'front');self.send(last,'end')
  self.assertEqual([r['id'] for r in queue_page(self.store,self.pid)['rows']],[2,1,3])
  with self.store.db() as db:self.assertEqual(tuple(db.execute('SELECT done,total FROM jobs WHERE id=?',(jid,)).fetchone()),(0,3))
  from worker import run
  class Fake:
   def __init__(self,*a):self.calls=0
  seen=[]
  def translate(provider,stage,entry):seen.append(entry['id']);return ('Тест.','')
  with patch('worker.process_record',side_effect=translate):run(self.store,jid,Fake)
  self.assertEqual(seen,[2,1,3])
 def test_new_job_only_selected_record(self):
  r=self.preserve(2);result,launch=self.send(r,'end');launch.assert_called_once_with(self.store,result['job'])
  self.assertEqual([r['id'] for r in queue_page(self.store,self.pid)['rows']],[2])
 def test_append_record_missing_from_queue_and_paused_stays_paused(self):
  r=self.preserve(2);jid=self.store.create_job(self.pid,'translate','local',self.settings)
  with self.store.db() as db:db.execute("UPDATE jobs SET state='paused' WHERE id=?",(jid,))
  result,launch=self.send(r,'end');self.assertTrue(result['paused']);launch.assert_not_called()
  self.assertEqual([r['id'] for r in queue_page(self.store,self.pid)['rows']],[1,3,2])
  with self.store.db() as db:self.assertEqual(tuple(db.execute('SELECT done,total FROM jobs').fetchone()),(0,3))
 def test_manual_save_keeps_flags_and_protects_text(self):
  marked=self.store.mark(2,0,'bad');r=self.preserve(2);result,launch=self.send(r,'manual',text='Ручной перевод.')
  launch.assert_not_called();r=self.store.record(2);self.assertEqual((r['text'],r['manual'],r['status'],r['flag'],r['preserve_kind']),('Ручной перевод.',1,'translated','bad',''))
 def test_invalid_or_stale_manual_save_is_atomic(self):
  r=self.preserve(2)
  with self.assertRaises(ValueError):self.send(r,'manual',text='')
  self.assertEqual(self.store.record(2)['status'],'preserved')
  self.store.mark(2,r['revision'],'review')
  with self.assertRaises(ValueError):self.send(r,'manual',text='Ручной перевод.')
  self.assertEqual(self.store.record(2)['status'],'preserved')
 def test_newest_preservation_first(self):
  self.preserve(3);self.preserve(1);self.assertEqual([r['id'] for r in folder(self.store,self.pid)['rows']],[1,3])
 def test_wrong_project_and_invalid_provider_leave_record_intact(self):
  r=self.preserve(2);other=self.root/'other';other.mkdir();pid=self.store.project(str(other))['id']
  with self.assertRaises(ValueError):self.send(r,'end',project=pid)
  with self.assertRaises(ValueError):self.send(r,'end',provider='invalid')
  self.assertEqual(self.store.record(2)['status'],'preserved')
 def test_protected_translation_and_active_review_not_enqueued(self):
  r=self.store.update(2,0,'Привет.','translated','human',manual=True);r=self.preserve(2)
  with self.assertRaises(ValueError):self.send(r,'front')
  self.store.update(1,0,'Привет.','translated','local');self.store.create_job(self.pid,'review','local',self.settings)
  r=self.preserve(3)
  with self.assertRaises(ValueError):self.send(r,'end')
  self.assertEqual(self.store.record(3)['status'],'preserved')

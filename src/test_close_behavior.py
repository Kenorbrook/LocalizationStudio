import json,tempfile,threading,unittest
from pathlib import Path
from core import Store
from close_behavior import read_choice,write_choice,stop_jobs

class CloseBehaviorTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=Store(self.root/'test.db');self.store.launch_lock=threading.RLock();self.store.closing=False
 def tearDown(self):self.tmp.cleanup()
 def test_preference_only_remembers_real_actions(self):
  path=self.root/'preferences.json';self.assertIsNone(read_choice(path));write_choice(path,'tray');self.assertEqual(read_choice(path),'tray');write_choice(path,'exit');self.assertEqual(read_choice(path),'exit');write_choice(path,None);self.assertIsNone(read_choice(path))
  with self.assertRaises(ValueError):write_choice(path,'cancel')
 def test_close_pauses_and_preserves_queue_and_translation(self):
  pid=self.store.project(str(self.root))['id'];p=self.root/'text.json';p.write_text('[{"source":"Hello."}]');self.store.import_files(pid,[p]);jid=self.store.create_job(pid,'translate','local',{})
  with self.store.db() as db:db.execute("UPDATE jobs SET state='running',pid=123")
  seen=[]
  def stop(store,job):
   with store.db() as db:self.assertEqual(db.execute('SELECT state FROM jobs').fetchone()[0],'paused')
   seen.append(job['id']);return True
  stop_jobs(self.store,stop);self.assertEqual(seen,[jid]);self.assertTrue(self.store.closing)
  with self.store.db() as db:self.assertEqual(db.execute('SELECT state FROM queue').fetchone()[0],'pending');self.assertEqual(db.execute('SELECT count(*) FROM records').fetchone()[0],1)
 def test_failure_keeps_app_usable_and_queue_paused(self):
  pid=self.store.project(str(self.root))['id'];p=self.root/'text.json';p.write_text('[{"source":"Hello."}]');self.store.import_files(pid,[p]);self.store.create_job(pid,'translate','local',{})
  with self.assertRaises(ValueError):stop_jobs(self.store,lambda *_:(_ for _ in ()).throw(ValueError('access denied')))
  self.assertFalse(self.store.closing)

if __name__=='__main__':unittest.main()

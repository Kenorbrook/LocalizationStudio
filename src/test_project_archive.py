import json,tempfile,unittest
from pathlib import Path
from core import Store
from app import state
class ProjectArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=Store(self.root/'test.db');self.pid=self.store.project(str(self.root))['id']
        path=self.root/'corpus.json';path.write_text('[{"source":"Hello"}]');self.store.import_files(self.pid,[path]);self.store.update(1,0,'Привет','translated','human',manual=True)
    def tearDown(self):self.tmp.cleanup()
    def test_hide_last_project_and_restore_same_path_preserves_everything(self):
        self.store.mark(1,1,'review');before=self.store.record(1);self.store.hide_project(self.pid);self.assertEqual(state(self.store,self.pid)['projects'],[]);self.assertIsNone(state(self.store,self.pid)['project']);self.assertEqual(len(self.store.hidden_projects()),1)
        restored=self.store.project(str(self.root));self.assertEqual(restored['id'],self.pid);self.assertEqual(self.store.record(1),before);self.assertEqual(self.store.hidden_projects(),[])
    def test_hidden_project_selection_falls_back_to_visible_project(self):
        other=self.root/'other';other.mkdir();pid=self.store.project(str(other))['id'];self.store.hide_project(self.pid);self.assertEqual(state(self.store,self.pid)['project'],pid)
    def test_hide_and_restore_do_not_stop_running_job_or_change_queue(self):
        with self.store.db() as db:db.execute('UPDATE records SET manual=0,status=\'empty\'')
        jid=self.store.create_job(self.pid,'translate','local',{})
        with self.store.db() as db:db.execute("UPDATE jobs SET state='running',done=0,pid=123 WHERE id=?",(jid,))
        self.store.hide_project(self.pid);self.assertTrue(self.store.hidden_projects()[0]['active']);self.store.hide_project(self.pid,False)
        with self.store.db() as db:self.assertEqual(tuple(db.execute('SELECT state,pid,done,total FROM jobs WHERE id=?',(jid,)).fetchone()),('running',123,0,1));self.assertEqual(db.execute('SELECT state FROM queue WHERE job=?',(jid,)).fetchone()[0],'pending')
    def test_unknown_project_rejected(self):
        with self.assertRaises(ValueError):self.store.hide_project(999)
if __name__=='__main__':unittest.main()

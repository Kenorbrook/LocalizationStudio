import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from core import Store
from app import api
from game_detection import analyze,inspect_project,saved_report,import_detected,read_prefix

class DetectionTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.store=Store(self.root/'studio'/'test.db');self.pid=self.store.project(str(self.root))['id']
    def tearDown(self):self.temp.cleanup()
    def file(self,name,content):
        path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(content if isinstance(content,bytes) else content.encode('utf-8'));return path
    def test_renpy_existing_language_layer_preferred(self):
        self.file('renpy/__init__.py','');self.file('game/script.rpy','label start:\n    e "Hello."\n')
        self.file('game/tl/russian/script.rpy','translate russian greeting:\n    # e "Hello."\n    e "Привет."\n')
        self.file('game/screens.rpy','screen preferences():\n    textbutton "English" action Language(None)\n    textbutton "Русский" action Language("russian")\n')
        r=analyze(self.root);self.assertEqual(r['engine']['id'],'renpy');self.assertEqual(r['engine']['confidence'],'high')
        self.assertEqual(r['auto_import'],['game/tl/russian/script.rpy']);self.assertEqual(r['languages'],['russian']);self.assertEqual(len(r['language_actions']),2)
        self.assertFalse(r['capabilities']['add_language_menu'])
    def test_archive_only_renpy_is_reported_without_import(self):
        self.file('renpy/__init__.py','');self.file('game/scripts.rpa',b'RPA-3.0 0000000000000000 00000000\n')
        r=analyze(self.root);self.assertEqual(r['engine']['id'],'renpy');self.assertFalse(r['auto_import']);self.assertFalse(r['capabilities']['archive_extraction'])
    def test_extension_spoof_not_confirmed(self):
        for i in range(4):self.file(f'game/{i}.rpa',b'not an archive')
        self.file('example.rpy','This is plain text.')
        r=analyze(self.root);self.assertEqual(r['engine']['confidence'],'tentative')
    def test_empty_runtime_directory_is_only_tentative(self):
        (self.root/'renpy').mkdir();r=analyze(self.root);self.assertEqual(r['engine']['confidence'],'tentative');self.assertFalse(r['auto_import'])
    def test_unity_player_and_source_version(self):
        self.file('Assets/dialogues.csv','source,translation\nHello,\n');self.file('ProjectSettings/ProjectVersion.txt','m_EditorVersion: 6000.1.2f1\n')
        self.file('Game_Data/globalgamemanagers',b'\0');self.file('UnityPlayer.dll',b'\0')
        r=analyze(self.root);self.assertEqual(r['engine']['id'],'unity');self.assertEqual(r['engine']['version'],'6000.1.2f1');self.assertEqual(r['capabilities']['importable_files'],1);self.assertFalse(r['auto_import'])
    def test_godot_pck_magic_and_project(self):
        self.file('project.godot','config_version=5\n[application]\nconfig/name="Demo"');self.file('game.pck',b'GDPC'+b'\0'*20)
        r=analyze(self.root);self.assertEqual(r['engine']['id'],'godot');self.assertEqual(len(r['archives']),1)
    def test_rpgmaker_events_not_generic_json_corpus(self):
        self.file('www/js/rpg_core.js',"var Utils = {}; Utils.RPGMAKER_NAME = 'MV';")
        self.file('www/data/Map001.json',json.dumps({'events':[{'list':[{'code':401,'parameters':['Hello.']}]}],'tilesetId':1}))
        r=analyze(self.root);self.assertEqual(r['engine']['id'],'rpgmv');self.assertFalse(r['auto_import']);self.assertEqual(r['capabilities']['importable_files'],0)
    def test_kirikiri_and_gamemaker_headers(self):
        self.file('scenario.xp3',b'XP3\r\n \n\x1a\x8bg\x01');r=analyze(self.root);self.assertEqual(r['engine']['id'],'kirikiri')
        self.file('data.win',b'FORM'+b'\0'*12);r=analyze(self.root);self.assertEqual(r['engine']['id'],'multiple');self.assertFalse(r['auto_import'])
    def test_generic_package_not_rpgmaker_or_unreal(self):
        self.file('package.json','{"name":"App","main":"index.html"}');self.file('data.pak',b'not an Unreal package')
        self.assertEqual(analyze(self.root)['engine']['id'],'unknown')
    def test_mixed_engines_never_auto_merge_games(self):
        self.file('One/renpy/__init__.py','');self.file('One/game/script.rpy','label start:\n    "Hello"')
        self.file('Two/project.godot','config_version=5')
        r=analyze(self.root);self.assertEqual(r['engine']['id'],'multiple');self.assertFalse(r['auto_import'])
    def test_saved_analysis_is_project_scoped_and_read_only(self):
        path=self.file('corpus.json',json.dumps([{'source':'Hello','translation':'Привет'}]));original=path.read_bytes()
        with patch('subprocess.Popen',side_effect=AssertionError('No processes allowed')),patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('No network allowed')):
            r=inspect_project(self.store,self.pid)
        self.assertEqual(Path(saved_report(self.store,self.pid)['root']).resolve(),self.root.resolve());self.assertEqual(path.read_bytes(),original)
        with self.store.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM records').fetchone()[0],0)
        other=self.root/'other';other.mkdir();pid=self.store.project(str(other))['id'];self.assertIsNone(saved_report(self.store,pid))
        self.assertEqual(import_detected(self.store,self.pid,['corpus.json'])['added'],1)
        with self.assertRaises(ValueError):import_detected(self.store,self.pid,['../outside.txt'])
    def test_invalid_encoding_is_not_guessed(self):
        self.file('dialogue.txt','Привет'.encode('utf-16'));r=analyze(self.root);self.assertFalse(r['candidates'][0]['supported'])
    def test_partial_utf8_prefix_does_not_break_character(self):
        path=self.file('sample.txt','Я'*40000);text,truncated=read_prefix(path,65535);self.assertTrue(truncated);self.assertTrue(text.endswith('Я'))
    def test_scan_bound_and_runtime_cache_excluded(self):
        self.file('translation_tools/cache.json','[{"source":"Not game text"}]');self.file('game/saves/save.json','[{"source":"Not game text"}]')
        for i in range(10):self.file(f'text{i}.txt','Hello')
        r=analyze(self.root,max_files=3);self.assertTrue(r['partial']);self.assertLessEqual(r['scanned_files'],3)
        r=analyze(self.root);self.assertFalse(any('translation_tools' in c['path'] or '/saves/' in c['path'] for c in r['candidates']))
    def test_project_open_does_not_import_arbitrary_config_json(self):
        self.file('config.json','{"asset":"image.png","name":"Project"}');self.file('corpus.json','[{"source":"Hello"}]')
        result=api(self.store,'project',{'root':str(self.root),'scan':True});self.assertEqual(result['import']['added'],1)
    def test_unreal_project_metadata(self):
        self.file('Demo.uproject','{"FileVersion":3,"EngineAssociation":"5.4"}');r=analyze(self.root);self.assertEqual(r['engine']['id'],'unreal');self.assertEqual(r['engine']['version'],'5.4')

if __name__=='__main__':unittest.main()

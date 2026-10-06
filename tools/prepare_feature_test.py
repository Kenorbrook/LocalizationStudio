import sys,json,subprocess,time
sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'src'))
from core import Store
fixture=root/'qa-project-features'/str(time.time_ns());fixture.mkdir(parents=True)
db=fixture/'test.sqlite3';store=Store(db);pid=store.project(str(fixture))['id']
path=fixture/'corpus.json';path.write_text(json.dumps([{'source':f'Line {i}','translation':'Текст' if i==0 else ''} for i in range(52)]),encoding='utf-8')
store.import_files(pid,[path]);jid=store.create_job(pid,'translate','local',{})
store.error(jid,3,'Не помещается в контекст — тест')
for rid in (51,52):
    store.update(rid,0,'Текст '+str(rid),'translated','local:fixture')
    with store.db() as connection:connection.execute("UPDATE queue SET state='done' WHERE job=? AND record=?",(jid,rid))
with store.db() as connection:connection.execute('UPDATE jobs SET done=2 WHERE id=?',(jid,))
with store.db() as connection:connection.execute('UPDATE jobs SET heartbeat=? WHERE id=?',(time.time(),jid))
report=fixture/'report.json'
exe=root/'release'/'LocalizationStudio'/'LocalizationStudio.exe';worker=exe.with_name('LocalizationWorker.exe')
client=subprocess.Popen([str(worker),'--mcp','--db',str(db)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
client.stdin.write((json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'clientInfo':{'name':'UI test client','version':'1.0'}}})+'\n').encode());client.stdin.flush()
assert 'result' in json.loads(client.stdout.readline())
gui=subprocess.Popen([str(exe),'--db',str(db),'--smoke-report',str(report),'--ui-test-script',str(root/'tools'/'project_feature_probe.js')])
try:
    for i in range(160):
        if report.exists():break
        time.sleep(.5)
    result=json.loads(report.read_text(encoding='utf-8'))
    (root/'project-feature-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False));assert result.get('passed'),result
finally:
    client.stdin.close();client.wait(timeout=5)
    import ctypes
    u=ctypes.WinDLL('user32');u.FindWindowW.argtypes=[ctypes.c_wchar_p,ctypes.c_wchar_p];u.FindWindowW.restype=ctypes.c_void_p
    # Close only this fixture window by enumerating its process id.
    callback=ctypes.WINFUNCTYPE(ctypes.c_bool,ctypes.c_void_p,ctypes.c_void_p)
    def close(h,_):
        process=ctypes.c_ulong();u.GetWindowThreadProcessId.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulong)];u.GetWindowThreadProcessId(h,ctypes.byref(process))
        if process.value==gui.pid:u.PostMessageW.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_void_p,ctypes.c_void_p];u.PostMessageW(h,0x10,None,None)
        return True
    (fixture/'desktop_preferences.json').write_text('{"close_choice":"exit"}')
    u.EnumWindows(callback(close),None)
    gui.wait(timeout=15)
    # Delete only this synthetic fixture after its process has exited.
    import shutil
    if fixture.resolve().parent != (root/'qa-project-features').resolve():raise RuntimeError('Unexpected fixture path')
    shutil.rmtree(fixture)

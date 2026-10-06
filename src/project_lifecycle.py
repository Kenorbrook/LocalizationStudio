"""Project-scoped removal; original game files are never removed."""
import ctypes,json,os,re,subprocess,sys
from pathlib import Path
from core import APP_HOME,ROOT

def matches_worker(info,store,jid):
    command=info.get('CommandLine') or '';exe=Path(info.get('ExecutablePath') or '')
    packaged=exe.parent==APP_HOME and exe.name.startswith('LocalizationWorker') and exe.suffix.lower()=='.exe'
    source=exe==Path(sys.executable) and str(ROOT/'app.py').lower() in command.lower()
    if not (packaged or source) or not re.search(r'--worker\s+'+str(int(jid))+r'(?:\s|$)',command):return False
    match=re.search(r'--db\s+(?:"([^"]+)"|(\S+))',command)
    return bool(match and os.path.normcase(str(Path(match[1] or match[2]).resolve()))==os.path.normcase(str(store.path.resolve())))

def stop_worker(store,job):
    pid=job.get('pid')
    if not pid:return False
    if os.name!='nt':raise ValueError('Остановка обработчика поддерживается только в Windows')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_bool,ctypes.c_ulong];kernel.OpenProcess.restype=ctypes.c_void_p
    kernel.CloseHandle.argtypes=[ctypes.c_void_p];kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
    kernel.TerminateProcess.argtypes=[ctypes.c_void_p,ctypes.c_uint]
    handle=kernel.OpenProcess(0x0001|0x1000|0x100000,False,int(pid))
    if not handle and ctypes.get_last_error()==87:return False
    try:
        if handle and kernel.WaitForSingleObject(handle,0)==0:return False
        command=f"Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}' | Select-Object ExecutablePath,CommandLine | ConvertTo-Json -Compress"
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],capture_output=True,text=True,encoding='utf-8',errors='replace',creationflags=subprocess.CREATE_NO_WINDOW,timeout=12)
        if result.returncode:raise ValueError('Не удалось проверить обработчик перед удалением')
        info=json.loads(result.stdout.lstrip('\ufeff')) if result.stdout.strip() else {}
        if not matches_worker(info,store,job['id']):return False
        if not handle:raise ValueError('Нет доступа для остановки обработчика проекта')
        if not kernel.TerminateProcess(handle,0):raise ctypes.WinError(ctypes.get_last_error())
        if kernel.WaitForSingleObject(handle,5000)!=0:raise ValueError('Обработчик ещё не завершился; повторите удаление')
        return True
    finally:
        if handle:kernel.CloseHandle(handle)

def remove_secret(pid):
    if os.name!='nt':return
    api=ctypes.WinDLL('Advapi32.dll',use_last_error=True);api.CredDeleteW.argtypes=[ctypes.c_wchar_p,ctypes.c_ulong,ctypes.c_ulong]
    if not api.CredDeleteW(f'LocalizationStudio/project/{pid}',1,0) and ctypes.get_last_error()!=1168:raise ctypes.WinError(ctypes.get_last_error())

def delete_project(store,pid,stopper=stop_worker):
    with store.db() as db:
        if not db.execute('SELECT id FROM projects WHERE id=?',(pid,)).fetchone():raise ValueError('Проект не найден')
        db.execute('UPDATE projects SET deleting=1 WHERE id=?',(pid,))
        jobs=[dict(r) for r in db.execute('SELECT * FROM jobs WHERE project=?',(pid,))]
        db.execute("UPDATE jobs SET state='cancelled',error='Проект удаляется' WHERE project=? AND state IN ('running','queued','paused','waiting','held')",(pid,))
        db.execute("UPDATE mcp_requests SET state='failed',response='Проект удаляется' WHERE job IN (SELECT id FROM jobs WHERE project=?) AND state IN ('pending','sent')",(pid,))
    stopped=sum(bool(stopper(store,job)) for job in jobs)
    remove_secret(pid)
    result=store.delete_project_data(pid);result['stopped_workers']=stopped;return result

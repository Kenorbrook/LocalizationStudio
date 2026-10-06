import ctypes,json,sqlite3,sys,time
from pathlib import Path
root=Path(__file__).resolve().parents[1];pid=int(sys.argv[1]);prefs=root/'data'/'desktop_preferences.json';previous=prefs.read_bytes() if prefs.exists() else None
with sqlite3.connect(root/'data'/'studio.sqlite3') as db:active=[r[0] for r in db.execute("SELECT id FROM jobs WHERE state IN ('running','queued')")]
(root/'language-update-running-jobs.json').write_text(json.dumps(active))
u=ctypes.WinDLL('user32');u.GetWindowThreadProcessId.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulong)];u.ShowWindow.argtypes=[ctypes.c_void_p,ctypes.c_int];u.PostMessageW.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_void_p,ctypes.c_void_p];u.GetWindowTextW.argtypes=[ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_int]
cb=ctypes.WINFUNCTYPE(ctypes.c_bool,ctypes.c_void_p,ctypes.c_void_p)
def collect(parent=None):
 values=[]
 def visit(h,_):
  p=ctypes.c_ulong();u.GetWindowThreadProcessId(h,ctypes.byref(p))
  if p.value==pid:
   title=ctypes.create_unicode_buffer(1024);u.GetWindowTextW(h,title,1024);values.append((h,title.value))
  return True
 if parent:u.EnumChildWindows.argtypes=[ctypes.c_void_p,cb,ctypes.c_void_p];u.EnumChildWindows(parent,cb(visit),None)
 else:u.EnumWindows(cb(visit),None)
 return values
k=ctypes.WinDLL('kernel32');k.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_bool,ctypes.c_ulong];k.OpenProcess.restype=ctypes.c_void_p;k.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_ulong];k.CloseHandle.argtypes=[ctypes.c_void_p]
handle=k.OpenProcess(0x100000,False,pid)
try:
 prefs.write_text('{"close_choice":null}')
 main=next(h for h,t in collect() if t=='Localization Studio');u.ShowWindow(main,9);u.PostMessageW(main,0x10,None,None)
 for _ in range(100):
  dialog=next((h for h,t in collect() if t=='Закрытие Localization Studio'),None)
  if dialog:
   button=next(h for h,t in collect(dialog) if t=='Закрыть с выгрузкой');u.PostMessageW(button,0xF5,None,None);break
  time.sleep(.1)
 if k.WaitForSingleObject(handle,20000)!=0:raise RuntimeError('Программа не закрылась. Возможно, есть несохранённые правки; обновление не установлено.')
 print(json.dumps({'closed':True,'resume_jobs':active}))
finally:
 if previous is None:prefs.unlink(missing_ok=True)
 else:prefs.write_bytes(previous)
 k.CloseHandle(handle)

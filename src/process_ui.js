// Translation process: bounded future rows, retained history, and project folders.
window.processTab='queue';window.markKind='bad';let processOffset=0,marksOffset=0,processPrefs={max_phrases:200,max_seconds:600,page_size:50},processPrefsProject=0;
const processControls=element('div');processControls.id='processControls';processControls.hidden=true;
const processTabs=element('div',undefined,'flex wrap');const futureTab=element('button','Очередь'),historyTab=element('button','История');futureTab.id='futureTab';historyTab.id='historyTab';processTabs.append(futureTab,historyTab);processControls.append(processTabs);
const historySettings=element('details');historySettings.id='historySettings';historySettings.append(element('summary','Размер очереди и показ истории'));
const settingsGrid=element('div',undefined,'flex wrap');
for(const [id,label,value]of [['historyMaxPhrases','Последних фраз в истории (0 — без лимита)',200],['historyMaxSeconds','Хранить в истории, секунд (0 — без лимита)',600],['processPageSize','Строк на странице / в очереди (10–200)',50]]){const box=element('label',label,'sub');const input=element('input');input.id=id;input.type='number';input.min=id==='processPageSize'?10:0;input.value=value;input.style.cssText='display:block;width:190px;margin-top:6px';box.append(input);settingsGrid.append(box)}
const saveHistorySettings=element('button','Применить');saveHistorySettings.id='saveHistorySettings';historySettings.append(settingsGrid,element('p','Оба ограничения применяются одновременно. Они скрывают старые результаты только здесь: весь перевод остаётся в проекте. Даже без лимитов список показывается страницами.','sub'),saveHistorySettings);processControls.append(historySettings);textPane.prepend(processControls);
const badFolder=element('button','▣ Брак · 0'),reviewFolder=element('button','▣ Ручная проверка · 0');badFolder.id='badFolder';reviewFolder.id='reviewFolder';projectTools.append(badFolder,reviewFolder);
function updateProcessControls(){processControls.hidden=viewMode!=='queue';futureTab.classList.toggle('primary',window.processTab==='queue');historyTab.classList.toggle('primary',window.processTab==='history');textPane.querySelectorAll('.pagination button').forEach(button=>button.hidden=viewMode==='queue'&&window.processTab==='queue');setText(badFolder,'▣ Брак · '+(snapshot?.flags?.bad||0));setText(reviewFolder,'▣ Ручная проверка · '+(snapshot?.flags?.review||0))}
const processShowView=showView;showView=function(mode){processShowView(mode);updateProcessControls()};
function processPageInfo(data){if(viewMode==='queue'&&window.processTab==='queue')return `В очереди: ${data.total} · показаны следующие ${data.rows.length} записей`;const start=data.offset||0;return data.total?`${start+1}–${Math.min(start+data.rows.length,data.total)} из ${data.total}${viewMode==='queue'?' · история процесса':''}`:viewMode==='queue'?'История пуста или скрыта установленными ограничениями':viewMode==='flags'?'В этой папке нет помеченных строк':'Нет строк'}
async function switchProcess(tab){if(dirty.size)throw Error('Сохраните или отмените правки перед сменой вкладки');window.processTab=tab;processOffset=0;showView('queue');await loadRows()}
futureTab.onclick=guard(()=>switchProcess('queue'));historyTab.onclick=guard(()=>switchProcess('history'));
for(const [button,kind]of [[badFolder,'bad'],[reviewFolder,'review']])button.onclick=guard(async()=>{if(dirty.size)throw Error('Сохраните или отмените правки');window.markKind=kind;marksOffset=0;showView('flags');await loadRows()});
saveHistorySettings.onclick=guard(async()=>{processPrefs=await request('process-settings',{project,preferences:{max_phrases:+$('historyMaxPhrases').value,max_seconds:+$('historyMaxSeconds').value,page_size:+$('processPageSize').value}});processOffset=0;if(!dirty.size)await loadRows();toast('Настройки процесса сохранены для проекта')});
const processOriginalLoadRows=loadRows;
loadRows=async function(quiet=false){
  if(viewMode!=='queue'&&viewMode!=='flags'&&viewMode!=='preserved')return processOriginalLoadRows(quiet);
  const identity=JSON.stringify([project,viewMode,window.processTab,window.markKind,processOffset,marksOffset]);
  const url=viewMode==='preserved'?`preserved?project=${project}&offset=${marksOffset}`:viewMode==='flags'?`marked?project=${project}&kind=${window.markKind}&offset=${marksOffset}`:window.processTab==='history'?`process-history?project=${project}&offset=${processOffset}`:`queue?project=${project}`;
  const data=await request(url);
  if(identity!==JSON.stringify([project,viewMode,window.processTab,window.markKind,processOffset,marksOffset])||(quiet&&(dirty.size||document.activeElement.tagName==='TEXTAREA')))return;
  renderRows(data,quiet);const size=data.page_size||50,position=data.offset||0;$('prev').disabled=!position;$('next').disabled=position+size>=data.total;
};
const oldPrev=$('prev').onclick,oldNext=$('next').onclick;
for(const [id,direction,old]of [['prev',-1,oldPrev],['next',1,oldNext]])$(id).onclick=guard(async()=>{if(viewMode!=='flags'&&viewMode!=='preserved'&&!(viewMode==='queue'&&window.processTab==='history'))return old();if(dirty.size)throw Error('Сохраните или отмените правки');if(viewMode==='flags'||viewMode==='preserved')marksOffset=Math.max(0,marksOffset+direction*50);else processOffset=Math.max(0,processOffset+direction*processPrefs.page_size);await loadRows()});
const processRender=renderRows;renderRows=function(data,quiet=false){processRender(data,quiet);const byId=new Map(data.rows.map(row=>[row.id,row]));
  for(const card of $('rows').querySelectorAll('.record')){const row=byId.get(+card.dataset.id);if(!row||card.dataset.markControls)return;card.dataset.markControls='1';const actions=card.querySelector('.record-actions'),top=card.querySelector('.record-top');
    if(viewMode!=='text'&&row.path){const filename=element('span',row.path.split(/[\\/]/).pop(),'sub');filename.title=row.path;top.insertBefore(filename,top.lastChild)}
    if(/^[^\p{L}\p{N}]+$/u.test(row.source)){card.classList.add('punctuation');card.querySelector('textarea').style.minHeight='32px';card.querySelector('textarea').style.height='32px'}
    if(row.flag){const label=element('span',row.flag==='bad'?'Брак':'Нужна ручная проверка','badge');label.style.color=label.style.borderColor=row.flag==='bad'?'var(--red)':'var(--yellow)';top.append(label);}
    if(row.processed_at){const stamp=element('span',new Date(row.processed_at*1000).toLocaleTimeString('ru-RU'),'sub');top.append(stamp)}
    for(const [label,kind]of row.flag?[['Снять пометку','']]:[['Брак','bad'],['Ручная проверка','review']]){const button=element('button',label);button.title=kind==='bad'?'Пометить текущий перевод как заведомо плохой; пометка сохраняется после редактуры':kind==='review'?'Отложить строку для ручной проверки; пометка сохраняется после редактуры':'Убрать пометку; существующая ручная правка останется защищённой';button.onclick=guard(async()=>{if(dirty.has(row.id))throw Error('Сохраните или отмените правку перед пометкой');await request('mark',{id:row.id,revision:row.revision,kind});await refresh();await loadRows();toast(kind?'Строка добавлена в папку проекта':'Пометка снята')});actions.append(button)}
  }
};
const processRefresh=refresh;refresh=async function(initial=false){await processRefresh(initial);updateProcessControls();if(project&&processPrefsProject!==project){const pid=project,pref=await request('process-settings?project='+pid);if(pid!==project)return;processPrefs=pref;processPrefsProject=pid;processOffset=marksOffset=0;$('historyMaxPhrases').value=pref.max_phrases;$('historyMaxSeconds').value=pref.max_seconds;$('processPageSize').value=pref.page_size}};

// Folder review reuses the same model/language/context controls as ordinary jobs.
const folderControls=element('div');folderControls.id='folderReviewControls';folderControls.hidden=true;textPane.prepend(folderControls);
const folderSettingsOrigin=element('span');settingsMenu.before(folderSettingsOrigin);
const folderReviewHint=element('p','Повторная редактура всех переведённых строк этой папки, включая уже отредактированные. Пометки сохраняются до ручного снятия. Модель, контекст, языки и лимиты задаются ниже.','sub');
const folderInstruction=element('textarea');folderInstruction.id='folderReviewInstruction';folderInstruction.maxLength=4000;folderInstruction.rows=4;folderInstruction.style.width='100%';
const defaultFolderInstruction='Это фразы, отмеченные человеком как неудачные или сомнительные. Тщательно сравни перевод с оригиналом и контекстом. Проверь смысл, естественность речи, род и обращения персонажей, пропуски и лишние детали. Сохрани служебные символы. Не меняй корректный перевод без конкретной причины; объясни найденные проблемы.';
const manualLabel=element('label');const folderManual=element('input');folderManual.id='folderReviewManual';folderManual.type='checkbox';manualLabel.append(folderManual,document.createTextNode(' Включить ручные правки: разрешить выбранной модели изменить их текст'));
const folderRun=element('button','Отправить заново на редактуру','primary');folderRun.id='folderReviewRun';
const folderClear=element('button');folderClear.id='folderClearMarks';folderControls.append(folderReviewHint,element('label','Дополнительная инструкция редактору'),folderInstruction,manualLabel,element('div',undefined,'flex wrap'));folderControls.lastChild.append(folderRun,folderClear);
let folderIdentity='';function updateFolderControls(){const visible=viewMode==='flags';folderControls.hidden=!visible;$('scope').hidden=visible;if(visible){if(settingsMenu.parentNode!==folderControls)folderControls.insertBefore(settingsMenu,folderReviewHint);settingsMenu.open=true;const identity=project+':'+window.markKind;if(identity!==folderIdentity){folderInstruction.value=localStorage.getItem('review-instruction:'+identity)||defaultFolderInstruction;folderManual.checked=false;folderIdentity=identity}setText(folderClear,window.markKind==='bad'?'Снять «Брак» со всех строк проекта':'Снять «Ручная проверка» со всех строк проекта')}else if(settingsMenu.parentNode===folderControls){folderSettingsOrigin.after(settingsMenu)}}
folderInstruction.oninput=()=>localStorage.setItem('review-instruction:'+folderIdentity,folderInstruction.value);
folderRun.onclick=guard(async()=>{if(dirty.size)throw Error('Сохраните или отмените правки');readRunLimits();await request('job',{project,stage:'review',provider:$('provider').value,settings:{...settings(),review_instruction:folderInstruction.value},mark_kind:window.markKind,allow_manual:folderManual.checked});await refresh();toast('Редактура папки запущена; пометки останутся')});
folderClear.onclick=guard(async()=>{if(dirty.size)throw Error('Сохраните или отмените правки');const result=await request('clear-marks',{project,kind:window.markKind});marksOffset=0;await refresh();await loadRows();toast('Снято пометок: '+result.cleared)});
const folderShowView=showView;showView=function(mode){folderShowView(mode);updateFolderControls()};
const folderRefresh=refresh;refresh=async function(initial=false){await folderRefresh(initial);updateFolderControls()};

const preservedFolder=element('button','▣ Сохранено без перевода · 0');preservedFolder.id='preservedFolder';projectTools.append(preservedFolder);
preservedFolder.onclick=guard(async()=>{if(dirty.size)throw Error('Сохраните или отмените правки');marksOffset=0;showView('preserved');await loadRows()});
const preservedRefresh=refresh;refresh=async function(initial=false){await preservedRefresh(initial);setText(preservedFolder,'▣ Сохранено без перевода · '+(snapshot?.counts?.preserved||0))};
const literalRender=renderRows;renderRows=function(data,quiet=false){literalRender(data,quiet);const map=new Map(data.rows.map(row=>[row.id,row]));for(const card of $('rows').querySelectorAll('.record')){const row=map.get(+card.dataset.id);if(!row||card.dataset.literalControls)return;card.dataset.literalControls='1';const actions=card.querySelector('.record-actions');
 if(row.language_note&&row.flag==='review'&&!row.preserve_kind){const note=element('p',row.language_note,'sub');note.style.padding='0 16px';card.querySelector('.record-top').after(note)}
 if(row.preserve_kind){const reason=element('p','Почему оставлен оригинал: '+row.preserve_reason,'sub');reason.style.padding='0 16px';card.querySelector('.record-top').after(reason);const contextButton=[...actions.querySelectorAll('button')].find(b=>b.textContent==='Контекст');const area=card.querySelector('textarea');area.readOnly=true;area.oninput=null;area.setAttribute('aria-label','Оригинал сохранён без перевода');
 const translate=element('button','Перевести');translate.title='Добавить в конец очереди, обработать следующей или перевести вручную';translate.onclick=guard(()=>showPreservedTranslation(row));actions.replaceChildren(translate,contextButton)}

 else {const menu=element('details');menu.append(element('summary','Оставить оригинал без перевода'));const reason=element('input');reason.placeholder='Почему: другой язык, авторский приём…';reason.maxLength=1000;reason.style.width='100%';const preserve=element('button','Другой язык / авторский приём');preserve.onclick=guard(async()=>{if(dirty.has(row.id))throw Error('Сохраните или отмените правку');if(!reason.value.trim())throw Error('Укажите причину сохранения оригинала');await request('preserve',{id:row.id,revision:row.revision,kind:'foreign',reason:reason.value.trim()});await refresh();await loadRows()});menu.append(reason,preserve);actions.append(menu)}
}};
// Error recovery uses the current settings and never starts inference for a budget check.
const oldErrorOpen=$('errorsButton').onclick;
$('errorsButton').onclick=guard(async()=>{
 await oldErrorOpen();if(!$('infoDialog').open)return;
 const errors=await request('errors?project='+project);const content=$('infoContent');
 content.prepend(element('p','Повтор использует текущую модель и настройки справа. Если задача работает, сначала приостановите её. Проверка контекста не обращается к модели. Ручные правки защищены.','sub'));
 const retry=async(stage,record)=>{if(dirty.size)throw Error('Сохраните или отмените правки');await request('retry-errors',{project,stage,record,provider:stage==='cloud'&&$('provider').value==='local'?'cloud':$('provider').value,settings:settings()});$('infoDialog').close();await refresh();toast('Ошибочные строки возвращены в обработку с текущими настройками')};
 const controls=element('div',undefined,'flex wrap');
 for(const stage of [...new Set(errors.filter(e=>e.record&&e.stage).map(e=>e.stage))]){const b=element('button','Повторить все ошибки '+({translate:'перевода',review:'редактуры',cloud:'проверки'}[stage]));b.dataset.retryStage=stage;b.onclick=guard(()=>retry(stage));controls.append(b)}
 if(job&&['running','queued'].includes(job.state)){const pause=element('button','Приостановить текущую задачу');pause.onclick=guard(async()=>{await request('control',{id:job.id,mode:'pause'});pause.disabled=true;toast('Задача приостановлена. Дождитесь завершения текущего запроса перед повтором')});controls.append(pause)}
 content.prepend(controls);
 const cards=[...content.querySelectorAll('.error')];
 errors.forEach((e,i)=>{if(!e.record||!e.stage||!cards[i])return;const card=cards[i];const one=element('button','Повторить эту строку');one.dataset.retryRecord=e.record;one.onclick=guard(()=>retry(e.stage,e.record));const inspect=element('button','Проверить контекст без запуска');inspect.dataset.budgetRecord=e.record;const result=element('p',undefined,'sub');inspect.onclick=guard(async()=>{const b=await request('error-budget',{project,record:e.record,stage:e.stage,settings:settings()});setText(result,`Выбрано: ${b.configured} токенов. Полный запрос с резервом ответа: ≈${b.estimated_full}. Короткий фрагмент без соседей: ≈${b.estimated_minimum}. Рекомендуемый контекст для полного запроса: ≈${b.recommended}. `+(b.fits_full?'По оценке помещается.':'Полный запрос не помещается.')+' Это оценка; точное число зависит от токенизатора модели. Разбиение и сокращение соседей могут уменьшить запрос.');});const custom=element('button','Повторить с другими параметрами');custom.dataset.customRetryRecord=e.record;const panel=element('div');panel.hidden=true;panel.style.padding='12px 0';const fields={};for(const [key,label,min,max]of [['context_before','Фраз до',0,100],['context_after','Фраз после',0,100],['context','Контекст, токенов',1024,131072]]){const box=element('label',label);const input=element('input');input.type='number';input.min=min;input.max=max;input.step=1;input.value=settings()[key];input.dataset.overrideKey=key;fields[key]=input;box.append(input);panel.append(box)}panel.append(element('p','Применится только к этой строке. Настройки проекта и остальных строк сохранятся.','sub'));const run=element('button','Повторить только с этими параметрами');run.dataset.runCustomRetry=e.record;const estimate=element('button','Оценить контекст');const cancel=element('button','Отмена');cancel.onclick=()=>panel.hidden=true;custom.onclick=()=>panel.hidden=!panel.hidden;const readOverrides=()=>{const values={};for(const [k,input]of Object.entries(fields)){if(!input.checkValidity()||!Number.isInteger(+input.value))throw Error('Проверьте параметры строки');values[k]=+input.value}return values};run.onclick=guard(async()=>{if(dirty.size)throw Error('Сохраните или отмените правки');await request('retry-errors',{project,stage:e.stage,record:e.record,provider:e.stage==='cloud'&&$('provider').value==='local'?'cloud':$('provider').value,settings:settings(),overrides:readOverrides()});$('infoDialog').close();await refresh();toast('Для этой строки сохранены отдельные параметры повтора')});const budgetText=element('p',undefined,'sub');estimate.onclick=guard(async()=>{const b=await request('error-budget',{project,record:e.record,stage:e.stage,settings:{...settings(),...readOverrides()}});setText(budgetText,`Полный запрос: ≈${b.estimated_full} токенов; выбрано ${b.configured}; рекомендуемый запас: ≈${b.recommended}. Оценка, без запуска модели.`)});panel.append(run,estimate,cancel,budgetText);card.append(one,inspect,custom,panel,result)});
});

async function showPreservedTranslation(row){
 if(dirty.size)throw Error('Сохраните или отмените правки');
 const dialog=$('infoDialog'),content=$('infoContent');$('infoTitle').textContent='Перевести фразу';content.replaceChildren(element('p',row.source));
 content.append(element('p','В активной задаче используются её модель и параметры. Если задачи нет, перевод начнётся с настройками справа. На паузе фраза останется в очереди до продолжения.','sub'));
 for(const [mode,label]of [['end','Добавить в конец очереди'],['front','Проверить без очереди — обработать следующей'],['manual','Ручной перевод']]){
  const button=element('button',label);button.dataset.preservedMode=mode;button.style.cssText='display:block;margin:12px 0;width:100%';
  button.onclick=guard(async()=>{
   if(mode==='manual')return showPreservedManual(row);
   const result=await request('translate-preserved',{project,id:row.id,revision:row.revision,mode,provider:$('provider').value,settings:settings()});dialog.close();await refresh();await loadRows();toast(result.paused?'Строка добавлена. Продолжите задачу после паузы':mode==='front'?'Фраза будет обработана после текущего запроса':'Фраза добавлена в конец очереди');
  });content.append(button);
 }
 content.append(element('p','Обработка без очереди не прерывает текущий запрос к модели: выбранная фраза пойдёт сразу после него.','sub'));
 dialog.showModal();$('infoClose').focus({preventScroll:true});content.scrollTop=0;
}
async function showPreservedManual(row){
 await showRecordContext(row.id);$('infoTitle').textContent='Ручной перевод · контекст';
 const selected=$('contextSelected'),card=selected.querySelector('.context-card'),area=element('textarea');area.id='preservedManualText';area.value=row.text||row.source;area.rows=5;area.style.cssText='width:100%;box-sizing:border-box;margin-top:12px';area.setAttribute('aria-label','Ручной перевод выбранной фразы');
 card.lastChild.replaceWith(area);card.append(element('p','После сохранения фраза перейдёт в обычный текст проекта. Ручной перевод защищён от перезаписи ИИ.','sub'));
 // Expanding context must not rebuild an editor containing an unsaved draft.
 for(const b of $('infoContent').querySelectorAll('button'))if(b.textContent==='Показать больше контекста')b.remove();
 const save=element('button','Сохранить перевод');save.id='preservedManualSave';$('infoFooter').prepend(save);
 area.oninput=()=>dirty.add(row.id);
 const cleanup=()=>{dirty.delete(row.id);save.remove()};$('infoDialog').addEventListener('close',cleanup,{once:true});
 save.onclick=guard(async()=>{await request('translate-preserved',{project,id:row.id,revision:row.revision,mode:'manual',text:area.value});$('infoDialog').close();await refresh();await loadRows();toast('Ручной перевод сохранён и защищён')});
 $('infoContent').scrollTop=Math.max(0,selected.offsetTop-$('infoContent').offsetTop-12);area.focus({preventScroll:true});
}

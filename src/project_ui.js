// Project navigation and connection state; preserve editors during live polling.
let viewMode='home';
const style=element('style');style.textContent=`[hidden]{display:none!important}.project-actions{display:flex;gap:16px;margin:26px 0}.project-actions button{padding:26px;flex:1;text-align:left;font-size:18px}.project-actions small{display:block;font-size:12px;color:var(--muted);margin-top:9px}.spinner{display:inline-block;width:13px;height:13px;border:2px solid #33594b;border-top-color:var(--accent);border-radius:50%;animation:spin 1s linear infinite;margin-right:8px;vertical-align:middle}@keyframes spin{to{transform:rotate(360deg)}}#projectActivity{margin:15px 0;min-height:22px}#projectSummary{line-height:1.9}#projectBack{margin-bottom:18px}#mcpStatus{white-space:pre-line}`;document.head.append(style);
const center=document.querySelector('.center');
const textPane=element('div');textPane.id='textPane';
for(const selector of ['.toolbar','.pagination','#rows'])textPane.append(center.querySelector(selector));center.append(textPane);
const back=element('button','← Проект');back.id='projectBack';const projectNav=element('div');projectNav.id='projectNav';projectNav.append(back);center.prepend(projectNav);style.textContent+='#projectNav{position:sticky;top:-24px;z-index:20;background:var(--bg);margin:-24px -28px 18px;padding:12px 28px;border-bottom:1px solid var(--line)}#projectNav #projectBack{margin-bottom:0}';
const home=element('div');home.id='projectHome';
const activity=element('div');activity.id='projectActivity';$('stats').before(activity);
const summary=element('div',undefined,'sub');summary.id='projectSummary';home.append(summary);
const choices=element('div',undefined,'project-actions');
const all=element('button','Просмотр всего текста');all.id='viewAll';all.append(element('small','Все файлы, блоки и сохранённые переводы'));
const queue=element('button','Процесс перевода');queue.id='viewQueue';queue.append(element('small','Очередь будущих строк и история результатов'));choices.append(all,queue);home.append(choices);
const projectTools=element('div',undefined,'flex wrap');projectTools.append($('errorsButton'),proposalsButton);home.append(projectTools);center.append(home);
const mcpStatus=element('p','MCP не подключён','sub');mcpStatus.id='mcpStatus';$('mcpButton').before(mcpStatus);
const projectPulse=element('div',undefined,'sub');projectPulse.id='projectPulse';$('projects').after(projectPulse);let activityVersion='',pulseVersion='';
const oldMcp=$('mcpButton').onclick;$('mcpButton').onclick=async()=>{await oldMcp();$('infoContent').prepend(element('p',mcpStatus.textContent))};
function showView(mode){
  viewMode=mode;home.hidden=mode!=='home';textPane.hidden=mode==='home';back.hidden=mode==='home';projectNav.hidden=mode==='home';
  $('files').hidden=mode!=='text';$('files').previousElementSibling.hidden=mode!=='text';
  textPane.querySelector('.toolbar').hidden=mode!=='text';textPane.querySelector('.pagination').querySelectorAll('button').forEach(b=>b.hidden=mode==='queue'&&window.processTab!=='history');
  if(snapshot)updateProjectScreen();
}
function updateProjectScreen(){
  const p=snapshot.projects.find(p=>p.id==project);const active=!!p?.active;
  const av=JSON.stringify([project,active,job?.state]);if(av!==activityVersion){activity.replaceChildren();if(active)activity.append(element('span',undefined,'spinner'));activity.append(element('span',active?'Перевод или проверка выполняется':['paused','held'].includes(job?.state)?'Задача на паузе':'Нет работающих задач'));activityVersion=av}
  const names=snapshot.projects.filter(p=>p.active).map(p=>p.name);const pv=JSON.stringify(names);if(pv!==pulseVersion){projectPulse.replaceChildren();if(names.length)projectPulse.append(element('span',undefined,'spinner'),element('span','Работает: '+names.join(', ')));pulseVersion=pv}
  const counts=snapshot.counts;const total=Object.values(counts).reduce((a,b)=>a+b,0);const translated=(counts.translated||0)+(counts.edited||0)+(counts.verified||0);
  setText(summary,`Всего строк: ${total} · Есть перевод: ${translated} · Без перевода: ${counts.empty||0}\nСохранено без перевода: ${counts.preserved||0} · В очереди: ${snapshot.pending||0} · Сейчас обрабатывается: ${job?.state==='running'&&job.current?1:0} · Ошибок проекта: ${snapshot.errors||0}`);
  setText('errorsButton','Ошибки этого проекта · '+(snapshot.errors||0));
  if(viewMode==='home'){setText('fileTitle',p?.name||'Откройте проект');setText('description','Выберите просмотр всего текста или текущей очереди');setText('breadcrumb','ПРОЕКТ')}
  if(viewMode==='queue'){setText('fileTitle','Процесс перевода');setText('description',window.processTab==='history'?'Недавние переведённые фразы. Ограничения влияют только на показ в этом окне.':'Следующие записи очереди. Готовые фразы доступны во вкладке «История».');setText('breadcrumb',(p?.name||'')+' / ПРОЦЕСС')}
  if(viewMode==='flags'){setText('fileTitle',window.markKind==='bad'?'Брак':'Ручная проверка');setText('description','Помеченные строки этого проекта. Редактура разрешена; пометки снимаются только вручную.');setText('breadcrumb',(p?.name||'')+' / ПОМЕТКИ')}
  if(viewMode==='preserved'){setText('fileTitle','Сохранено без перевода');setText('description','Символы, паузы и намеренно оставленный другой язык. Причина указана у каждой строки; обычная очередь и редактура их пропускают.');setText('breadcrumb',(p?.name||'')+' / ОРИГИНАЛ')}
  if(viewMode==='text'){const f=snapshot.files.find(f=>f.id===file);if(f){setText('fileTitle',f.path.split(/[\\/]/).pop());setText('description',f.total+' строк · '+f.kind+' · правки сохраняются в базе приложения');setText('breadcrumb',(p?.name||'')+' / ТЕКСТ')}}
}
all.onclick=guard(async()=>{showView('text');await loadRows()});queue.onclick=guard(async()=>{showView('queue');await loadRows()});back.onclick=guard(async()=>{if(dirty.size)throw Error('Сохраните или отмените правки перед выходом');showView('home')});
$('files').addEventListener('click',()=>showView('text'),true);
const oldProjectChange=$('projects').onchange;$('projects').onchange=async(...args)=>{if(!dirty.size)showView('home');await oldProjectChange(...args)};
const oldOpen=$('openProject').onclick;$('openProject').onclick=async(...args)=>{showView('home');await oldOpen(...args)};
const projectRefresh=refresh;let connectionVersion='';
refresh=async function(initial=false){await projectRefresh(initial);updateProjectScreen();
  const connections=await request('connections');const version=JSON.stringify(connections);
  window.connectionSnapshot=connections.mcp;
  if(version!==connectionVersion){const message=connections.mcp.length?connections.mcp.map(c=>`● Подключён: ${c.client}${c.version?' '+c.version:''}\nMCP: stdio · без сетевого порта`).join('\n'):'MCP не подключён';setText(mcpStatus,message+'\nПриложение: '+connections.application);connectionVersion=version}
};
showView('home');
const characters=element('button','Персонажи и голоса');projectTools.append(characters);
const profileDialog=element('dialog');profileDialog.id='speakerDialog';
profileDialog.append(element('h1','Персонажи и голоса'),element('p','Правила относятся только к этому проекту. Укажите идентификатор из исходника. Неизвестный род оставляйте пустым. Самообращение и обращения к персонажу могут различаться.','sub'));
const profileList=element('div');profileDialog.append(profileList);
const profileActions=element('div',undefined,'flex wrap');const addProfile=element('button','+ Добавить голос'),saveProfiles=element('button','Сохранить профили','primary'),closeProfiles=element('button','Закрыть');profileActions.append(addProfile,saveProfiles,closeProfiles);profileDialog.append(profileActions);document.body.append(profileDialog);
function profileCard(id='',profile={}){
  const card=element('div',undefined,'error');const fields=[['id','Идентификатор говорящего',id],['name','Имя',profile.name],['kind','Тип: dialogue / thought / narrator / unknown',profile.kind],['self_reference_gender','Как говорит о себе: мужской / женский / неизвестно',profile.self_reference_gender],['reference_gender','Как говорят о персонаже',profile.reference_gender],['notes','Особенности речи, исключения и подтверждающие примеры',profile.notes]];
  for(const [key,label,value]of fields){card.append(element('label',label));const input=element('input');input.dataset.field=key;input.value=value||'';card.append(input)}
  const remove=element('button','Удалить профиль');remove.onclick=()=>card.remove();card.append(remove);profileList.append(card);
}
characters.onclick=guard(async()=>{await refresh();const p=snapshot.projects.find(p=>p.id==project);const profiles=JSON.parse(p.settings||'{}').speaker_profiles||{};profileList.replaceChildren();Object.entries(profiles).forEach(([id,p])=>profileCard(id,p));profileDialog.showModal()});
addProfile.onclick=()=>profileCard();closeProfiles.onclick=()=>profileDialog.close();
saveProfiles.onclick=guard(async()=>{const profiles={};for(const card of profileList.children){const values={};card.querySelectorAll('input').forEach(i=>values[i.dataset.field]=i.value.trim());const id=values.id;delete values.id;if(!id)throw Error('Укажите идентификатор каждого голоса');if(profiles[id])throw Error('Идентификатор повторяется: '+id);profiles[id]=values}await request('speaker-profiles',{project,profiles});profileDialog.close();await refresh();toast('Правила персонажей сохранены для этого проекта')});

// One primary action; secondary operations stay available in named submenus.
const right=document.querySelector('.right');const originalControls=$('pause').parentElement;
const mainAction=element('button','Перевести оставшееся','full primary');mainAction.id='mainAction';
const actionHint=element('p',undefined,'sub');actionHint.id='actionHint';
const activeSettings=element('p',undefined,'sub');activeSettings.id='activeSettings';
const taskMenu=element('details');taskMenu.id='taskMenu';taskMenu.append(element('summary','Действия с задачей'),$('cancel'),$('retry'));
originalControls.before(mainAction,actionHint,activeSettings,taskMenu);originalControls.hidden=true;
const translateButton=right.querySelector('[data-stage="translate"]');translateButton.hidden=true;
const settingsTitle=right.querySelector('h2');const settingsMenu=element('details');settingsMenu.id='translationSettings';settingsMenu.append(element('summary','Настройки нового запуска'));
settingsTitle.before(settingsMenu);let settingNode=settingsTitle.nextSibling;
while(settingNode&&settingNode!==translateButton){const next=settingNode.nextSibling;settingsMenu.append(settingNode);settingNode=next}settingsTitle.remove();
const reviewMenu=element('details');reviewMenu.id='reviewMenu';reviewMenu.append(element('summary','Редактура и проверка'));
const reviewHint=element('p',undefined,'sub');reviewHint.id='reviewHint';reviewMenu.append(reviewHint,right.querySelector('[data-stage="review"]'),right.querySelector('[data-stage="cloud"]'));
const cloudConnection=$('endpoint').closest('details');reviewMenu.append(cloudConnection);settingsMenu.before(reviewMenu);
setText(right.querySelector('[data-stage="review"]'),'Литературная редакция');setText(right.querySelector('[data-stage="cloud"]'),'Облачная проверка');
mainAction.onclick=guard(async()=>{if(job?.state==='held'||(job?.state==='paused'&&JSON.parse(job.settings||'{}')._retry_origin)){await request('control',{id:job.id,mode:'resume',preserve_project_settings:true});await refresh()}else if(job?.state==='paused')await $('resume').onclick();else if(job?.state==='running')await $('pause').onclick();else if(job?.state!=='queued')await start('translate')});
function updateTaskActions(){
  const active=!!job&&['running','queued','paused','held'].includes(job.state);
  const stageName={translate:'перевод',review:'редактуру',cloud:'облачную проверку'}[job?.stage]||'задачу';
  const primary=['paused','held'].includes(job?.state)?'Продолжить '+stageName:job?.state==='running'?'Приостановить '+stageName:job?.state==='queued'?'Запуск задачи…':'Перевести оставшееся';
  setText(mainAction,primary);mainAction.disabled=job?.state==='queued'||(!active&&!(snapshot?.counts.empty||0));
  setText(actionHint,['paused','held'].includes(job?.state)?(JSON.parse(job.settings||'{}')._retry_origin||job.state==='held'?'Продолжит эту очередь с её сохранённой моделью и параметрами.':'Возобновит сохранённую очередь; лимит и число соседних фраз берутся из настроек ниже.'):job?.state==='running'?'Приостановит задачу после текущего запроса.':job?.state==='queued'?'Обработчик запускается.':!(snapshot?.counts.empty||0)?'В проекте нет строк без перевода.':'Создаст новую задачу для строк без перевода.');
  const currentSettings=active?JSON.parse(job.settings||'{}'):settings();
  const provider=active?job.provider:$('provider').value;const model=provider==='cloud'?currentSettings.cloud_model:currentSettings.model;
  setText(activeSettings,(active?'Текущая задача: ':'Новый запуск: ')+(model||'модель не выбрана')+' · '+(currentSettings.source_language||'English')+' → '+(currentSettings.target_language||'Russian')+' · соседей: '+(currentSettings.context_before??(active?4:12))+' до / '+(currentSettings.context_after??(active?4:8))+' после');
  $('cancel').hidden=!active;$('retry').disabled=active||!(snapshot?.errors||0);
  const review=right.querySelector('[data-stage="review"]');const cloud=right.querySelector('[data-stage="cloud"]');
  review.disabled=active||!(snapshot?.counts.translated||0);cloud.disabled=active||!((snapshot?.counts.translated||0)+(snapshot?.counts.edited||0));
  setText(reviewHint,active?'Сначала завершите или отмените текущую задачу.':'Проверка запускается отдельно для уже переведённых строк.');
}
const actionsRefresh=refresh;refresh=async function(initial=false){await actionsRefresh(initial);updateTaskActions()};
settingsMenu.addEventListener('input',updateTaskActions);settingsMenu.addEventListener('change',updateTaskActions);
if(snapshot)updateTaskActions();

// Ollama metadata is local. Parameter count is a size metric, not a quality score.
let modelInventory=[];
const modelSortLabel=element('label','Сортировка моделей');const modelSort=element('select');modelSort.id='model_sort';
for(const [value,label]of [['name','По имени'],['parameters','По числу параметров'],['size','По размеру на диске'],['modified','По дате изменения (новые сверху)']]){const option=element('option',label);option.value=value;modelSort.append(option)}
const modelInfo=element('p',undefined,'sub');modelInfo.id='modelInfo';$('model').after(modelSortLabel,modelSort,modelInfo);
function parameterCount(value){const match=String(value||'').match(/([\d.]+)\s*([BKM])/i);return match?Number(match[1])*({B:1e9,M:1e6,K:1e3}[match[2].toUpperCase()]||1):0}
function applyModelList(inventory,preferredName=$('model').value){
  modelInventory=inventory;const current=preferredName;const groups=new Map();
  for(const model of inventory){const key=model.digest||model.name;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(model)}
  const representatives=[...groups.values()].map(group=>{const preferred=group.find(m=>m.name===current)||group.find(m=>m.name==='qwen3:4b')||[...group].sort((a,b)=>a.name.localeCompare(b.name))[0];return {...preferred,aliases:group.map(m=>m.name)}});
  representatives.sort((a,b)=>{const byName=a.name.localeCompare(b.name);if(modelSort.value==='parameters')return parameterCount(b.details?.parameter_size)-parameterCount(a.details?.parameter_size)||byName;if(modelSort.value==='size')return (b.size||0)-(a.size||0)||byName;if(modelSort.value==='modified')return (Date.parse(b.modified_at)||0)-(Date.parse(a.modified_at)||0)||byName;return byName});
  $('model').replaceChildren(...representatives.map(m=>{const option=element('option',m.name+(m.details?.parameter_size?' · '+m.details.parameter_size:'')+(m.aliases.length>1?` · ${m.aliases.length} имени`:''));option.value=m.name;return option}));
  if(representatives.some(m=>m.name===current))$('model').value=current;
  updateModelInfo();updateTaskActions();
}
function updateModelInfo(){
  const selected=modelInventory.find(m=>m.name===$('model').value);if(!selected){setText(modelInfo,'');return}
  const aliases=modelInventory.filter(m=>m.digest&&m.digest===selected.digest).map(m=>m.name);
  let text=(selected.size?(selected.size/1024**3).toFixed(1)+' ГиБ · ':'')+(selected.details?.quantization_level||'');
  if(selected.modified_at)text+=' · Изменена локально: '+new Date(selected.modified_at).toLocaleDateString('ru-RU');
  if(aliases.length>1)text+='\nОдна модель под именами: '+aliases.join(', ');
  text+='\nЧисло параметров не является оценкой качества перевода. Дата изменения не является датой выпуска.';setText(modelInfo,text);
}
modelSort.onchange=()=>applyModelList(modelInventory);$('model').addEventListener('change',updateModelInfo);
const savedLoadSettings=loadSettings;loadSettings=function(p){savedLoadSettings(p);if(modelInventory.length)applyModelList(modelInventory,JSON.parse(p.settings||'{}').model||$('model').value)};

// Removing a project from the picker never removes its corpus or jobs.
const archiveMenu=element('dialog');archiveMenu.id='projectManager';archiveMenu.setAttribute('aria-labelledby','projectManagerTitle');
const hideProject=element('button','Скрыть проект в списке');hideProject.id='hideProject';
const hiddenProjects=element('button','Скрытые проекты');hiddenProjects.id='hiddenProjects';archiveMenu.append(hideProject,hiddenProjects,element('p','Переводы, правки, настройки и очередь остаются в базе. Повторное открытие той же папки вернёт проект. Работающая задача продолжит выполняться.','sub'));document.body.append(archiveMenu);
async function selectRestoredProject(pid){project=pid;file=offset=0;rows=[];total=0;$('rows').replaceChildren();showView('home');await refresh(true)}
hideProject.onclick=guard(async()=>{if(!project)throw Error('Нет выбранного проекта');if(dirty.size)throw Error('Сохраните или отмените правки перед скрытием');await request('hide-project',{project});await selectRestoredProject(0);toast('Проект скрыт; весь прогресс сохранён')});
hiddenProjects.onclick=guard(async()=>{const list=await request('hidden-projects');$('infoTitle').textContent='Скрытые проекты · данные сохранены';$('infoContent').replaceChildren();for(const p of list){const card=element('div',undefined,'error');const restore=element('button','Вернуть в список');restore.dataset.restoreProject=p.id;restore.disabled=!!p.deleting;restore.onclick=guard(async()=>{if(dirty.size)throw Error('Сохраните или отмените правки');await request('restore-project',{project:p.id});$('infoDialog').close();await selectRestoredProject(p.id);toast('Проект восстановлен с сохранённым прогрессом')});const remove=element('button','Удалить из программы');remove.onclick=guard(()=>confirmProjectDeletion(p));card.append(remove,element('strong',p.name+(p.deleting?' · удаление не завершено':p.active?' · задача работает':'')),element('p',p.root,'sub'),restore);$('infoContent').append(card)}if(!list.length)$('infoContent').append(element('p','Скрытых проектов нет'));$('infoDialog').showModal()});
const archiveRefresh=refresh;refresh=async function(initial=false){await archiveRefresh(initial);hideProject.disabled=!project};

const completeProject=element('button','Перенести в завершённые');completeProject.id='completeProject';
const completedProjects=element('button','Завершённые проекты');completedProjects.id='completedProjects';
const deleteProject=element('button','Удалить проект из программы');deleteProject.id='deleteProject';deleteProject.style.color='var(--red)';archiveMenu.append(completeProject,completedProjects,deleteProject);
completeProject.onclick=guard(async()=>{if(dirty.size)throw Error('Сохраните или отмените правки');await request('complete-project',{project});await selectRestoredProject(0);toast('Проект перенесён в завершённые; данные сохранены')});
function confirmProjectDeletion(p){if(dirty.size)throw Error('Сохраните или отмените правки');$('infoTitle').textContent='Удалить проект «'+p.name+'»?';$('infoContent').replaceChildren(element('p',p.root),element('p','Задачи этого проекта будут остановлены. Переводы, ручные правки, история, пометки и настройки будут удалены из программы. Папка игры и её файлы останутся. Для сохранения прогресса используйте «Завершённые» или скрытие.'),element('p','После повторного добавления проект будет создан заново.'));const confirm=element('button','Удалить и остановить задачи');confirm.id='confirmDeleteProject';confirm.style.color='var(--red)';confirm.onclick=guard(async()=>{confirm.disabled=true;try{await request('delete-project',{project:p.id});$('infoDialog').close();await selectRestoredProject(0);toast('Проект удалён из программы; его задачи остановлены')}finally{confirm.disabled=false}});$('infoContent').append(confirm);if(!$('infoDialog').open)$('infoDialog').showModal();$('infoContent').scrollTop=0}
deleteProject.onclick=guard(async()=>{const p=snapshot.projects.find(p=>p.id===project);if(!p)throw Error('Выберите проект');confirmProjectDeletion(p)});
completedProjects.onclick=guard(async()=>{const list=await request('completed-projects');$('infoTitle').textContent='Завершённые проекты · перевод сохранён';$('infoContent').replaceChildren(element('p','Перевод всех строк завершён. Редактура и проверка могут ещё требоваться.'));for(const p of list){const card=element('div',undefined,'error');const restore=element('button','Вернуть в активные');restore.dataset.restoreCompleted=p.id;restore.onclick=guard(async()=>{await request('restore-project',{project:p.id});$('infoDialog').close();await selectRestoredProject(p.id)});const remove=element('button','Удалить из программы');remove.onclick=guard(()=>confirmProjectDeletion(p));card.append(element('strong',p.name),element('p',p.root,'sub'),restore,remove);$('infoContent').append(card)}if(!list.length)$('infoContent').append(element('p','Завершённых проектов нет'));$('infoDialog').showModal();$('infoContent').scrollTop=0});
const lifecycleRefresh=refresh;refresh=async function(initial=false){const oldProject=project;await lifecycleRefresh(initial);deleteProject.disabled=!project;completeProject.disabled=!project||!!snapshot?.counts?.empty||!snapshot?.files?.length;if(oldProject&&oldProject!==project){file=offset=0;rows=[];$('rows').replaceChildren();if(snapshot.files.length)file=snapshot.files[0].id;showView('home')}};

// Project lifecycle actions belong in a dialog, never between the picker and its files.
const projectPicker=element('div',undefined,'project-picker');projectPicker.id='projectPicker';$('projects').before(projectPicker);
const manageProjects=element('button','⋯');manageProjects.id='projectArchiveMenu';manageProjects.title='Управление проектами';manageProjects.setAttribute('aria-label','Управление проектами');
projectPicker.append($('projects'),manageProjects);
const managerHeader=element('div',undefined,'project-manager-header');const managerTitle=element('h1','Управление проектами');managerTitle.id='projectManagerTitle';const managerClose=element('button','×');managerClose.id='projectManagerClose';managerClose.setAttribute('aria-label','Закрыть управление проектами');managerClose.onclick=()=>archiveMenu.close();managerHeader.append(managerTitle,managerClose);
const managerBody=element('div');managerBody.id='projectManagerBody';const managerProject=element('div',undefined,'manager-project');managerProject.append(element('div','Выбранный проект','sub'));const managerName=element('strong');managerName.id='managedProjectName';const managerPath=element('p',undefined,'sub');managerPath.id='managedProjectPath';managerProject.append(managerName,managerPath);managerBody.append(managerProject);
function managementGroup(title,hint,buttons,danger=false){const section=element('section',undefined,'manager-group'+(danger?' manager-danger':''));section.append(element('h2',title),element('p',hint,'sub'));const actions=element('div',undefined,'manager-actions');actions.append(...buttons);section.append(actions);managerBody.append(section)}
managementGroup('Положение в списке','Скрытие сохраняет переводы, правки и очередь. Работающая задача продолжит выполняться. В завершённые можно перенести проект без строк, ожидающих перевода.',[hideProject,completeProject]);
managementGroup('Другие проекты','Вернуть сохранённый проект в активный список.',[hiddenProjects,completedProjects]);
managementGroup('Удаление данных проекта','Остановит задачи и удалит переводы, историю и настройки из программы. Игровая папка останется. Перед удалением появится подтверждение.',[deleteProject],true);
const managerFooter=element('div',undefined,'project-manager-footer');const managerDone=element('button','Закрыть');managerDone.onclick=()=>archiveMenu.close();managerFooter.append(managerDone);archiveMenu.replaceChildren(managerHeader,managerBody,managerFooter);
manageProjects.onclick=()=>{const selected=snapshot?.projects.find(p=>p.id===project);setText(managerName,selected?.name||'Проект не выбран');setText(managerPath,selected?.root||'Можно открыть скрытые и завершённые проекты.');archiveMenu.showModal();managerBody.scrollTop=0;managerClose.focus({preventScroll:true})};
for(const button of [hideProject,completeProject,hiddenProjects,completedProjects,deleteProject]){const invoke=button.onclick;button.onclick=(...args)=>{archiveMenu.close();return invoke(...args)}}
style.textContent+=`
.project-picker{display:flex;align-items:center;gap:8px;margin:10px 0 12px}
#projectPulse:empty{display:none}
#projectPulse:not(:empty){margin:0 0 10px;overflow-wrap:anywhere}
.project-picker #projects{margin-top:0!important;width:0;flex:1;min-width:0}
#projectArchiveMenu{flex:0 0 36px;height:42px;padding:0;font-size:24px;line-height:1}
#projectManager{width:560px;max-width:calc(100vw - 40px);padding:0;overflow:hidden}
#projectManager[open]{display:flex;flex-direction:column;max-height:85vh}
.project-manager-header{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:20px 24px;border-bottom:1px solid var(--line);flex-shrink:0}
.project-manager-header h1{margin:0;font-size:21px}
#projectManagerClose{font-size:24px;line-height:1;padding:5px 11px}
#projectManagerBody{padding:20px 24px;overflow:auto;min-height:0;scrollbar-gutter:stable}
.manager-project{padding:14px 16px;border:1px solid var(--line);border-radius:9px;background:#121b20;overflow-wrap:anywhere}
.manager-project strong{display:block;margin-top:5px;font-size:16px}
.manager-project p{margin:6px 0 0}
.manager-group{margin-top:22px;padding-top:18px;border-top:1px solid var(--line)}
.manager-group h2{margin:0 0 8px}
.manager-group p{margin:0 0 14px}
.manager-actions{display:flex;flex-direction:column;gap:10px}
.manager-actions button{width:100%;margin:0;text-align:left;padding:11px 14px}
.manager-danger h2{color:var(--red)}
.manager-danger button{border-color:#694448;background:#30252a}
.project-manager-footer{padding:14px 24px;border-top:1px solid var(--line);display:flex;justify-content:flex-end;flex-shrink:0}
`;

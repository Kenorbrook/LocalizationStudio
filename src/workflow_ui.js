// Human context, local/API/MCP model selection, and bounded translation runs.
const limitMenu=element('details');limitMenu.id='runLimits';limitMenu.append(element('summary','Лимит запуска / продолжения'));
const limitMode=element('select');limitMode.id='limitMode';
for(const [value,label]of [['none','Без ограничения'],['lines','По числу строк'],['time','По времени']]){const option=element('option',label);option.value=value;limitMode.append(option)}
const limitValue=element('input');limitValue.id='limitValue';limitValue.type='number';limitValue.min=1;limitValue.value=200;
const limitLabel=element('label','Количество строк');limitLabel.id='limitLabel';
const limitHint=element('p','После достижения лимита очередь встанет на паузу. Остановка — после текущей строки.','sub');limitMenu.append(limitMode,limitLabel,limitValue,limitHint);actionHint.after(limitMenu);
function readRunLimits(validate=true){const value=+limitValue.value;if(validate&&limitMode.value!=='none'&&(!Number.isFinite(value)||value<=0))throw Error('Укажите положительный лимит');return {run_lines:limitMode.value==='lines'?value:0,run_minutes:limitMode.value==='time'?value:0}}
function updateLimitInput(){const shown=limitMode.value!=='none';limitLabel.hidden=limitValue.hidden=!shown;setText(limitLabel,limitMode.value==='time'?'Продолжительность, минут (120 = 2 часа)':'Количество обрабатываемых строк');limitValue.step=limitMode.value==='time'?'0.1':'1'}
limitMode.onchange=()=>{limitValue.value=limitMode.value==='time'?120:200;updateLimitInput()};updateLimitInput();
const mcpSessionInput=element('input');mcpSessionInput.id='mcp_session';mcpSessionInput.hidden=true;settingsMenu.append(mcpSessionInput);
const outputInput=$('max_output');const outputLabel=outputInput.previousElementSibling;setText(outputLabel,'Лимит ответа модели, токенов');$('context').after(outputLabel,outputInput,element('p','Длинный текст разбивается на части по абзацам и предложениям. Готовые части сохраняются для продолжения.','sub'));
const executionModel=element('select');executionModel.id='executionModel';const executionLabel=element('label','Модель для нового запуска');settingsMenu.querySelector('summary').after(executionLabel,executionModel);
const connectMcp=element('button','Подключить MCP');connectMcp.id='connectMcp';executionModel.after(connectMcp);
connectMcp.onclick=()=>{$('mcpButton').onclick()};
const modelHint=element('input');modelHint.id='mcp_model_hint';modelHint.placeholder='Необязательно: предпочтительная модель MCP';settingsMenu.append(modelHint);
// The local picker remains the source of local model metadata, without a second visible choice.
$('model').hidden=true;const localLabel=$('model').previousElementSibling;if(localLabel?.tagName==='LABEL')localLabel.hidden=true;
$('provider').hidden=true;const providerLabel=$('provider').previousElementSibling;if(providerLabel?.tagName==='LABEL')providerLabel.hidden=true;
let knownConnections=[],executionVersion='';
function updateExecutionChoices(){
  const selected=$('provider').value==='mcp'?'mcp:'+mcpSessionInput.value:$('provider').value==='cloud'?'cloud':'local:'+$('model').value;
  const local=[...$('model').options].map(o=>[o.value,o.textContent]);const signature=JSON.stringify([local,knownConnections.map(c=>[c.session,c.client,c.model,c.sampling]),$('cloud_model').value,selected]);
  if(signature!==executionVersion){
    const localGroup=element('optgroup');localGroup.label='Локальные модели';for(const [id,label]of local){const option=element('option',label);option.value='local:'+id;localGroup.append(option)}
    const external=element('optgroup');external.label='Облачный API и MCP';const api=element('option',$('cloud_model').value?'API · '+$('cloud_model').value:'Подключить облачную модель через API…');api.value='cloud';external.append(api);
    for(const c of knownConnections){const option=element('option',`MCP · ${c.client} · ${c.model||'модель выбирает клиент'}${c.sampling?'':' (только чтение и предложения)'}`);option.value='mcp:'+c.session;option.disabled=!c.sampling;external.append(option)}
    if(selected.startsWith('mcp:')&&!knownConnections.some(c=>'mcp:'+c.session===selected)){const option=element('option','MCP отключён — подключите клиент');option.value=selected;option.disabled=true;external.append(option)}
    executionModel.replaceChildren(localGroup,external);executionModel.value=selected;executionVersion=signature;
  }
  connectMcp.hidden=knownConnections.some(c=>c.sampling);setText(connectMcp,knownConnections.length?'Настроить MCP':'Подключить MCP');modelHint.hidden=$('provider').value!=='mcp';modelSort.hidden=modelSortLabel.hidden=$('provider').value!=='local';modelInfo.hidden=$('provider').value!=='local';
}
executionModel.onchange=()=>{const value=executionModel.value;if(value==='cloud'){$('provider').value='cloud';if(!$('cloud_model').value){reviewMenu.open=cloudConnection.open=true;$('endpoint').focus()}}else if(value.startsWith('mcp:')){$('provider').value='mcp';mcpSessionInput.value=value.slice(4)}else{$('provider').value='local';$('model').value=value.slice(6);updateModelInfo()}updateExecutionChoices();updateTaskActions()};
const mcpProviderOption=element('option','MCP-клиент');mcpProviderOption.value='mcp';$('provider').append(mcpProviderOption);
const originalSettings=settings;settings=function(){return {...originalSettings(),...readRunLimits(false),execution_kind:$('provider').value,mcp_session:mcpSessionInput.value,mcp_model_hint:modelHint.value}};
const workflowStart=start;start=function(...args){readRunLimits();return workflowStart(...args)};
const workflowLoadSettings=loadSettings;loadSettings=function(p){workflowLoadSettings(p);const s=JSON.parse(p.settings||'{}');$('provider').value=s.execution_kind||'local';mcpSessionInput.value=s.mcp_session||'';modelHint.value=s.mcp_model_hint||'';updateExecutionChoices()};
const inventoryApply=applyModelList;applyModelList=function(...args){inventoryApply(...args);updateExecutionChoices()};
const workflowRefresh=refresh;let limitsProject=0;
refresh=async function(initial=false){await workflowRefresh(initial);knownConnections=window.connectionSnapshot||[];updateExecutionChoices();
  if(initial||limitsProject!==project){const s=job?JSON.parse(job.settings||'{}'):{};limitMode.value=s.run_lines?'lines':s.run_minutes?'time':'none';limitValue.value=s.run_lines||s.run_minutes||200;updateLimitInput();limitsProject=project}
};
// Only the body scrolls; title and close controls stay visible in every info dialog.
const infoDialog=$('infoDialog'),infoHeader=element('div',undefined,'flex'),infoClose=element('button','×');
infoHeader.id='infoHeader';infoHeader.style.cssText='flex-shrink:0;justify-content:space-between;gap:16px';infoClose.id='infoClose';infoClose.setAttribute('aria-label','Закрыть окно');infoClose.title='Закрыть';infoClose.onclick=()=>infoDialog.close();
infoHeader.append($('infoTitle'),infoClose);infoDialog.prepend(infoHeader);$('infoTitle').style.margin=0;
const infoFooter=element('div');infoFooter.id='infoFooter';infoFooter.style.cssText='flex-shrink:0;padding-top:14px;border-top:1px solid var(--line)';infoFooter.append(infoDialog.querySelector('[data-close]'));infoDialog.append(infoFooter);
const infoStyle=element('style');infoStyle.textContent='#infoDialog[open]{display:flex;flex-direction:column;overflow:hidden;max-height:85vh}#infoContent{min-height:0;overflow:auto;scrollbar-gutter:stable;overflow-anchor:none;margin:16px 0}#infoClose{font-size:24px;line-height:1;padding:5px 11px}#infoContent .context-card p{color:var(--ink);white-space:pre-wrap}#infoContent .context-card div{white-space:pre-wrap}';document.head.append(infoStyle);
let contextRequest=0;
infoDialog.addEventListener('close',()=>{contextRequest++});
async function showRecordContext(id,radius=10){
  id=Number(id);const generation=++contextRequest;
  const data=await request(`context?id=${id}&radius=${radius}`);if(generation!==contextRequest)return;
  $('infoTitle').textContent='Контекст строки';const content=$('infoContent');content.replaceChildren();
  const loc=data.location;let location=`Файл: ${loc.file}`;if(loc.line)location+=' · строка '+loc.line;if(loc.row)location+=' · запись '+loc.row;
  if(loc.source_file)location+='\nИсходник: '+loc.source_file+':'+loc.source_line;
  if(loc.json_path)location+='\nПуть в JSON: '+JSON.stringify(loc.json_path);
  location+='\nСцена / блок: '+(loc.scene||'не указан')+'\nГоворящий: '+(loc.speaker||'не установлен');
  const details=element('details');details.style.marginTop=0;details.append(element('summary','Где находится фраза и кто её произносит'),element('pre',location));
  const meta=data.metadata.speaker_metadata;if(meta.profile&&Object.keys(meta.profile).length)details.append(element('pre','Правила голоса: '+JSON.stringify(meta.profile,null,2)));content.append(details);
  content.append(element('p','Порядок: предыдущие строки → выбранная фраза → последующие строки. Это порядок записей того же файла и блока, не обязательно порядок прохождения игры. Последующие фразы тоже могут пояснять смысл; для меню это соседние пункты.','sub'));
  const ordered=[...data.rows].sort((a,b)=>a.position-b.position),target=ordered.find(r=>r.id===id);if(!target)throw Error('Выбранная строка не найдена в контексте');
  function section(title,rows,empty){const box=element('section');box.append(element('h2',title));if(!rows.length)box.append(element('p',empty,'sub'));for(const r of rows){const card=element('div',undefined,'error context-card'+(r.id===id?' current':''));card.dataset.contextId=r.id;card.style.border=r.id===id?'1px solid var(--accent)':'';card.append(element('strong',`${r.id===id?'▶ Выбранная фраза · ':''}#${r.position+1} · ${r.speaker||'Говорящий не установлен'}`),element('p',r.source),element('div',r.text||'Пока без перевода'));box.append(card)}content.append(box);return box}
  const before=ordered.filter(r=>r.position<target.position),after=ordered.filter(r=>r.position>target.position);
  section('До выбранной фразы · '+before.length,before,data.before_count?'В пределах показанного диапазона предыдущих строк нет. Более ранние записи этого блока находятся дальше.':'В этом блоке перед выбранной фразой ничего не предшествовало.');
  const selected=section('Выбранная фраза',[target],'');selected.id='contextSelected';
  section('После выбранной фразы · '+after.length,after,data.after_count?'В пределах показанного диапазона следующих строк нет. Более поздние записи этого блока находятся дальше.':'В этом блоке после выбранной фразы следующих строк нет.');
  if(radius<30){const more=element('button','Показать больше контекста');more.onclick=guard(()=>showRecordContext(id,30));content.append(more)}
  if(!infoDialog.open)infoDialog.showModal();
  infoClose.focus({preventScroll:true});infoDialog.scrollTop=0;content.scrollTop=0;
  const reveal=()=>{if(generation!==contextRequest||!infoDialog.open)return;const offset=selected.getBoundingClientRect().top-content.getBoundingClientRect().top;content.scrollTop=Math.max(0,content.scrollTop+offset-12);if(!before.length)content.scrollTop=0};
  reveal();requestAnimationFrame(reveal);
}
updateExecutionChoices();

// Source neighbors are independent of the model's total token budget.
const neighborControls=element('div');neighborControls.id='neighborControls';
const neighborFields=element('div',undefined,'flex');
for(const [id,label,value]of [['context_before','Фраз до текущей',12],['context_after','Фраз после текущей',8]]){const box=element('div',undefined,'grow');const input=element('input');input.id=id;input.type='number';input.min=0;input.max=100;input.step=1;input.value=value;box.append(element('label',label),input);neighborFields.append(box)}
neighborControls.append(neighborFields,element('p','Соседние фразы — в оригинале, в пределах того же файла и сцены. 0 отключает соответствующую сторону. Длинные соседние фразы сокращаются до 300 символов; при нехватке токенов дальние убираются. Число фраз применяется к новому запуску и при продолжении задачи после паузы.','sub'));$('context').after(neighborControls);
function readNeighborContext(){const result={};for(const key of ['context_before','context_after']){const value=+$(key).value;if(!Number.isInteger(value)||value<0||value>100)throw Error('Укажите целое число соседних фраз от 0 до 100');result[key]=value}return result}
const neighborOriginalSettings=settings;settings=function(){return {...neighborOriginalSettings(),...readNeighborContext()}};
const neighborLoadSettings=loadSettings;loadSettings=function(p){neighborLoadSettings(p);const saved=JSON.parse(p.settings||'{}');$('context_before').value=saved.context_before??12;$('context_after').value=saved.context_after??8};

const foreignPolicy=element('label');const autoForeign=element('input');autoForeign.id='auto_foreign';autoForeign.type='checkbox';autoForeign.checked=true;foreignPolicy.append(autoForeign,document.createTextNode(' Сохранять реплики на другом языке'));neighborControls.after(foreignPolicy,element('p','Язык определяется локально перед переводом. Уверенные случаи сохраняются в оригинале с причиной; сомнительные получают пометку «Ручная проверка». Имена и отдельные слова автоматически не исключаются.','sub'));
const languageSettings=settings;settings=function(){return {...languageSettings(),auto_foreign:autoForeign.checked}};
const languageLoad=loadSettings;loadSettings=function(p){languageLoad(p);autoForeign.checked=JSON.parse(p.settings||'{}').auto_foreign!==false};

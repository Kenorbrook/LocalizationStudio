void (async () => {
  const checks = {}, check = (name, value) => {checks[name]=!!value;if(!value)throw Error(name);};
  const wait = ms => new Promise(resolve => setTimeout(resolve,ms));
  try {
    for(let n=0;n<150&&!studioState.project;n++)await wait(40);
    await refresh();await pluginHost.ready();await projectJourney.refresh();
    const api=window.pywebview.api, real=api.plugin_call;
    const source=studioState.project, child=window.__renpyWorkspace;
    let state={enabled:true,busy:false,phase:"idle",prepared:false,built:false,message:"Модуль готов"};
    let offline=false, unsupported=false, sent=null, prepared=false, launched=[];
    api.plugin_call=async(id,command,args)=>{
      if(id!=="renpy")return real(id,command,args);
      if(command==="state")return structuredClone(state);
      if(command==="models")return {online:!offline,models:offline?[]:["fixture-a","fixture-b"]};
      if(command==="inspect"&&unsupported)throw Error("Не найден встроенный Windows Python игры");
      if(command==="prepare"){
        sent={command,args};prepared=true;state={...state,prepared:true,source_project:source,workspace_project:child,copy:args[1],message:"Копия подготовлена"};return structuredClone(state);
      }
      if(command==="pipeline"){sent={command,args};state={...state,busy:true,phase:"pipeline",message:"Локальный цикл: перевод"};return structuredClone(state);}
      if(command==="build"){sent={command,args};state={...state,built:true,busy:false,rollback_available:true,message:"Копия собрана"};return structuredClone(state);}
      if(command==="rollback"){state={...state,built:false,rollback_available:false};return structuredClone(state);}
      if(command==="launch"){launched.push(args);return {launched:true};}
      return real(id,command,args);
    };
    check("engineActionsAppearAtRelevantJourneySteps",!$("renpyPrepareEntry").parentElement.hidden&&$("journeyStep-files").contains($("renpyPrepareEntry"))&&$("journeyStep-install").contains($("renpyBuildEntry")));
    await $("renpyPrepareEntry").onclick();
    check("preparationUsesGameRuntimeAndEditableNewFolder",$("renpyGameDialog").open&&$("renpyCopySize").textContent.includes("8.5.3")&&$("renpyCopyPath").value.endsWith("Studio-Russian"));
    check("modelsComeFromInstalledListAndRequireChoice",$("renpyCycleModel").tagName==="SELECT"&&$("renpyCycleModel").options.length===3&&!$("renpyCycleModel").value&&$("renpyStartCycle").disabled);
    $("renpyCopyPath").value="D:\\Neutral-Copy";$("renpyCopyPath").dispatchEvent(new Event("input"));$("renpyCopyPath").focus();
    await wait(1300);
    check("pollKeepsDestinationDraftAndFocus",$("renpyCopyPath").value==="D:\\Neutral-Copy"&&document.activeElement===$("renpyCopyPath"));
    $("renpyClose").click();unsupported=true;
    await $("renpyPrepareEntry").onclick();
    check("unsupportedRuntimeCannotBeReenabledByModelRefresh",$("renpyPrepare").disabled&&$("renpyGameStatus").textContent.includes("Windows Python"));
    $("renpyClose").click();unsupported=false;
    await $("renpyPrepareEntry").onclick();
    const expected=$("renpyCopyPath").value;
    await $("renpyPrepare").onclick();await wait(350);
    check("explicitPreparationTargetsOriginalAndNewPath",prepared&&sent.command==="prepare"&&sent.args[0]===source&&sent.args[1]===expected);
    check("preparedWorkspaceOpensWithoutLosingDialog",studioState.project===child&&$("renpyGameDialog").open&&$("renpyPrepare").hidden);
    $("renpyCycleModel").value="fixture-b";$("renpyCycleModel").dispatchEvent(new Event("change"));
    offline=true;await $("renpyReloadModels").onclick();
    check("offlineModelBlocksCycleButAllowsBuild",$("renpyStartCycle").disabled&&!$("renpyBuild").disabled);
    offline=false;await $("renpyReloadModels").onclick();$("renpyCycleModel").value="fixture-b";$("renpyCycleModel").dispatchEvent(new Event("change"));
    $("renpyLanguageMode").value="launcher";
    await $("renpyStartCycle").onclick();
    check("cycleForcesLocalProviderAndChosenModel",sent.command==="pipeline"&&sent.args[0]===child&&sent.args[1].provider==="local"&&sent.args[1].execution_kind==="local"&&sent.args[1].model==="fixture-b"&&sent.args[2]==="launcher");
    check("cycleShowsNativeQueueWithoutBlockingModal",studioState.viewMode==="queue"&&!$("renpyGameDialog").open);
    state.busy=false;await $("renpyBuildEntry").onclick();await $("renpyBuild").onclick();
    check("verifiedBuildOffersSeparateLanguageLaunches",!$("renpyPlayRussian").hidden&&!$("renpyPlayOriginal").hidden&&!$("renpyRollback").hidden);
    await $("renpyPlayRussian").onclick();await $("renpyPlayOriginal").onclick();
    check("launchRequiresExplicitLanguageAndNeverStartsRealGameInProbe",launched.length===2&&launched[0][1]==="russian"&&launched[1][1]==="original");
    await $("renpyReadLog").onclick();
    check("engineLogIsExplicitlyReadableInModule",!$("renpyEngineLog").hidden&&!!$("renpyEngineLog").textContent);
    const help=$("renpyGameTitle").querySelector(".ux-help");help.focus();await wait(60);help.onmouseenter();
    check("explanationsLiveUnderAccessibleQuestionMark",help.getAttribute("aria-label").includes("Ren’Py")&&!$("contextTooltip").hidden);
    await $("renpyRollback").onclick();
    check("rollbackRemovesLaunchReadiness",$("renpyPlayRussian").hidden);
    $("renpyClose").click();
    await api.set_plugin_enabled("renpy",false);await pluginsUi.refresh();await pluginHost.ready();
    check("disabledPluginRemovesWholeContributedUi",!$("renpyPrepareEntry")&&!$("renpyBuildEntry")&&!$("renpyGameDialog"));
    api.plugin_call=real;await api.set_plugin_enabled("renpy",true);await pluginsUi.refresh();await pluginHost.ready();
    await pluginHost.instance("renpy").configure();
    check("noHorizontalOverflow",document.documentElement.scrollWidth<=innerWidth+1);
    window.__featureProbe={passed:true,checks,modelRequests:0,launchedGames:0,createdCopies:0};
  }catch(error){window.__featureProbe={passed:false,checks,error:String(error),stack:error.stack};}
})();

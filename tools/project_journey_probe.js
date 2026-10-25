void (async () => {
  const checks = {}, nativeRequest = request;
  let phase = "normal", offline = false, jobs = [];
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const check = (name, value) => {checks[name] = !!value; if (!value) throw Error(name);};
  const watchdog = setTimeout(() => {window.__featureProbe ||= {passed:false, checks, error:"Journey probe timeout"};}, 45000);
  try {
    for(let n=0;n<150&&!studioState.project;n++) await wait(40);
    await refresh();
    const before = await nativeRequest("state?project=" + studioState.project);
    request = async (path, data) => {
      if(path === "models") return {online:!offline,models:offline?[]:["fixture-local-a","fixture-local-b"]};
      if(path === "job") {jobs.push(structuredClone(data));return {job:99999};}
      if(path.startsWith("project-workflow?")) {
        const value = await nativeRequest(path, data);
        if(phase === "review") {value.empty = 0;value.translated=1;}
        if(phase === "paused") value.jobs=[{id:999,stage:"translate",provider:"cloud",state:"paused",worker_active:0}];
        return value;
      }
      return nativeRequest(path,data);
    };
    showView("home");await projectJourney.refresh();
    check("fourStageJourneyVisibleOnProjectHome",!$("projectHome").hidden&&$("projectJourney").querySelectorAll(".journey-step").length===4);
    check("installationIsExplicitlyUnavailable",$("journeyStep-install").dataset.state==="blocked"&&$("journeyBoundary").textContent.includes("модуль подготовки и сборки"));
    check("importDoesNotClaimWholeGameCoverage",$("journeyStep-files").textContent.includes("Полнота извлечения"));
    check("localPrerequisitesDoNotRequireAgentOrCloud",$("journeyModules").textContent.includes("не требуется плагин")&&$("journeyModules").textContent.includes("API-ключ"));
    check("untranslatedRowsDoNotOfferReview",$("journeyReview").disabled&&!$("journeyTranslate").disabled);
    await $("journeyTranslate").onclick();
    check("localLaunchHasExplicitModelPicker",$("journeyRunDialog").open&&$("journeyRunModel").options.length===3);
    check("multipleModelsRequireChoice",$("journeyStartLocal").disabled);
    offline=true;await $("journeyReloadModels").onclick();
    check("offlineOllamaExplainedAndBlocked",$("journeyRunModelState").textContent.includes("Запустите")&&$("journeyStartLocal").disabled);
    offline=false;await $("journeyReloadModels").onclick();
    $("journeyRunModel").value="fixture-local-b";$("journeyRunModel").dispatchEvent(new Event("change"));
    check("selectedModelEnablesExplicitStart",!$("journeyStartLocal").disabled);
    const chosenModel=$("journeyRunModel");chosenModel.focus();await projectJourney.refresh();
    check("pollPreservesModelSelectionAndFocus",document.activeElement===chosenModel&&chosenModel.value==="fixture-local-b");
    $("cloud_enabled").checked=true;$("provider").value="cloud";$("cloud_model").value="fixture-cloud";
    await $("journeyStartLocal").onclick();
    check("guideLaunchForcesLocalProviderAndWholeProject",jobs.length===1&&jobs[0].provider==="local"&&jobs[0].settings.provider==="local"&&jobs[0].settings.execution_kind==="local"&&jobs[0].settings.model==="fixture-local-b"&&jobs[0].file===null);
    check("guideDoesNotOverwriteProjectPreset",jobs[0].persist_settings===false&&$("provider").value==="cloud");
    check("launchNavigatesToQueue",studioState.viewMode==="queue"&&!$("journeyRunDialog").open);
    phase="review";showView("home");await projectJourney.refresh();
    await $("journeyReview").onclick();
    $("journeyRunModel").value="fixture-local-a";$("journeyRunModel").dispatchEvent(new Event("change"));
    check("reviewExplainsGameValidationLimit",$("journeyRunDialog").textContent.includes("шрифты"));
    await $("journeyStartLocal").onclick();
    check("reviewRunsLocallyWithoutCloudStage",jobs.length===2&&jobs[1].stage==="review"&&jobs[1].provider==="local");
    phase="paused";showView("home");await projectJourney.refresh();await $("journeyTranslate").onclick();
    $("journeyRunModel").value="fixture-local-a";$("journeyRunModel").dispatchEvent(new Event("change"));
    check("existingPausedQueueBlocksNewGuideLaunch",!$("journeyRunBlocked").hidden&&$("journeyStartLocal").disabled&&!$("journeyOpenQueue").hidden);
    $("journeyRunClose").click();
    phase="normal";await projectJourney.refresh();await $("journeyExport").onclick();
    check("exportExplainsCorpusIsNotGamePatch",$("journeyExportDialog").open&&$("journeyExportInfo").textContent.includes("JSON-корпус")&&$("journeyExportInfo").textContent.includes("не готовый патч"));
    await $("journeyOpenExport").onclick();
    check("exportStillRequiresDestinationAndExplicitSave",$("exportDialog").open&&$("exportPath").value.endsWith(".json"));
    $("exportDialog").close();
    const after=await nativeRequest("state?project="+studioState.project);
    check("previewTestsDoNotLaunchRealWorkersOrChangeSavedSettings",after.jobs.every(j=>!j.worker_active)&&JSON.stringify(before.projects.map(p=>p.settings))===JSON.stringify(after.projects.map(p=>p.settings))&&after.jobs.length===before.jobs.length);
    $("provider").value="local";$("cloud_enabled").checked=false;request=nativeRequest;
    showView("home");await projectJourney.refresh();$("journeyModules").open=false;
    $("toast").style.display="none";
    check("noHorizontalOverflow",document.documentElement.scrollWidth<=innerWidth);
    window.__featureProbe={passed:true,checks,modelRequests:0,launchedWorkers:0};
  } catch(error) {request=nativeRequest;window.__featureProbe={passed:false,checks,error:error.message,stack:error.stack};}
  finally {clearTimeout(watchdog);}
})();

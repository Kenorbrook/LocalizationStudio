(async () => {
  try {
    const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
    for (let n=0;n<150&&!studioState.snapshot;n++)await wait(40);
    await pluginsUi.open();
    const screen=window.__pluginScreen;
    if(screen==="settings")await hermesUi.openSettings();
    if(screen==="sources") {workspaceUi.openSettings("files");await hermesUi.refresh();}
    if(screen==="errors") {showView("errors");await loadErrors();await hermesUi.refresh();$("errorRecoveryDetails").open=true;}
    const checks={catalogAvailable:!!$("openPlugins"),namedDialog:[...document.querySelectorAll("dialog[open]")].every(node=>!!node.getAttribute("aria-labelledby")),noHorizontalOverflow:document.documentElement.scrollWidth<=innerWidth};
    if(screen==="empty")checks.noProjectRequired=!studioState.project&&!$("pluginsPane").hidden;
    window.__featureProbe={passed:Object.values(checks).every(Boolean),checks,screen};
  }catch(error){window.__featureProbe={passed:false,error:error.message,stack:error.stack};}
})();

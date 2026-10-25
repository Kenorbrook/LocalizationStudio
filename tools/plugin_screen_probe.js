void (async () => {
  try {
    const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
    for (let n=0;n<150&&!studioState.snapshot;n++)await wait(40);
    await pluginsUi.open();
    const screen=window.__pluginScreen;
    const original=window.pywebview.api.plugin_call;
    window.pywebview.api.plugin_call=(id,command,args)=>command==="models"?window.pywebview.api.get_hermes_models():original(id,command,args);
    if (["sources","errors"].includes(screen)) { await window.pywebview.api.set_plugin_enabled("hermes",true); await pluginsUi.refresh(); await pluginHost.ready(); }
    const hermesUi={openSettings:()=>pluginHost.action("hermes","configure"),refresh:()=>pluginHost.instance("hermes").refresh()};
    if(screen==="settings") {
      window.pywebview.api.get_hermes_models=async()=>({online:true,models:[{name:"fixture-agent",size:4000000000,details:{parameter_size:"4B"}}],error:""});
      await hermesUi.openSettings();
      $("hermesModel").value="fixture-agent";$("hermesModel").dispatchEvent(new Event("input"));
    }
    if(screen==="sources") {workspaceUi.openSettings("files");await hermesUi.refresh();}
    if(screen==="errors") {showView("errors");await loadErrors();await hermesUi.refresh();$("errorRecoveryDetails").open=true;}
    const checks={catalogAvailable:!!$("openPlugins"),namedDialog:[...document.querySelectorAll("dialog[open]")].every(node=>!!node.getAttribute("aria-labelledby")),noHorizontalOverflow:document.documentElement.scrollWidth<=innerWidth};
    if(screen==="empty")checks.noProjectRequired=!studioState.project&&!$("pluginsPane").hidden;
    window.__featureProbe={passed:Object.values(checks).every(Boolean),checks,screen};
  }catch(error){window.__featureProbe={passed:false,error:error.message,stack:error.stack};}
})();

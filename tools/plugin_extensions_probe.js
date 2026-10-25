void (async () => {
  const checks = {};
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  function check(name, value) { window.__extensionStage=name; checks[name] = !!value; if (!value) throw Error(name); }
  async function sync() { await pluginsUi.refresh(); await pluginHost.ready(); }
  let api;
  const watchdog = setTimeout(() => { window.__featureProbe ||= {passed:false,checks,error:"Extension probe timeout",stage:window.__extensionStage}; },45000);
  async function install(path) { await api.preview_plugin_package(path); await api.install_plugin_package(); await sync(); }
  async function toggle(enabled) { await api.set_plugin_enabled("qa-extension",enabled); await sync(); }
  try {
    for(let n=0;n<150&&!studioState.project;n++)await wait(40);
    api=window.pywebview.api;
    window.__extensionStage="initial-refresh";
    await refresh(); await sync();
    const baseline=getComputedStyle(document.querySelector("header")).backgroundColor;
    const preview=await api.preview_plugin_package(window.__extensionPackages[0]);
    check("packagePreviewShowsVersionAndPermissions",preview.version==="1.0.0"&&preview.permissions.includes("theme"));
    await api.install_plugin_package(); await sync();
    check("newPluginDisabledAndLazy",!$("qaPluginNavigation")&&!pluginHost.diagnostics().loaded.includes("qa-extension"));
    await toggle(true);
    check("pluginCanAddMenuAndProjectTab",!!$("qaPluginNavigation")&&!!$("settingsTab-qa-extension-inspector"));
    check("themeChangesWholeWindow",getComputedStyle(document.querySelector("header")).backgroundColor==="rgb(20, 60, 90)");
    showView("text");await loadRows();
    check("pluginCanAddRecordActions",!!document.querySelector("[data-qa-record-action]"));
    const editor=document.querySelector(".record textarea");
    if(editor){editor.focus();editor.value="Neutral user draft";editor.dispatchEvent(new Event("input"));}
    window.dispatchEvent(new Event("qa-plugin-event"));await wait(600);
    check("managedListenerAndIntervalRun",window.__extensionEvents.clicks===1&&window.__extensionEvents.ticks>0);
    await toggle(false);
    const stoppedTicks=window.__extensionEvents.ticks;
    window.dispatchEvent(new Event("qa-plugin-event"));await wait(600);
    check("disableRemovesMenuTabRecordActionsAndStyles",!$("qaPluginNavigation")&&!$("settingsTab-qa-extension-inspector")&&!document.querySelector("[data-qa-record-action]")&&!document.querySelector('[data-plugin-style="qa-extension"]'));
    check("disableRestoresTheme",getComputedStyle(document.querySelector("header")).backgroundColor===baseline);
    check("disableDisposesTimersAndListeners",window.__extensionEvents.clicks===1&&window.__extensionEvents.ticks===stoppedTicks&&window.__extensionEvents.disposals===1);
    check("disablePreservesEditorDraftAndFocus",!editor||(editor.isConnected&&editor.value==="Neutral user draft"&&document.activeElement===editor));
    studioState.dirty.clear();
    await toggle(true);
    check("reenableMountsOnce",document.querySelectorAll("#qaPluginNavigation").length===1&&document.querySelectorAll("#settingsTab-qa-extension-inspector").length===1);
    window.__extensionThrow=true;await refresh();window.__extensionThrow=false;
    check("pluginHookErrorDoesNotBreakCore",studioState.snapshot.projects.length>0&&pluginHost.diagnostics().errors["qa-extension"].includes("Neutral"));
    await install(window.__extensionPackages[1]);
    check("packageUpdateReplacesOnlyItsUi",$("qaPluginNavigation").textContent.includes("1.0.1")&&getComputedStyle(document.querySelector("header")).backgroundColor==="rgb(60, 20, 90)"&&document.querySelectorAll("#qaPluginNavigation").length===1);
    check("updateKeepsEnabledAndOffersRollback",(await api.get_plugins()).plugins.some(p=>p.id==="qa-extension"&&p.enabled&&p.version==="1.0.1"&&p.rollback_available));
    await api.rollback_plugin("qa-extension");await sync();
    check("rollbackRestoresPreviousUiWithoutRestart",$("qaPluginNavigation").textContent.includes("1.0.0")&&getComputedStyle(document.querySelector("header")).backgroundColor==="rgb(20, 60, 90)"&&(await api.get_update_state()).current==="0.6.0");
    await toggle(false);
    await install(window.__extensionPackages[2]);
    await api.set_plugin_enabled("qa-broken",true);await sync();
    check("failedActivationCleansPartialContributions",!$("qaPartialNode")&&!document.querySelector('[data-plugin-style="qa-broken"]')&&!!pluginHost.diagnostics().errors["qa-broken"]);
    await api.set_plugin_enabled("qa-broken",false);await sync();
    check("coreRetryAvailableWithoutAgent",!$("hermesSources")&&!!$("automaticErrorRetry"));
    await pluginsUi.open();
    window.__featureProbe={passed:true,checks};
  } catch(error) { window.__featureProbe={passed:false,checks,error:error.message,stack:error.stack,host:pluginHost.diagnostics()}; } finally {clearTimeout(watchdog);}
})();

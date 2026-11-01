void (async () => {
  const checks = {};
  const check = (name, value) => {
    checks[name] = !!value;
    if (!value) throw Error(name);
  };
  try {
    const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    for (let n = 0; n < 150 && !studioState.snapshot; n++) await wait(40);
    await pluginHost.ready();
    showView("errors");
    await loadErrors();
    const instance = pluginHost.instance("hermes");
    await instance.refresh();
    const api = window.pywebview.api;
    const real = api.plugin_call;
    let phase = null, mode = null, cancelled = false;
    api.plugin_call = async (id, command, args) => {
      if (id !== "hermes") return real(id, command, args);
      if (command === "run") { mode = args[1]; phase = "running"; return {}; }
      if (command === "cancel") { cancelled = true; phase = "ready"; return {}; }
      const state = await real(id, command, args);
      if (command === "state" && phase) return {...state,
        busy: phase === "running", phase, report_mode: "repair_errors",
        message: phase === "running" ? "Проверка решения 1/2 · строка #1" : "Обработано 2 из 2",
        report: "Исправлено: 1 · Подтверждено без изменения текста: 1",
        repair_progress: {fix: 1, keep: 1, attention: 0, done: 2, total: 2},
        error_resolution: {eligible: phase === "running" ? 2 : 0, protected: 1, limit: 50},
      };
      return state;
    };
    check("independentPluginVersion", (await api.get_plugins()).plugins.some(p => p.id === "hermes" && p.version === "1.1.0"));
    check("primaryActionEnabled", !$("hermesExplainErrors").disabled && $("hermesExplainErrors").textContent.includes("исправить"));
    check("eligibleCountVisible", $("hermesErrorsReason").textContent.includes("3"));
    await $("hermesExplainErrors").onclick();
    check("startsResolutionMode", mode === "repair_errors");
    check("showsResultDialog", $("hermesReportDialog").open);
    check("busyPreventsDuplicate", $("hermesExplainErrors").disabled);
    check("liveProgressNamesRecord", $("hermesErrorProgress").textContent.includes("#1"));
    check("resolutionExplainsAutomaticSaving", $("hermesReportScope").textContent.includes("сохраняются в проекте"));
    check("importNavigationHiddenForResolution", $("hermesReportImport").hidden);
    check("stopAvailable", !$("hermesErrorsStop").hidden && !$("stopHermesTask").hidden);
    await $("hermesErrorsStop").onclick();
    check("stopUsesPluginCancellation", cancelled);
    check("summaryCountsVisible", $("hermesErrorProgress").textContent.includes("Исправлено: 1") && $("hermesErrorProgress").textContent.includes("Подтверждено: 1"));
    check("emptyEligibleDisablesAction", $("hermesExplainErrors").disabled && $("hermesErrorsReason").textContent.includes("защищены"));
    check("stoppedHidesStopButton", $("hermesErrorsStop").hidden);
    phase = null;
    await instance.refresh();
    check("researchRemainsReadOnly", $("hermesReportScope").textContent.includes("не установил перевод"));
    $("hermesReportDialog").close();
    check("noHorizontalOverflow", document.documentElement.scrollWidth <= innerWidth);
    await api.set_plugin_enabled("hermes", false);
    await pluginsUi.refresh(); await pluginHost.ready();
    check("disableRemovesPluginControls", !$("hermesExplainErrors") && !$("hermesErrorsStop"));
    window.__featureProbe = {passed: true, checks};
  } catch (error) {
    window.__featureProbe = {passed: false, checks, error: error.message, stack: error.stack};
  }
})();

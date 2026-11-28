void (async () => {
  const checks = {};
  const check = (name, value) => {
    checks[name] = !!value;
    if (!value) throw Error(name);
  };
  try {
    for (let n = 0; n < 150 && !studioState.project; n++)
      await new Promise((resolve) => setTimeout(resolve, 40));
    await refresh();
    check("dashboardVisible", $("projectDashboard").getClientRects().length);
    check(
      "preparationSeparate",
      !$("projectPreparation").open &&
        !$("projectHome").contains($("projectJourney")),
    );
    await $("projectPreparationEntry").onclick();
    check(
      "preparationOpens",
      $("projectPreparation").open &&
        $("journeyStep-files").textContent.includes("Импортировано"),
    );
    await $("journeyExport").onclick();
    check(
      "exportOpens",
      $("journeyExportDialog").open &&
        $("journeyExportFile").options.length > 0,
    );
    $("journeyExportDialog").close();
    $("projectPreparation").close();
    await pluginsUi.open();
    check(
      "backToProjectVisible",
      !$("projectBack").hidden && !$("activeProjectJob").hidden,
    );
    await $("projectBack").onclick();
    check(
      "returnsToProject",
      studioState.viewMode === "home" && $("activeProjectJob").hidden,
    );
    window.__featureProbe = { passed: true, checks };
  } catch (error) {
    window.__featureProbe = {
      passed: false,
      checks,
      error: error.message,
      stack: error.stack,
    };
  }
})();

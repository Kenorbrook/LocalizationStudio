void (async () => {
  try {
    for (let i = 0; i < 20 && !studioState.snapshot; i++)
      await new Promise((r) => setTimeout(r, 100));
    const expected = window.__visibilityProject;
    for (let i = 0; i < 3; i++) await refresh();
    if (studioState.project !== expected)
      throw Error("Finished project was replaced by another selection");
    if (
      ![...$("projects").options].some(
        (option) => Number(option.value) === expected,
      )
    )
      throw Error("Finished project disappeared from selector");
    if (studioState.snapshot.counts.translated !== 1)
      throw Error("Saved translation is unavailable");
    const button = $("projectArchiveMenu");
    if (
      !document.querySelector("#projectSidebar").contains(button) ||
      !button.getClientRects().length
    )
      throw Error("Archive button is not visible beside projects");
    const root = studioState.snapshot.projects.find(
      (p) => p.id === expected,
    ).root;
    button.click();
    if (!$("projectManager").open) throw Error("Archive menu did not open");
    $("projectManagerClose").click();
    await $("hideProject").onclick();
    if (
      [...$("projects").options].some(
        (option) => Number(option.value) === expected,
      )
    )
      throw Error("Archived project still appears");
    $("newProject").click();
    $("rootPath").value = root;
    await $("openProject").onclick();
    if (
      studioState.project !== expected ||
      !$("toast").textContent.includes("восстановлен из архива")
    )
      throw Error("Reopening the folder failed to restore the project");
    $("newProject").click();
    await $("openProject").onclick();
    if (
      !$("toast").textContent.includes("уже добавлен") ||
      studioState.snapshot.projects.length !== 2
    )
      throw Error("Duplicate opening was not identified");
    window.__featureProbe = {
      passed: true,
      project: studioState.project,
      checks: {
        completed_project_visible: true,
        selection_survives_refresh: true,
        translation_available: true,
        archive_visible: true,
        same_folder_restores: true,
        duplicate_warns: true,
      },
    };
  } catch (error) {
    window.__featureProbe = {
      passed: false,
      error: error.message,
      stack: error.stack,
    };
  }
})();

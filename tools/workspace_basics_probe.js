void (async () => {
  const checks = [];
  function check(condition, name) {
    if (!condition) throw new Error(name);
    checks.push(name);
  }
  try {
    for (let attempt = 0; attempt < 100 && !studioState.snapshot; attempt++)
      await new Promise((resolve) => setTimeout(resolve, 50));
    await refresh(true);
    check(studioState.project === 1, "initial project");
    check(
      !$("activeProjectJob").hidden &&
        $("activeProjectJob").title.includes("fixture-model"),
      "task in another project is visible",
    );
    await $("activeProjectJob").onclick();
    check(studioState.project === 2, "navigate to paused task");
    check(
      $("taskCard").contains($("progressError")) &&
        $("progressText").textContent.includes("1 / 2"),
      "progress and error appear in the task card",
    );
    check(
      $("live").textContent.includes("The door is open."),
      "source preview",
    );
    check(
      $("activeSettings").textContent.includes("fixture-model"),
      "running model label",
    );
    check($("setupCard").hidden, "secondary controls hidden for a paused job");

    const native = window.pywebview.api;
    check(
      typeof native.choose_project_folder === "function",
      "native folder picker is exposed",
    );
    const choose = native.choose_project_folder;
    native.choose_project_folder = async () => "C:\\Example\\Novel";
    try {
      $("newProject").click();
      await $("chooseProjectFolder").onclick();
      check(
        $("rootPath").value === "C:\\Example\\Novel",
        "selected folder fills path",
      );
    } finally {
      native.choose_project_folder = choose;
    }
    const clipboard = native.clipboard_text;
    native.clipboard_text = async () => "Appended";
    try {
      $("rootPath").select();
      $("rootPath").dispatchEvent(
        new MouseEvent("contextmenu", { bubbles: true, cancelable: true }),
      );
      const menu = document.querySelector("#projectDialog .text-menu");
      check(!!menu, "right click opens context menu in dialog");
      await [...menu.querySelectorAll("button")]
        .find((button) => button.textContent === "Вставить")
        .onclick();
      check($("rootPath").value === "Appended", "paste replaces selected text");
    } finally {
      native.clipboard_text = clipboard;
    }
    $("projectDialog").close();

    const catalog = await native.cloud_connections();
    check(
      catalog.find((item) => item.id === "fixture-global").projects === null,
      "global key metadata",
    );
    check(
      catalog.find((item) => item.id === "fixture-scoped").projects[0] === 1,
      "scoped key metadata",
    );
    $("projectSettingsButton").click();
    $("settingsTab-integrations").click();
    $("cloud_enabled").checked = true;
    $("cloud_enabled").dispatchEvent(new Event("change", { bubbles: true }));
    const picker = $("cloudConnectionPicker");
    check(
      [...picker.options].some((option) => option.value === "fixture-global"),
      "global connection available",
    );
    check(
      ![...picker.options].some((option) => option.value === "fixture-scoped"),
      "other project connection hidden",
    );
    picker.value = "fixture-global";
    await picker.onchange();
    check(!$("cloudModelOptions").hidden, "discovered models are selectable");
    $("cloudModelOptions").value = "second-model";
    $("cloudModelOptions").onchange();
    check(
      settings().cloud_connection === "fixture-global" &&
        settings().cloud_model === "second-model",
      "job selects key and model independently",
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

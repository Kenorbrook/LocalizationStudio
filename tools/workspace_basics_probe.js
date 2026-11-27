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
    check(
      $("mainAction")
        .closest(".ux-primary-action")
        .nextElementSibling.contains($("activeSettings")),
      "executor summary is directly below the primary action",
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
    const executor = $("executionModel");
    check(
      [...executor.options].some(
        (option) => option.value === "api:fixture-global",
      ),
      "global API key is visible in the executor chooser",
    );
    check(
      ![...executor.options].some(
        (option) => option.value === "api:fixture-scoped",
      ),
      "executor chooser excludes keys scoped to another project",
    );
    executor.value = "api:fixture-global";
    executor.onchange();
    check(
      $("cloud_enabled").checked &&
        $("settingsPanel-translation").contains($("cloudModelOptions")) &&
        !$("executorModelFields").hidden,
      "choosing the key enables API and shows models on the translation tab",
    );
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
    $("settingsTab-translation").click();
    check(!$("cloudModelOptions").hidden, "discovered models are selectable");
    $("cloudModelOptions").value = "second-model";
    $("cloudModelOptions").onchange();
    check(
      settings().cloud_connection === "fixture-global" &&
        settings().cloud_model === "second-model",
      "job selects key and model independently",
    );
    await $("saveSettings").onclick();
    check(
      $("activeSettings").textContent ===
        "API-ключ «fixture-global» · second-model",
      "paused job shows the key name and model chosen for continuation",
    );
    const originalRequest = request;
    let control;
    request = (path, body) => {
      if (path === "control") {
        control = body;
        return Promise.resolve({ ok: true });
      }
      return originalRequest(path, body);
    };
    try {
      await $("mainAction").onclick();
      check(
        control?.mode === "resume" &&
          control.settings.cloud_connection === "fixture-global" &&
          control.settings.cloud_model === "second-model",
        "resume sends the same connection and model as the label",
      );
    } finally {
      request = originalRequest;
    }

    $("appSettings").click();
    const settingsPanel = $("updatesDialog");
    const catalogDialog = $("cloudCatalogDialog");
    const editorDialog = $("cloudEditorDialog");
    const addCloud = $("addCloudConnection");
    const manageCloud = $("manageCloudConnections");
    const cloudTitle = $("cloudConnections").querySelector("h3");
    const closeButton = $("changeCloseChoice");
    check(
      settingsPanel.contains(addCloud) &&
        settingsPanel.contains(manageCloud) &&
        !settingsPanel.querySelector(".cloud-list") &&
        Math.abs(
          cloudTitle.getBoundingClientRect().top -
            addCloud.getBoundingClientRect().top,
        ) < 16 &&
        addCloud.getBoundingClientRect().right <
          manageCloud.getBoundingClientRect().right,
      "API actions are next to the heading with no embedded list",
    );
    await addCloud.onclick();
    check(
      editorDialog.open &&
        !catalogDialog.open &&
        editorDialog.querySelector('input[type="password"]').type ===
          "password",
      "add opens only the masked-key form",
    );
    editorDialog.close();
    await manageCloud.onclick();
    check(
      catalogDialog.open &&
        catalogDialog.offsetWidth > settingsPanel.offsetWidth &&
        catalogDialog.textContent.includes("first-model") &&
        catalogDialog.textContent.includes("Все проекты"),
      "wide API catalog lists models and project scope",
    );
    const keyCard = [...catalogDialog.querySelectorAll(".cloud-entry")].find(
      (card) => card.querySelector("strong").textContent === "fixture-scoped",
    );
    [...keyCard.querySelectorAll("button")]
      .find((button) => button.textContent === "Редактировать")
      .click();
    const nameField = editorDialog.querySelector("input");
    check(
      editorDialog.open &&
        catalogDialog.open &&
        nameField.value === "fixture-scoped",
      "edit opens the selected key form above the catalog",
    );
    nameField.value = "Renamed key";
    await editorDialog
      .querySelector("form")
      .onsubmit(new Event("submit", { cancelable: true }));
    check(
      !editorDialog.open &&
        catalogDialog.open &&
        catalogDialog.textContent.includes("Renamed key"),
      "edited key returns to refreshed catalog",
    );
    catalogDialog.close();
    check(
      !closeButton.disabled &&
        $("closePrefsMenu").querySelector("h3 > .ux-help"),
      "close action is editable with help beside the heading",
    );
    for (const [choice, saved, label] of [
      ["tray", "tray", "Сворачивать в трей"],
      ["exit", "exit", "Закрывать и останавливать перевод"],
      ["ask", null, "Спрашивать каждый раз"],
    ]) {
      await closeButton.onclick();
      const closeDialog = $("closeChoiceDialog");
      check(closeDialog.open, "open close behavior chooser for " + choice);
      $("closeChoiceSelect").value = choice;
      await closeDialog
        .querySelector("form")
        .onsubmit(new Event("submit", { cancelable: true }));
      check(
        !closeDialog.open &&
          (await native.get_close_state()).choice === saved &&
          $("closeChoiceStatus").textContent === label,
        "persist and display close behavior " + choice,
      );
    }
    const previousUpdateState = await native.get_update_state();
    displayUpdateState({
      ...previousUpdateState,
      latest: "99.0.0",
      phase: "ready",
    });
    await $("installUpdate").onclick();
    check(
      $("infoDialog").open &&
        $("infoContent").childElementCount === 1 &&
        $("infoContent").firstElementChild.id === "confirmInstallUpdate",
      "update confirmation contains only the install action",
    );
    $("infoDialog").close();
    displayUpdateState(previousUpdateState);
    settingsPanel.close();
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

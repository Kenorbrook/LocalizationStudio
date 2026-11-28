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
      $("activeProjectJob").hidden &&
        $("projectSidebar").contains($("newProject")),
      "project home keeps the header clear and open-project beside the picker",
    );
    $("projects").value = 2;
    await $("projects").onchange();
    check(studioState.project === 2, "navigate to paused task");
    check(
      !$("projectDashboard").closest("[hidden]") &&
        $("projectPreparation").contains($("projectJourney")) &&
        !$("projectPreparation").open &&
        $("compactJobs").textContent.includes("fixture-model") &&
        getComputedStyle($("taskCard")).display === "none",
      "project dashboard separates preparation and uses compact task cards",
    );
    for (
      let attempt = 0;
      attempt < 30 && !document.querySelector(".dashboard-bar");
      attempt++
    )
      await new Promise((resolve) => setTimeout(resolve, 75));
    check(
      !!document.querySelector(".dashboard-bar") &&
        document
          .querySelector(".dashboard-models")
          .textContent.includes("fixture-model"),
      "dashboard plots actual saved output by model",
    );
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
    const projectDialog = $("projectSettingsDialog");
    check(
      !projectDialog.contains($("source_language")) &&
        !projectDialog.contains($("executionModel")),
      "project settings omit language and executor controls",
    );
    check(
      $("analyzeKnowledge").disabled &&
        $("analyzeKnowledge").parentElement.classList.contains(
          "disabled-reason",
        ),
      "blocked analysis offers a reason",
    );
    $("settingsTab-context").click();
    $("analyzeKnowledge").parentElement.onmouseenter();
    check(
      !$("contextTooltip").hidden &&
        $("contextTooltip").textContent.includes("текущих задач"),
      "disabled analysis explains its specific blocker",
    );
    $("analyzeKnowledge").parentElement.onmouseleave();
    $("settingsTab-files").click();
    await $("importButton").onclick();
    check(
      $("importDialog").open &&
        !$("importExtra").open &&
        !$("uploadFiles").closest("details"),
      "file selection is primary and alternate import methods are folded",
    );
    $("importDialog").close();
    $("settingsTab-integrations").click();
    await $("projectManageApiKeys").onclick();
    check(
      $("cloudCatalogDialog").open && !$("updatesDialog").open,
      "project opens the API key catalog directly",
    );
    $("cloudCatalogDialog").close();
    $("cloud_enabled").checked = true;
    await $("saveSettings").onclick();
    check(
      $("activeSettings").textContent.includes("fixture-model"),
      "paused job keeps its saved executor",
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
          control.preserve_project_settings &&
          !control.settings,
        "resume keeps the existing queue settings",
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
    const retrySetting = [...editorDialog.querySelectorAll("label")].find(
      (label) => label.textContent.includes("Автовозобновление"),
    ).firstElementChild;
    check(retrySetting.checked, "API auto-resume is enabled for existing keys");
    retrySetting.checked = false;
    await editorDialog
      .querySelector("form")
      .onsubmit(new Event("submit", { cancelable: true }));
    check(
      !editorDialog.open &&
        catalogDialog.open &&
        catalogDialog.textContent.includes("Renamed key"),
      "edited key returns to refreshed catalog",
    );
    check(
      (await native.cloud_connections()).find(
        (item) => item.id === "fixture-scoped",
      ).retry_unavailable === false,
      "API auto-resume can be disabled for one key",
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
    request = async (path, body) => {
      const response = await originalRequest(path, body);
      if (!path.startsWith("state?")) return response;
      const retryAt = Date.now() / 1000 + 30;
      Object.assign(response.jobs[0], {
        state: "running",
        worker_active: 1,
        retry_at: retryAt,
        error: "Провайдер временно недоступен: HTTP 504",
      });
      Object.assign(response.activity[0], {
        state: "running",
        retry_at: retryAt,
      });
      return response;
    };
    try {
      await refresh(true);
      check(
        $("jobState").textContent === "Ожидание API" &&
          $("compactJobs").textContent.includes("Ожидание API") &&
          $("progressError").textContent.includes("повтор через") &&
          !$("mainAction").disabled,
        "API wait shows retry countdown and leaves pause available",
      );
    } finally {
      request = originalRequest;
    }
    let newTaskPayload;
    let selectedQueuePath;
    const addedQueue = {
      id: 99,
      state: "paused",
      stage: "review",
      provider: "local",
      settings: JSON.stringify({ model: "local-fixture" }),
      done: 1,
      total: 3,
      current: null,
      parallel: 1,
      retry_at: 0,
    };
    request = async (path, body) => {
      if (path === "models") return { online: true, models: ["local-fixture"] };
      if (path === "parallel-job") {
        newTaskPayload = body;
        return { job: 99 };
      }
      if (path.startsWith("job-log?id=99"))
        return { text: "Review fixture log" };
      const response = await originalRequest(path, body);
      if (path.startsWith("queue?")) selectedQueuePath = path;
      if (path.startsWith("state?")) {
        response.jobs[0].total = 8;
        if (newTaskPayload) response.jobs.push(addedQueue);
      }
      return response;
    };
    try {
      await refresh(true);
      showView("queue");
      await $("startParallelQueue").onclick();
      const parallelForm = $("parallelQueueDialog").querySelector("form");
      const [purpose, source, , executor] =
        parallelForm.querySelectorAll("select");
      check(
        $("parallelQueueDialog").open &&
          purpose.value === "review" &&
          [...executor.options].some(
            (item) => item.value === "api:fixture-global",
          ),
        "parallel launch offers separate local and API executors",
      );
      executor.value = "local:local-fixture";
      $("queueSourceLanguage").value = "German";
      executor.onchange();
      await parallelForm.onsubmit(new Event("submit", { cancelable: true }));
      check(
        newTaskPayload.stage === "review" &&
          newTaskPayload.settings.source_language === "German" &&
          $("queueTargetLanguage").tagName === "SELECT" &&
          newTaskPayload.settings.model === "local-fixture" &&
          studioState.job.id === 99 &&
          selectedQueuePath.endsWith("&job=99") &&
          $("jobLog").textContent.includes("Review fixture log"),
        "parallel selection targets the new task and its own queue and log",
      );
      const reviewOptions = document.querySelector(
        '.queue-options[data-job="99"]',
      );
      reviewOptions.open = true;
      const action = (name) =>
        [...reviewOptions.querySelectorAll("button")].find(
          (item) => item.textContent === name,
        );
      await action("Журнал").onclick();
      check(
        $("fullJobLog").open &&
          $("fullJobLog").textContent.includes("Review fixture log"),
        "queue menu opens a separate journal for the selected task",
      );
      $("fullJobLog").close();
      await action("Настроить").onclick();
      check(
        $("parallelQueueDialog").open &&
          $("parallelQueueDialogTitle").textContent.includes("#99") &&
          !$("parallelQueueDialog").querySelector(".parallel-tuning").hidden &&
          [...parallelForm.querySelectorAll("label")].some((item) =>
            item.textContent.includes("Лимит ответа, токенов"),
          ),
        "queue settings name tokens and show an independent configuration form",
      );
      $("parallelQueueDialog").close();
      await $("splitParallelQueue").onclick();
      check(
        $("parallelQueueDialog").open &&
          purpose.value === "translate" &&
          !!source.value &&
          $("parallelQueueDialogTitle").textContent === "Разделить очередь",
        "split chooses a live translation queue and preserves its stage",
      );
      const target = +source.value;
      executor.value = "api:fixture-global";
      executor.onchange();
      parallelForm.querySelector("[list=parallelCloudModels]").value =
        "second-model";
      await parallelForm.onsubmit(new Event("submit", { cancelable: true }));
      check(
        newTaskPayload.split_from === target &&
          newTaskPayload.provider === "cloud" &&
          newTaskPayload.settings.cloud_connection === "fixture-global" &&
          newTaskPayload.settings.cloud_model === "second-model",
        "split sends source queue and new API executor independently",
      );
    } finally {
      request = originalRequest;
    }
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

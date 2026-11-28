"use strict";

// Owns the workspace layout and project settings; existing controls keep their handlers.
const workspaceUi = (() => {
  let helpSerial = 0;
  const tooltip = element("div", undefined, "ux-tooltip");
  tooltip.id = "contextTooltip";
  tooltip.role = "tooltip";
  tooltip.hidden = true;
  document.body.append(tooltip);
  let helpOwner = null;
  function hideHelp() {
    tooltip.hidden = true;
    tooltip.classList.remove("disabled-explanation");
    helpOwner?.removeAttribute("aria-describedby");
    helpOwner = null;
  }
  function showHelp(button, content) {
    const text = typeof content === "function" ? content() : content;
    if (!text) return;
    hideHelp();
    (button.closest("dialog") || document.body).append(tooltip);
    tooltip.textContent = text;
    tooltip.hidden = false;
    helpOwner = button;
    button.setAttribute("aria-describedby", tooltip.id);
    const rect = button.getBoundingClientRect();
    const width = tooltip.offsetWidth;
    tooltip.style.left =
      Math.max(12, Math.min(rect.left, innerWidth - width - 12)) + "px";
    const height = tooltip.offsetHeight;
    tooltip.style.top =
      (rect.bottom + height + 16 < innerHeight
        ? rect.bottom + 8
        : Math.max(12, rect.top - height - 8)) + "px";
  }
  function help(text, name = "Подсказка") {
    const button = element("button", "?", "ux-help");
    button.type = "button";
    button.dataset.helpId = ++helpSerial;
    button.setAttribute("aria-label", name);
    button.onmouseenter = button.onfocus = () => showHelp(button, text);
    button.onmouseleave = button.onblur = hideHelp;
    button.onclick = (event) => {
      event.preventDefault();
      event.stopPropagation();
      showHelp(button, text);
    };
    button.onkeydown = (event) => {
      if (event.key === "Escape") {
        hideHelp();
        event.stopPropagation();
      }
    };
    return button;
  }
  function disabledReason(button, reason) {
    const wrapper = element("span", undefined, "disabled-reason");
    wrapper.append(button);
    const sync = () => {
      wrapper.tabIndex = button.disabled ? 0 : -1;
      if (!button.disabled && helpOwner === wrapper) hideHelp();
    };
    const reveal = () => {
      if (!button.disabled) return;
      showHelp(wrapper, reason);
      tooltip.classList.add("disabled-explanation");
    };
    wrapper.onmouseenter = wrapper.onfocus = reveal;
    wrapper.onmouseleave = wrapper.onblur = hideHelp;
    wrapper.onkeydown = (event) => {
      if (event.key === "Escape" && helpOwner === wrapper) {
        hideHelp();
        event.stopPropagation();
      }
    };
    new MutationObserver(sync).observe(button, {
      attributes: true,
      attributeFilter: ["disabled"],
    });
    sync();
    return wrapper;
  }
  addEventListener("resize", hideHelp);
  document.addEventListener("scroll", hideHelp, true);
  document.addEventListener("pointerdown", (event) => {
    if (!event.target.closest(".ux-help")) hideHelp();
  });

  const dialog = element("dialog", undefined, "ux-project-settings");
  dialog.id = "projectSettingsDialog";
  dialog.setAttribute("aria-labelledby", "projectSettingsTitle");
  const heading = element("div", undefined, "ux-dialog-heading");
  const headingText = element("div");
  const title = element("h1", "Настройки проекта");
  title.id = "projectSettingsTitle";
  const projectName = element("div", undefined, "sub");
  projectName.id = "settingsProjectName";
  headingText.append(title, projectName);
  const close = element("button", "×", "ux-close");
  close.id = "closeProjectSettings";
  close.setAttribute("aria-label", "Отменить изменения и закрыть настройки");
  heading.append(headingText, close);
  const body = element("div", undefined, "ux-settings-layout");
  const tabs = element("nav", undefined, "ux-settings-tabs");
  tabs.role = "tablist";
  tabs.setAttribute("aria-label", "Разделы настроек проекта");
  tabs.setAttribute("aria-orientation", "vertical");
  const content = element("div", undefined, "ux-settings-content");
  body.append(tabs, content);
  const footer = element("div", undefined, "ux-dialog-footer");
  const cancel = element("button", "Отмена");
  cancel.id = "cancelProjectSettings";
  setText("saveSettings", "Сохранить изменения");
  $("saveSettings").classList.remove("full");
  $("saveSettings").classList.add("primary");
  footer.append(cancel, $("saveSettings"));
  dialog.append(heading, body, footer);
  document.body.append(dialog);
  $("source_language").required = true;
  $("target_language").required = true;
  $("executionModel").required = true;
  const panels = new Map();
  function section(id, label) {
    const button = element("button", label);
    button.id = "settingsTab-" + id;
    button.role = "tab";
    button.dataset.settingsTab = id;
    button.setAttribute("aria-controls", "settingsPanel-" + id);
    const panel = element("section");
    panel.id = "settingsPanel-" + id;
    panel.dataset.settingsPanel = id;
    panel.role = "tabpanel";
    panel.setAttribute("aria-labelledby", button.id);
    panel.append(element("h2", label));
    button.onclick = () => selectTab(id);
    tabs.append(button);
    content.append(panel);
    panels.set(id, { button, panel });
    return panel;
  }
  function selectTab(id, focus = false) {
    if (!panels.has(id)) id = "translation";
    hideHelp();
    for (const [key, { button, panel }] of panels) {
      panel.hidden = key !== id;
      button.setAttribute("aria-selected", String(key === id));
      button.tabIndex = key === id ? 0 : -1;
    }
    content.scrollTop = 0;
    if (focus) panels.get(id).button.focus();
  }
  tabs.onkeydown = (event) => {
    const entries = [...panels.keys()];
    const index = entries.indexOf(event.target.dataset.settingsTab);
    if (index < 0) return;
    let next;
    if (["ArrowDown", "ArrowRight"].includes(event.key))
      next = (index + 1) % entries.length;
    if (["ArrowUp", "ArrowLeft"].includes(event.key))
      next = (index + entries.length - 1) % entries.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = entries.length - 1;
    if (next !== undefined) {
      event.preventDefault();
      selectTab(entries[next], true);
    }
  };
  function group(panel, name, hint) {
    const section = element("section", undefined, "ux-settings-group");
    const heading = element("h3", name);
    if (hint) heading.append(help(hint, "О разделе «" + name + "»"));
    section.append(heading);
    panel.append(section);
    return section;
  }
  function field(parent, id, name, hint) {
    const control = $(id);
    const box = element("div", undefined, "ux-field");
    const label = element("label", name);
    label.htmlFor = id;
    const caption = element("div", undefined, "ux-field-caption");
    caption.append(label);
    if (hint) caption.append(help(hint, "О настройке «" + name + "»"));
    box.append(caption, control);
    parent.append(box);
    return box;
  }
  function toggle(parent, id, name, hint) {
    const control = $(id);
    const row = element("div", undefined, "ux-toggle");
    const label = element("label");
    label.append(control, document.createTextNode(name));
    row.append(label);
    if (hint) row.append(help(hint, "О настройке «" + name + "»"));
    parent.append(row);
    return row;
  }

  const translation = section("translation", "Перевод");
  const legacyQueueDefaults = element("div");
  legacyQueueDefaults.hidden = true;
  document.body.append(legacyQueueDefaults);
  const languageGrid = element("div");
  legacyQueueDefaults.append(languageGrid);
  field(languageGrid, "source_language", "Оригинал");
  field(languageGrid, "target_language", "Перевод");
  field(legacyQueueDefaults, "executionModel", "Исполнитель");
  toggle(
    translation,
    "auto_foreign",
    "Сохранять намеренные вставки на другом языке",
    "Если Studio уверенно определит, что реплика написана на другом языке, она оставит её в оригинале.",
  );

  const context = section("context", "Контекст и персонажи");
  const memory = group(
    context,
    "Справочник проекта",
    "Список имён, связей между персонажами и терминов из текста игры.",
  );
  toggle(
    memory,
    "use_project_knowledge",
    "Использовать справочник при переводе",
  );
  const autoMemory = toggle(
    memory,
    "analyze_before_translate",
    "Обновлять справочник перед новым переводом",
  );
  const knowledgeActionsRow = element(
    "div",
    undefined,
    "flex wrap settings-action-row",
  );
  knowledgeActionsRow.append(
    disabledReason(analyzeKnowledge, () =>
      !studioState.snapshot?.files.some((file) => file.total)
        ? "Сначала импортируйте текст"
        : "Дождитесь завершения текущих задач",
    ),
    viewKnowledge,
    help(
      "Собрать имена, связи между персонажами и термины из импортированного текста.",
      "О сборе справочника",
    ),
  );
  memory.append(knowledgeActionsRow);
  const neighbors = group(
    legacyQueueDefaults,
    "Соседние реплики",
    "Контекст берётся из того же файла и сцены. Ноль отключает соответствующую сторону. При нехватке токенов дальние реплики сокращаются или исключаются.",
  );
  const neighborGrid = element("div", undefined, "ux-two-fields");
  neighbors.append(neighborGrid);
  field(neighborGrid, "context_before", "До текущей фразы");
  field(neighborGrid, "context_after", "После текущей фразы");
  const voices = group(context, "Имена и речь");
  const voiceActions = element(
    "div",
    undefined,
    "flex wrap settings-action-row",
  );
  voiceActions.append(
    characters,
    help(
      "Задать написание имени, род и манеру речи конкретного персонажа.",
      "О правилах для персонажей",
    ),
  );
  voices.append(voiceActions);

  const files = section("files", "Файлы и источники");
  const fileSources = group(files, "Папка проекта");
  const projectPath = element("div", undefined, "ux-project-path");
  projectPath.id = "settingsProjectPath";
  const fileActions = element(
    "div",
    undefined,
    "flex wrap settings-action-row",
  );
  setText("importButton", "Добавить текстовые файлы…");
  $("importButton").classList.remove("full");
  fileActions.append($("importButton"), $("inspectProject"));
  fileSources.append(projectPath, fileActions);
  const historyGroup = group(files, "Показ очереди и истории");
  historySettings.querySelector("summary").hidden = true;
  historySettings.open = true;
  field(
    historyGroup,
    "historyMaxPhrases",
    "Количество фраз в истории",
    "0 — без ограничения.",
  );
  field(
    historyGroup,
    "historyMaxSeconds",
    "Период показа истории, секунд",
    "Показывает результаты за последние N секунд. 0 — за всё время.",
  );
  field(
    historyGroup,
    "processPageSize",
    "Строк на странице очереди и истории",
    "От 10 до 200.",
  );
  $("historyMaxPhrases").max = 1000000;
  $("historyMaxSeconds").max = 31536000;
  $("historyMaxSeconds").step = "any";
  $("processPageSize").max = 200;
  historySettings.hidden = true;
  const integrations = section("integrations", "Подключения");
  const api = group(integrations, "Облачный перевод");
  toggle(api, "cloud_enabled", "Разрешить облачные модели для проекта");
  cloudConnection.querySelector("summary").hidden = true;
  cloudConnection.open = true;
  const manageApi = element("button", "Настроить API-ключи");
  manageApi.id = "projectManageApiKeys";
  api.append(manageApi);
  legacyQueueDefaults.append(cloudConnection);
  const mcp = element("details", undefined, "ux-advanced");
  const mcpMenu = $("mcpButton").closest("details");
  mcp.append(element("summary", "Внешний ИИ-клиент · MCP"));
  const mcpActions = element("div", undefined, "flex wrap settings-action-row");
  setText(proposalsButton, "Предложения правок");
  setText("mcpButton", "Настроить MCP…");
  mcpActions.append($("mcpButton"), proposalsButton);
  mcp.append(mcpActions);
  legacyQueueDefaults.append(mcpMenu);
  mcpMenu.open = true;
  mcpMenu.querySelector("summary").hidden = true;
  integrations.append(mcp);
  const mcpHint = field(
    legacyQueueDefaults,
    "mcp_model_hint",
    "Предпочтительная модель клиента",
    "Имя передаётся подключённому MCP-клиенту как предпочтение; выбор модели зависит от клиента.",
  );

  const advanced = section("advanced", "Дополнительно");
  advanced.append(historyGroup);
  const modelBudget = group(
    legacyQueueDefaults,
    "Параметры модели",
    "Меняйте при нехватке контекста или обрезанных ответах. Большие значения повышают расход памяти локальной модели и объём платных облачных запросов.",
  );
  field(
    modelBudget,
    "context",
    "Контекст модели, токенов",
    "Общий бюджет запроса: инструкции, текущая фраза, соседние реплики и справочник. Длинные тексты разбиваются на части с сохранением готовых результатов.",
  );
  field(modelBudget, "max_output", "Максимальный ответ, токенов");
  field(
    modelBudget,
    "max_calls",
    "Запросов на один запуск",
    "Предельное число обращений к модели. Это не денежный лимит. Для облака денежное ограничение задаётся у провайдера.",
  );
  const modelCatalog = group(legacyQueueDefaults, "Каталог локальных моделей");
  field(modelCatalog, "model_sort", "Сортировка списка");
  modelCatalog.append(modelInfo);

  const storage = element("div");
  storage.hidden = true;
  storage.id = "legacyControls";
  document.body.append(storage);
  storage.append(settingsMenu, cloudAdvanced, knowledgeTools, translateButton);
  storage.append(saveHistorySettings);
  const sidebarImportHint = $("sidebarContent").querySelector(":scope > p.sub");
  if (sidebarImportHint) storage.append(sidebarImportHint);
  const detectedAction = importDetectedButton.parentElement;
  const importReason = disabledReason(importDetectedButton, () =>
    detectionBusy
      ? "Дождитесь завершения поиска"
      : "Выберите хотя бы один файл",
  );
  detectedAction.insertBefore(importReason, closeDetectionButton);
  settingsMenu.hidden = true;
  cloudAdvanced.hidden = true;
  knowledgeTools.hidden = true;
  // Scope and limits describe one run, so they remain beside its action.
  const scope = element("div", undefined, "ux-run-scope");
  field(scope, "scope", "Обработать");
  setText(newRunLimits.section.querySelector("summary"), "Параметры запуска");
  setText(limitMenu.querySelector("summary"), "Параметры продолжения");
  taskCard.insertBefore(scope, mainAction);
  taskCard.insertBefore(newRunLimits.section, taskMenu);
  const primaryRow = element("div", undefined, "ux-primary-action");
  mainAction.before(primaryRow);
  primaryRow.append(mainAction);
  actionHint.hidden = true;
  const executorSummary = element("div", undefined, "ux-executor-summary");
  primaryRow.after(executorSummary);
  executorSummary.append(activeSettings);
  reviewHint.hidden = true;
  reviewMenu
    .querySelector("summary")
    .append(help(() => reviewHint.textContent, "О проверке перевода"));
  setText(setupCard.querySelector("h2"), "Качество перевода");
  setupCard.replaceChildren(setupCard.querySelector("h2"), reviewMenu);

  // File navigation and review folders share one stable sidebar.
  const sidebar = document.querySelector("main > aside:first-child");
  const navigation = element("nav", undefined, "ux-workspace-nav");
  navigation.id = "workspaceNavigation";
  navigation.setAttribute("aria-label", "Разделы проекта");
  navigation.addEventListener(
    "click",
    (event) => {
      const button = event.target.closest("button");
      if (
        studioState.dirty.size &&
        button?.getAttribute("aria-current") !== "page"
      ) {
        event.preventDefault();
        event.stopImmediatePropagation();
        toast("Сохраните или отмените правки перед переходом в другой раздел");
      }
    },
    true,
  );
  const overview = element("button", "Проект");
  overview.id = "viewOverview";
  overview.onclick = guard(() => {
    if (studioState.dirty.size) throw Error("Сохраните или отмените правки");
    showView("home");
  });
  all.querySelector("small")?.remove();
  queue.querySelector("small")?.remove();
  setText(all, "Текст проекта");
  setText(queue, "Очередь и история");
  navigation.append(
    overview,
    all,
    queue,
    $("errorsButton"),
    reviewFolder,
    badFolder,
    preservedFolder,
  );
  const open = element(
    "button",
    "Настройки проекта",
    "ux-project-settings-button",
  );
  open.id = "projectSettingsButton";
  $("projectPicker").before($("newProject"));
  $("newProject").classList.add("full");
  $("projectPicker").after(open, manageProjects, navigation);
  const fileHeading = sidebar.querySelector("h2");
  fileHeading.textContent = "Файлы проекта";
  const fileMenu = element("details", undefined, "ux-file-tree");
  fileMenu.id = "workspaceFiles";
  fileMenu.open = true;
  fileMenu.append(element("summary", "Файлы проекта"), $("files"));
  fileHeading.replaceWith(fileMenu);
  choices.hidden = true;
  projectTools.hidden = true;
  $("projectBack").hidden = true;
  const descriptionHelp = help(
    () => $("description").textContent,
    "О текущем разделе",
  );
  const titleRow = element("div", undefined, "ux-page-title");
  $("fileTitle").before(titleRow);
  titleRow.append($("fileTitle"), descriptionHelp);
  $("description").hidden = true;
  detectionSummary.hidden = true;
  fileSources.append(detectionSummary);

  const application = element("section", undefined, "app-setting-row");
  application.id = "closePrefsMenu";
  const closeTitle = element("h3", "Действия при закрытии");
  closeTitle.append(
    help(
      "Действие при нажатии на крестик окна. В трее перевод продолжается.",
      "О действиях при закрытии",
    ),
  );
  const closeStatus = element("span", undefined, "sub app-setting-value");
  closeStatus.id = "closeChoiceStatus";
  const changeClose = element("button", "Изменить поведение");
  changeClose.id = "changeCloseChoice";
  const closeActions = element("div", undefined, "app-setting-actions");
  closeActions.append(closeStatus, changeClose);
  application.append(closeTitle, closeActions);
  updatesBody.prepend(application);

  const closeOptions = {
    ask: "Спрашивать каждый раз",
    tray: "Сворачивать в трей",
    exit: "Закрывать и останавливать перевод",
  };
  const closeForm = element("form");
  const closeSelect = element("select");
  closeSelect.id = "closeChoiceSelect";
  closeSelect.setAttribute("aria-label", "Действие при закрытии");
  for (const [value, label] of Object.entries(closeOptions)) {
    const option = element("option", label);
    option.value = value;
    closeSelect.append(option);
  }
  const saveClose = element("button", "Сохранить", "primary");
  saveClose.type = "submit";
  closeForm.append(closeSelect, saveClose);
  const closePopup = settingsPopup(
    "closeChoiceDialog",
    "Действия при закрытии",
    closeForm,
  );
  async function updateCloseChoice() {
    if (!window.pywebview?.api?.get_close_state) return;
    const { choice } = await window.pywebview.api.get_close_state();
    closeStatus.textContent = closeOptions[choice || "ask"];
    return choice || "ask";
  }
  changeClose.onclick = guard(async () => {
    closeSelect.value = (await updateCloseChoice()) || "ask";
    closePopup.showModal();
    closeSelect.focus();
  });
  closeForm.onsubmit = guard(async (event) => {
    event.preventDefault();
    saveClose.disabled = true;
    try {
      const { choice } = await window.pywebview.api.set_close_choice(
        closeSelect.value === "ask" ? null : closeSelect.value,
      );
      closeStatus.textContent = closeOptions[choice || "ask"];
      closePopup.close();
    } finally {
      saveClose.disabled = false;
    }
  });
  updatesDialog.addEventListener("settingsopened", guard(updateCloseChoice));
  const appAdvanced = element("details", undefined, "ux-advanced");
  appAdvanced.append(element("summary", "Автоматические обновления"));
  for (const input of Object.values(updateOptions))
    appAdvanced.append(input.closest("label"));
  updatesBody.append(appAdvanced);
  function updateAutomaticUpdates() {
    const enabled = updateOptions.auto_check.checked;
    for (const key of ["auto_download", "auto_install"]) {
      updateOptions[key].disabled = !enabled;
      updateOptions[key].closest("label").hidden = !enabled;
    }
  }
  for (const input of Object.values(updateOptions))
    input.addEventListener("change", updateAutomaticUpdates);

  let draft = null;
  let draftProject = 0;
  function controls() {
    return [
      ...new Set([
        ...dialog.querySelectorAll("input,select,textarea"),
        $("model"),
        $("provider"),
        mcpSessionInput,
      ]),
    ];
  }
  function refreshDependencies() {
    autoMemory.hidden = !useKnowledge.checked;
    analyzeKnowledge.disabled =
      !studioState.snapshot?.files.some((file) => file.total) ||
      studioState.snapshot.jobs.some(
        (job) =>
          ["queued", "running", "paused", "waiting", "held"].includes(
            job.state,
          ) || job.worker_active,
      );
    updateCloudAvailability();
    updateExecutionChoices();
    mcpHint.hidden = $("provider").value !== "mcp";
    modelCatalog.hidden = $("provider").value !== "local";
    updateLimitInput();
    newRunLimits.update();
    studioLifecycle.notify("projectCloudSelection");
  }
  useKnowledge.addEventListener("change", () => {
    if (!useKnowledge.checked) autoKnowledge.checked = false;
    refreshDependencies();
  });
  dialog.addEventListener("change", refreshDependencies);
  function openSettings(tab = "translation") {
    if (!studioState.project) return toast("Откройте проект");
    if (!dialog.open) {
      draftProject = studioState.project;
      draft = controls().map((control) => ({
        control,
        value: control.value,
        checked: control.checked,
      }));
      dialog.showModal();
    }
    selectTab(tab, true);
    refreshDependencies();
  }
  function rollback() {
    if (!draft) return;
    for (const entry of draft) {
      entry.control.value = entry.value;
      if (entry.checked !== undefined) entry.control.checked = entry.checked;
    }
    draft = null;
    $("apiKey").value = "";
    refreshDependencies();
    updateTaskActions();
  }
  dialog.addEventListener("close", () => {
    if (dialog.open) return;
    rollback();
    hideHelp();
  });
  function cancelSettings() {
    rollback();
    dialog.close();
  }
  dialog.addEventListener("cancel", rollback);
  close.onclick = cancel.onclick = cancelSettings;
  open.onclick = () => openSettings();
  dialog.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      cancelSettings();
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      $("saveSettings").click();
    }
  });
  studioLifecycle.register(
    "openProjectSettings",
    "workspaceSettings",
    ({ tab } = {}) => openSettings(tab),
  );
  studioLifecycle.register(
    "settingsDependencies",
    "workspaceDependencies",
    refreshDependencies,
  );
  studioLifecycle.register(
    "projectSettingsSaved",
    "workspaceSettingsSaved",
    () => {
      draft = null;
      dialog.close();
    },
  );
  studioLifecycle.register(
    "validateProjectSettings",
    "workspaceValidation",
    () => {
      for (const input of dialog.querySelectorAll("input,select")) {
        if (input.type === "number") input.required = true;
        if (!input.checkValidity()) {
          selectTab(
            input.closest("[data-settings-panel]").dataset.settingsPanel,
          );
          input.reportValidity();
          throw Error(
            "Проверьте значение: " +
              (dialog.querySelector(`label[for="${input.id}"]`)?.textContent ||
                input.id),
          );
        }
      }
    },
  );
  studioLifecycle.register("projectNavigation", "workspaceDraft", () => {
    if (dialog.open) cancelSettings();
  });

  // Only explanatory prose is transformed. Progress, warnings and source evidence remain visible.
  function convertHints(root) {
    for (const node of root.querySelectorAll(".hint")) {
      if (node.dataset.hintConverted) continue;
      node.dataset.hintConverted = "true";
      const button = help(() => node.textContent, "Пояснение");
      node.before(button);
      node.hidden = true;
    }
  }
  for (const selector of [
    "#exportDialog > p.sub",
    "#runLimits > p.sub",
    "#newRunLimits > p.sub",
    "#historySettings > p.sub",
    "#appAbout > p.sub:not([id])",
    "main > aside:first-child > p.sub",
    "#cloudAdvanced p.sub",
    "#cloud_enabled + p.sub",
  ]) {
    document
      .querySelectorAll(selector)
      .forEach((node) => node.classList.add("hint"));
  }
  mcpMenu
    .querySelectorAll("p.sub:not([id])")
    .forEach((node) => node.classList.add("hint"));
  cloudConnection
    .querySelectorAll("p.sub")
    .forEach((node) => node.classList.add("hint"));
  convertHints(document);
  new MutationObserver((records) => {
    for (const record of records)
      for (const node of record.addedNodes) {
        if (node.nodeType !== 1 || node === tooltip) continue;
        if (node.matches(".hint")) convertHints(node.parentElement);
        else if (node.querySelector(".hint")) convertHints(node);
      }
  }).observe(document.body, { childList: true, subtree: true });

  function refreshWorkspace() {
    const project = studioState.snapshot?.projects.find(
      (p) => p.id === studioState.project,
    );
    if (dialog.open && draftProject !== studioState.project) {
      draft = null;
      dialog.close();
    }
    setText(projectName, project?.name || "");
    setText(projectPath, project?.root || "");
    open.disabled = !project;
    const state = studioState.job?.state;
    $("live").hidden = !studioState.job?.current;
    const resuming = ["paused", "held"].includes(state);
    const working = ["running", "queued"].includes(state);
    newRunLimits.section.hidden = resuming || working;
    scope.hidden = resuming || working;
    setupCard.hidden = working || resuming;
    taskMenu.hidden = !working && !resuming && !studioState.snapshot?.errors;
    $("logDetails").hidden = !studioState.job;
    fileMenu.hidden = studioState.viewMode !== "text";
    $("projectBack").hidden = studioState.viewMode !== "plugins";
    const selected = studioState.viewMode;
    for (const [button, mode] of [
      [overview, "home"],
      [all, "text"],
      [queue, "queue"],
      [$("errorsButton"), "errors"],
      [badFolder, "bad"],
      [reviewFolder, "review"],
      [preservedFolder, "preserved"],
    ]) {
      const current =
        mode === selected ||
        (selected === "flags" && studioState.markKind === mode);
      button.classList.toggle("is-current", current);
      if (current) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
      const counter = button.textContent.match(/^(.*?)\s*·\s*(\d+)$/);
      if (counter && !button.querySelector(".ux-nav-count")) {
        const name = element("span", counter[1], "ux-nav-name");
        const separator = element("span", " · ", "ux-nav-separator");
        const count = element("span", counter[2], "ux-nav-count");
        button.replaceChildren(name, separator, count);
      }
    }
    const stage = {
      translate: "Перевод",
      analyze: "Анализ проекта",
      review: "Редактура",
      cloud: "Облачная проверка",
    }[studioState.job?.stage];
    setText(taskHeading.querySelector("strong"), stage || "Новый запуск");
    actionHint.hidden = true;
    reviewHint.hidden = true;
    updateAutomaticUpdates();
    refreshDependencies();
  }
  studioLifecycle.register("refresh", "workspaceRefresh", refreshWorkspace);
  studioLifecycle.register("navigate", "workspaceNavigation", () => {
    if (studioState.snapshot) refreshWorkspace();
  });
  selectTab("translation");
  if (studioState.snapshot) refreshWorkspace();
  function registerTab(id, label, node) {
    if (panels.has(id)) throw Error("Раздел уже существует");
    const panel = section(id, label);
    panel.append(node);
    panel.hidden = true;
    const button = panels.get(id).button;
    button.setAttribute("aria-selected", "false");
    button.tabIndex = -1;
    return () => {
      const selected = button.getAttribute("aria-selected") === "true";
      panel.remove();
      button.remove();
      panels.delete(id);
      if (selected) selectTab("translation");
    };
  }
  return {
    openSettings,
    selectTab,
    help,
    disabledReason,
    refreshDependencies,
    registerTab,
  };
})();

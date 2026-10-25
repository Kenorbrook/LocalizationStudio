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
  const languages = group(translation, "Языки проекта");
  const languageGrid = element("div", undefined, "ux-two-fields");
  languages.append(languageGrid);
  field(languageGrid, "source_language", "Оригинал");
  field(languageGrid, "target_language", "Перевод");
  const engine = group(
    translation,
    "Модель перевода",
    "Эта модель применяется к новым задачам и обычному продолжению перевода. Отдельные очереди повторов сохраняют собственную модель.",
  );
  field(engine, "executionModel", "Исполнитель");
  toggle(
    translation,
    "auto_foreign",
    "Сохранять намеренные вставки на другом языке",
    "Язык определяется локально. Уверенные случаи остаются в оригинале; сомнительные попадают в ручную проверку. Имена и отдельные слова автоматически не исключаются.",
  );

  const context = section("context", "Контекст и персонажи");
  const memory = group(
    context,
    "Справочник проекта",
    "Персонажи, отношения и термины собираются только из импортированных текстов с цитатами. Спорные выводы проверяет человек. Анализ использует выбранную модель и для облачного API расходует баланс.",
  );
  toggle(
    memory,
    "use_project_knowledge",
    "Использовать справочник при переводе",
    "Приложение подбирает сведения к текущей фразе. Устаревшие и отклонённые выводы не используются.",
  );
  const autoMemory = toggle(
    memory,
    "analyze_before_translate",
    "Обновлять справочник перед новым переводом",
    "Сначала анализируются все импортированные строки, затем запускается перевод. Готовые блоки анализа берутся из кэша. Продолжение уже созданной очереди сохраняет её порядок этапов.",
  );
  memory.append(
    analyzeKnowledge,
    help(() => analyzeKnowledge.dataset.modelDescription, "Модель анализа"),
  );
  const neighbors = group(
    context,
    "Соседние реплики",
    "Контекст берётся из того же файла и сцены. Ноль отключает соответствующую сторону. При нехватке токенов дальние реплики сокращаются или исключаются.",
  );
  const neighborGrid = element("div", undefined, "ux-two-fields");
  neighbors.append(neighborGrid);
  field(neighborGrid, "context_before", "До текущей фразы");
  field(neighborGrid, "context_after", "После текущей фразы");
  const voices = group(context, "Голоса персонажей");
  voices.append(
    characters,
    help(
      "Ручные правила имён, обращений и манеры речи относятся только к этому проекту. Неизвестный род можно оставить пустым.",
      "О голосах персонажей",
    ),
  );

  const files = section("files", "Файлы и источники");
  const fileSources = group(files, "Исходные файлы");
  const projectPath = element("div", undefined, "ux-project-path");
  projectPath.id = "settingsProjectPath";
  fileSources.append(projectPath, $("importButton"), $("inspectProject"));
  const historyGroup = group(
    files,
    "Показ очереди и истории",
    "Эти ограничения управляют только отображением, а не переводом или хранением готовых фраз.",
  );
  historySettings.querySelector("summary").hidden = true;
  historySettings.open = true;
  field(
    historyGroup,
    "historyMaxPhrases",
    "Последних фраз в истории",
    "Ограничивает видимый список. Ноль снимает ограничение; готовые переводы из базы не удаляются.",
  );
  field(
    historyGroup,
    "historyMaxSeconds",
    "Период показа истории, секунд",
    "Показывает результаты за последние N секунд. Ноль отключает ограничение по времени. Это не срок хранения перевода.",
  );
  field(
    historyGroup,
    "processPageSize",
    "Строк на странице",
    "От 10 до 200. Меняет размер страницы очереди и истории.",
  );
  $("historyMaxPhrases").max = 1000000;
  $("historyMaxSeconds").max = 31536000;
  $("historyMaxSeconds").step = "any";
  $("processPageSize").max = 200;
  historySettings.hidden = true;
  const manage = element("details", undefined, "ux-advanced");
  manage.append(
    element("summary", "Архив и управление проектами"),
    manageProjects,
  );
  setText(manageProjects, "Открыть управление проектами");
  files.append(manage);

  const integrations = section("integrations", "Подключения");
  const api = group(
    integrations,
    "Облачный API",
    "Необязательное платное подключение. Локальный перевод работает без ключа, оплаты и VPN. Ключ хранится в Windows Credential Manager.",
  );
  toggle(api, "cloud_enabled", "Разрешить облачные модели для проекта");
  cloudConnection.querySelector("summary").hidden = true;
  cloudConnection.open = true;
  api.append(cloudConnection);
  const mcp = element("details", undefined, "ux-advanced");
  const mcpMenu = $("mcpButton").closest("details");
  mcp.append(
    element("summary", "Внешний ИИ-клиент · MCP"),
    connectMcp,
    mcpMenu,
    proposalsButton,
  );
  mcpMenu.open = true;
  mcpMenu.querySelector("summary").hidden = true;
  integrations.append(mcp);
  const mcpHint = field(
    integrations,
    "mcp_model_hint",
    "Предпочтительная модель клиента",
    "Имя передаётся подключённому MCP-клиенту как предпочтение; выбор модели зависит от клиента.",
  );

  const advanced = section("advanced", "Дополнительно");
  const modelBudget = group(
    advanced,
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
  const modelCatalog = group(advanced, "Каталог локальных моделей");
  field(modelCatalog, "model_sort", "Сортировка списка");
  modelCatalog.append(modelInfo);

  const storage = element("div");
  storage.hidden = true;
  storage.id = "legacyControls";
  document.body.append(storage);
  storage.append(settingsMenu, cloudAdvanced, knowledgeTools, translateButton);
  storage.append(saveHistorySettings);
  const sidebarImportHint = $("sidebarContent").querySelector(":scope > p.sub");
  if (sidebarImportHint) {
    fileSources
      .querySelector("h3")
      .append(help(sidebarImportHint.textContent, "Об импорте файлов"));
    storage.append(sidebarImportHint);
  }
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
  const taskHelp = help(
    () => actionHint.textContent,
    "Что произойдёт при запуске",
  );
  const primaryRow = element("div", undefined, "ux-primary-action");
  mainAction.before(primaryRow);
  primaryRow.append(mainAction, taskHelp);
  actionHint.hidden = true;
  const modelDetails = help(
    () => activeSettings.dataset.fullDescription || activeSettings.textContent,
    "Параметры текущей задачи",
  );
  activeSettings.after(modelDetails);
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
  const overview = element("button", "Обзор проекта");
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
  $("projectPicker").after(open, navigation);
  const fileHeading = sidebar.querySelector("h2");
  fileHeading.textContent = "Файлы проекта";
  const fileMenu = element("details", undefined, "ux-file-tree");
  fileMenu.id = "workspaceFiles";
  fileMenu.open = true;
  fileMenu.append(element("summary", "Файлы проекта"), $("files"));
  fileHeading.replaceWith(fileMenu);
  choices.hidden = true;
  projectTools.hidden = true;
  const overviewGrid = element("div", undefined, "ux-overview-grid");
  const contextCard = element("section", undefined, "ux-overview-card");
  const contextTitle = element("h2", "Контекст проекта");
  contextTitle.append(
    help(
      "Справочник содержит персонажей, отношения, термины и неясные места с цитатами исходника. Подтверждённые сведения помогают переводу.",
      "О справочнике проекта",
    ),
  );
  contextCard.append(contextTitle, viewKnowledge);
  const contextSettings = element(
    "button",
    "Настроить контекст",
    "ux-text-button",
  );
  contextSettings.onclick = () => openSettings("context");
  contextCard.append(contextSettings);
  const projectCard = element("section", undefined, "ux-overview-card");
  projectCard.append(element("h2", "Параметры проекта"));
  const overviewSettings = element("div", undefined, "ux-overview-settings");
  overviewSettings.id = "projectConfigurationSummary";
  const editProject = element("button", "Изменить настройки", "ux-text-button");
  editProject.onclick = () => openSettings("translation");
  projectCard.append(overviewSettings, editProject);
  overviewGrid.append(contextCard, projectCard);
  home.append(overviewGrid);
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

  // Application preferences do not belong to a particular game.
  const application = element(
    "section",
    undefined,
    "ux-application-preferences",
  );
  application.append(element("h3", "Поведение окна"), closePrefsMenu);
  closePrefsMenu.open = true;
  closePrefsMenu.querySelector("summary").hidden = true;
  updatesBody.prepend(application);
  appSettings.addEventListener("click", () =>
    updateCloseChoice().catch((error) => toast(error.message)),
  );
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
    analyzeKnowledge.disabled = !useKnowledge.checked;
    updateCloudAvailability();
    updateExecutionChoices();
    mcpHint.hidden = $("provider").value !== "mcp";
    modelCatalog.hidden = $("provider").value !== "local";
    updateLimitInput();
    newRunLimits.update();
    updateKnowledgeModel();
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
    "#projectDialog > p.sub",
    "#importDialog > p.sub",
    "#exportDialog > p.sub",
    "#speakerDialog > p.sub",
    "#runLimits > p.sub",
    "#newRunLimits > p.sub",
    "#historySettings > p.sub",
    "#closePrefsMenu > p.sub:not([id])",
    "#projectManager .manager-group > p",
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
    newRunLimits.section.hidden =
      resuming || ["running", "queued"].includes(state);
    scope.hidden = resuming || ["running", "queued"].includes(state);
    fileMenu.hidden = studioState.viewMode !== "text";
    $("projectBack").hidden = true;
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
    const saved = JSON.parse(project?.settings || "{}");
    setText(
      overviewSettings,
      `${saved.source_language || "English"} → ${saved.target_language || "Russian"}\n${saved.provider === "cloud" ? saved.cloud_model || "Облачная модель" : saved.provider === "mcp" ? "Внешний клиент" : saved.model || $("model").value}`,
    );
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
  return { openSettings, selectTab, help, refreshDependencies, registerTab };
})();

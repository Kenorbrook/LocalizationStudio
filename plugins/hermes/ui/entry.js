export function create(api) {
  const {
    element,
    guard,
    setText,
    toast,
    $,
    workspaceUi,
    studioState,
    pluginsUi,
  } = api.ui;
  const studioLifecycle = api.lifecycle;
  const native = {
    get_hermes_state: (project) => api.call("state", project),
    get_hermes_models: () => api.call("models"),
    prepare_hermes_model: (model, context) =>
      api.call("prepare_model", model, context),
    install_hermes: () => api.call("install"),
    check_hermes: () => api.call("check"),
    cancel_hermes: () => api.call("cancel"),
    run_hermes: (project, mode) => api.call("run", project, mode),
    configure_plugin: (_id, values) => api.configure(values),
    set_plugin_enabled: (_id, enabled) => api.setEnabled(enabled),
  };
  let state = null,
    editing = false,
    sequence = 0,
    inventory = [],
    modelsOnline = false,
    modelsLoading = false,
    ownPreparation = false;
  function button(id, text, parent, action, className) {
    const node = element("button", text, className);
    node.id = id;
    node.onclick = guard(action);
    parent.append(node);
    return node;
  }
  function modal(id, titleText) {
    const dialog = element("dialog", undefined, "plugin-settings-dialog");
    dialog.id = id;
    const header = element("div", undefined, "plugin-settings-header");
    const title = element("h2", titleText);
    title.id = id + "Title";
    dialog.setAttribute("aria-labelledby", title.id);
    const close = button(id + "Close", "×", header, () => dialog.close());
    close.setAttribute("aria-label", "Закрыть «" + titleText + "»");
    header.prepend(title);
    const body = element("div", undefined, "plugin-settings-body");
    dialog.append(header, body);
    api.mount("app.dialogs", dialog);
    return { dialog, body, title };
  }
  const settings = modal("hermesSettingsDialog", "Настройки Hermes Agent");
  const settingsStatus = element("p", undefined, "sub");
  settingsStatus.id = "hermesStatus";
  settingsStatus.setAttribute("role", "status");
  const modelLabel = element("label", "Модель Ollama");
  modelLabel.htmlFor = "hermesModel";
  const model = element("select");
  model.id = "hermesModel";
  modelLabel.append(model);
  const modelAvailability = element("p", undefined, "sub");
  modelAvailability.id = "hermesModelsStatus";
  modelAvailability.setAttribute("role", "status");
  const modelActions = element("div", undefined, "plugin-task-actions");
  const reloadModels = button(
    "refreshHermesModels",
    "Обновить список моделей",
    modelActions,
    () => loadModels(),
  );
  const requirement = element(
    "p",
    "Нужна модель с инструментами и контекстом от 64 000 токенов.",
    "sub",
  );
  const advanced = element("details");
  advanced.id = "hermesAdvanced";
  advanced.append(element("summary", "Параметры модели"));
  const contextLabel = element("label", "Контекст агента, токенов");
  contextLabel.htmlFor = "hermesContext";
  contextLabel.append(
    workspaceUi.help(
      "Hermes требует минимум 64 000 токенов. Большой контекст требует больше памяти; настройка отделена от контекста перевода. Проверка подключения не загружает модель и не подтверждает, что она поместится в память.",
      "О контексте агента",
    ),
  );
  const context = element("input");
  context.id = "hermesContext";
  context.type = "number";
  context.min = 64000;
  context.max = 131072;
  contextLabel.append(context);
  advanced.append(contextLabel);
  const actions = element("div", undefined, "plugin-task-actions");
  const prepareModel = button(
    "prepareHermesModel",
    "Подготовить для Hermes",
    actions,
    async () => {
      const selected = model.value,
        tokens = +context.value;
      ownPreparation = true;
      try {
        await native.prepare_hermes_model(selected, tokens);
        await refresh();
        await pluginsUi.refresh();
      } catch (error) {
        ownPreparation = false;
        throw error;
      }
    },
    "primary",
  );
  const preparationStatus = element("p", undefined, "sub");
  preparationStatus.id = "hermesPreparationStatus";
  preparationStatus.setAttribute("role", "status");
  prepareModel.after(
    workspaceUi.help(
      "Создаёт отдельный профиль из выбранной установленной модели, задаёт указанный контекст в Ollama и сохраняет выбор для Hermes. Веса не скачиваются повторно. Исходная модель и настройки перевода не меняются. Это не проверка размещения модели в памяти.",
      "О подготовке модели",
    ),
  );
  const install = button(
    "installHermes",
    "Скачать и установить",
    actions,
    async () => {
      await native.install_hermes();
      await refresh();
      await pluginsUi.refresh();
    },
    "primary",
  );
  const check = button(
    "checkHermes",
    "Проверить подключение",
    actions,
    async () => {
      await native.check_hermes();
      await refresh();
      await pluginsUi.refresh();
    },
  );
  const stop = button("stopHermes", "Остановить", actions, async () => {
    await native.cancel_hermes();
    await refresh();
  });
  const logDetails = element("details");
  logDetails.append(element("summary", "Диагностика и журнал"));
  const log = element("pre");
  log.id = "hermesLog";
  logDetails.append(log);
  settings.body.append(
    settingsStatus,
    modelLabel,
    modelAvailability,
    modelActions,
    requirement,
    advanced,
    actions,
    preparationStatus,
    logDetails,
  );
  const footer = element("div", undefined, "plugin-settings-footer");
  const cancelSettings = button("cancelHermesSettings", "Закрыть", footer, () =>
    settings.dialog.close(),
  );
  const save = button("saveHermes", "Сохранить параметры", footer, async () => {
    await native.configure_plugin("hermes", {
      model: model.value.trim(),
      context: +context.value,
    });
    editing = false;
    await refresh(true);
    await pluginsUi.refresh();
    settings.dialog.close();
    toast("Параметры Hermes сохранены");
  });
  settings.dialog.append(footer);
  for (const input of [model, context])
    input.oninput = () => {
      editing = true;
      render();
    };
  settings.dialog.addEventListener("close", () => {
    editing = false;
  });
  async function openSettings() {
    editing = false;
    await refresh(true);
    if (!settings.dialog.open) settings.dialog.showModal();
    model.focus({ preventScroll: true });
    await loadModels();
  }
  function selectModel(value) {
    if (value && ![...model.options].some((option) => option.value === value)) {
      const option = element(
        "option",
        value + (modelsOnline ? " — не найдена" : " — сохранённая модель"),
      );
      option.value = value;
      model.append(option);
    }
    model.value = value;
  }
  async function loadModels() {
    if (modelsLoading) return;
    modelsLoading = true;
    reloadModels.disabled = true;
    setText(modelAvailability, "Читаем установленные модели Ollama…");
    render();
    try {
      const result = await native.get_hermes_models();
      modelsOnline = !!result.online;
      inventory = result.models || [];
      const selected = model.value || state?.preferences.model || "";
      const placeholder = element(
        "option",
        modelsOnline
          ? inventory.length
            ? "Выберите установленную модель"
            : "Нет установленных моделей"
          : "Ollama недоступна",
      );
      placeholder.value = "";
      model.replaceChildren(
        placeholder,
        ...inventory.map((item) => {
          const size = item.size
            ? ` · ${(item.size / 1000000000).toLocaleString("ru", { maximumFractionDigits: 1 })} ГБ`
            : "";
          const option = element(
            "option",
            item.name +
              (item.details?.parameter_size
                ? " · " + item.details.parameter_size
                : "") +
              size,
          );
          option.value = item.name;
          return option;
        }),
      );
      selectModel(selected);
      setText(
        modelAvailability,
        result.error ||
          (inventory.length
            ? "Установлено моделей: " + inventory.length
            : "Сначала установите модель в Ollama, затем обновите список"),
      );
    } catch (error) {
      modelsOnline = false;
      setText(modelAvailability, error.message);
    } finally {
      modelsLoading = false;
      reloadModels.disabled = false;
      render();
    }
  }
  const adapter = {
    configure: openSettings,
    install: openSettings,
    stop: () => native.cancel_hermes(),
  };

  const task = modal("hermesReportDialog", "Исследование с Hermes");
  const taskStatus = element("p", undefined, "sub");
  taskStatus.id = "hermesProjectStatus";
  taskStatus.setAttribute("role", "status");
  const report = element("pre", undefined, "plugin-task-report");
  report.id = "hermesReport";
  const taskActions = element("div", undefined, "plugin-task-actions");
  const stopTask = button(
    "stopHermesTask",
    "Остановить Hermes",
    taskActions,
    async () => {
      await native.cancel_hermes();
      await refresh();
    },
  );
  button("hermesReportSettings", "Настройки Hermes", taskActions, openSettings);
  const reportScope = element(
    "p",
    "Это отчёт-рекомендация. Hermes не добавил текст в проект, не изменил справочник и не установил перевод в игру.",
    "sub",
  );
  reportScope.id = "hermesReportScope";
  button(
    "hermesReportImport",
    "Перейти к поиску текста и импорту",
    taskActions,
    () => {
      task.dialog.close();
      workspaceUi.openSettings("files");
      $("inspectProject").click();
    },
  );
  task.body.append(taskStatus, reportScope, report, taskActions);
  async function runTask(mode) {
    const project = studioState.project;
    if (!project) throw Error("Откройте проект");
    await native.run_hermes(project, mode);
    task.title.textContent =
      mode === "research" ? "Отчёт о файлах игры" : "Разбор ошибок с Hermes";
    if (!task.dialog.open) task.dialog.showModal();
    await refresh();
  }
  function reason() {
    if (!state) return "Загружаем состояние Hermes…";
    if (!state.installed) return "Hermes не установлен";
    if (!state.preferences.enabled)
      return state.busy
        ? "Hermes отключён; текущая работа завершается"
        : "Hermes отключён";
    if (state.busy) return "Hermes занят: " + state.message;
    if (!state.preferences.model) return "Выберите модель в настройках Hermes";
    if (
      studioState.snapshot?.projects.some((x) => x.active) ||
      studioState.snapshot?.jobs.some((x) => x.worker_active)
    )
      return "Приостановите перевод и дождитесь завершения текущего запроса";
    return "";
  }
  async function connectHermes() {
    if (state?.installed && !state.preferences.enabled) {
      await native.set_plugin_enabled("hermes", true);
      await refresh();
      await pluginsUi.refresh();
    } else await openSettings();
  }
  function openReport() {
    task.title.textContent = "Последний отчёт Hermes · текущий проект";
    if (!task.dialog.open) task.dialog.showModal();
  }
  const sources = element("section", undefined, "plugin-inline-group");
  sources.id = "hermesSources";
  const sourceTitle = element("h3", "Дополнительный отчёт Hermes");
  sourceTitle.append(
    workspaceUi.help(
      "Hermes читает структуру и ограниченные фрагменты файлов текущего проекта. Отчёт содержит наблюдения и предположения; агент не меняет игру и не получает терминал.",
      "Об исследовании файлов",
    ),
  );
  const sourceActions = element("div", undefined, "plugin-context-actions");
  const research = button(
    "hermesResearch",
    "Получить отчёт о файлах",
    sourceActions,
    () => runTask("research"),
  );
  const sourceSetup = button(
    "hermesSourcesSettings",
    "Настроить Hermes",
    sourceActions,
    connectHermes,
  );
  const sourceReason = element("p", undefined, "sub");
  sourceReason.id = "hermesSourcesReason";
  const viewSourceReport = button(
    "hermesViewReport",
    "Открыть отчёт",
    sourceActions,
    openReport,
  );
  sources.append(sourceTitle, sourceActions, sourceReason);
  api.mount("project.settings.files", sources);
  const overviewSources = element("section", undefined, "ux-overview-card");
  overviewSources.append(element("h2", "Отчёты помощника"));
  button(
    "projectSources",
    "Исследование файлов с Hermes",
    overviewSources,
    () => workspaceUi.openSettings("files"),
  );
  api.mount("project.overview", overviewSources);

  const errorActions = element("section", undefined, "plugin-inline-group");
  errorActions.id = "hermesErrorActions";
  const errorActionsRow = element("div", undefined, "plugin-context-actions");
  const explain = button(
    "hermesExplainErrors",
    "Разобрать ошибки с Hermes",
    errorActionsRow,
    () => runTask("errors"),
  );
  const errorSetup = button(
    "hermesErrorsSettings",
    "Настроить Hermes",
    errorActionsRow,
    connectHermes,
  );
  const viewErrorReport = button(
    "hermesErrorsViewReport",
    "Открыть отчёт",
    errorActionsRow,
    openReport,
  );
  const errorReason = element("p", undefined, "sub");
  errorReason.id = "hermesErrorsReason";
  errorActions.append(errorActionsRow, errorReason);
  api.mount("project.errors.actions", errorActions);

  function render(fill = false) {
    if (!state) return;
    if (
      fill ||
      (!editing && ![model, context].includes(document.activeElement))
    ) {
      selectModel(state.preferences.model);
      context.value = state.preferences.context;
    }
    const ready = reason();
    research.disabled = explain.disabled = !!ready;
    setText(sourceReason, ready || "Модель: " + state.preferences.model);
    setText(errorReason, ready || "Модель: " + state.preferences.model);
    sourceReason.hidden = errorReason.hidden = false;
    viewSourceReport.hidden = !state.report;
    viewErrorReport.hidden = !state.report;
    for (const setup of [sourceSetup, errorSetup])
      setText(
        setup,
        state.installed && !state.preferences.enabled
          ? "Включить Hermes"
          : "Настроить Hermes",
      );
    for (const input of [model, context, save]) input.disabled = state.busy;
    setText(cancelSettings, state.busy || !editing ? "Закрыть" : "Отмена");
    install.hidden = state.installed;
    install.disabled = state.busy;
    prepareModel.disabled =
      state.busy ||
      modelsLoading ||
      !modelsOnline ||
      !inventory.some((item) => item.name === model.value) ||
      !context.checkValidity();
    setText(
      prepareModel,
      "Подготовить для Hermes · " +
        (+context.value || 0).toLocaleString("ru") +
        " токенов",
    );
    setText(
      preparationStatus,
      state.phase === "preparing" || state.phase === "error"
        ? state.message
        : state.model_profile
          ? `Профиль: ${state.model_profile.model} · ${state.model_profile.context.toLocaleString("ru")} токенов. Выбор сохранён.`
          : "",
    );
    preparationStatus.hidden = !preparationStatus.textContent;
    check.disabled =
      state.busy || !state.installed || !state.preferences.model || editing;
    check.title = editing ? "Сначала сохраните параметры модели" : "";
    stop.hidden = stopTask.hidden = !state.busy;
    setText(
      settingsStatus,
      state.busy || state.phase === "error"
        ? state.message
        : !state.installed
          ? "Не установлен · установка выполняется отдельно от приложения"
          : !state.preferences.enabled
            ? "Установлен · отключён в разделе «Плагины»"
            : !state.preferences.model
              ? "Установлен · выберите модель"
              : "Установлен · включён",
    );
    setText(log, state.log || "Журнал пуст");
    setText(taskStatus, state.message);
    setText(report, state.report || "Отчёт ещё не получен");
  }
  async function refresh(fill = false) {
    if (api.disposed) return;
    const current = ++sequence,
      project = studioState.project;
    const value = await native.get_hermes_state(project);
    if (current !== sequence || project !== studioState.project) return;
    state = value;
    if (ownPreparation && !value.busy) {
      ownPreparation = false;
      if (value.phase === "ready") {
        editing = false;
        render(true);
        await loadModels();
      }
    }
    render(fill);
  }
  studioLifecycle.register(
    "refresh",
    "hermesContextActions",
    async ({ previousProject }) => {
      if (previousProject !== studioState.project) {
        state = null;
        report.textContent = "";
        task.dialog.close();
      }
      if (
        previousProject !== studioState.project ||
        studioState.viewMode === "errors"
      )
        await refresh();
      else render();
    },
  );
  studioLifecycle.register("navigate", "hermesContextActions", () =>
    refresh().catch((error) => toast(error.message)),
  );
  studioLifecycle.register("pluginsChanged", "hermesAvailability", () =>
    refresh().catch((error) => toast(error.message)),
  );
  api.listen($("projectSettingsButton"), "click", () =>
    refresh().catch((error) => toast(error.message)),
  );
  api.interval(() => {
    if (
      settings.dialog.open ||
      task.dialog.open ||
      $("projectSettingsDialog").open ||
      studioState.viewMode === "errors"
    )
      refresh().catch((error) => setText(settingsStatus, error.message));
  }, 2500);
  return { ...adapter, refresh, openSettings };
}

"use strict";
const hermesUi = (() => {
  let state = null,
    editing = false,
    sequence = 0,
    recoveryPending = false;
  const recoveryViews = [];
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
    document.body.append(dialog);
    return { dialog, body, title };
  }
  const settings = modal("hermesSettingsDialog", "Настройки Hermes Agent");
  const settingsStatus = element("p", undefined, "sub");
  settingsStatus.id = "hermesStatus";
  settingsStatus.setAttribute("role", "status");
  const modelLabel = element("label", "Модель Ollama");
  modelLabel.htmlFor = "hermesModel";
  const model = element("input");
  model.id = "hermesModel";
  model.placeholder = "Выберите установленную модель";
  model.setAttribute("list", "hermesModels");
  const modelList = element("datalist");
  modelList.id = "hermesModels";
  modelLabel.append(model, modelList);
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
  const install = button(
    "installHermes",
    "Скачать и установить",
    actions,
    async () => {
      await window.pywebview.api.install_hermes();
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
      await window.pywebview.api.check_hermes();
      await refresh();
      await pluginsUi.refresh();
    },
  );
  const stop = button("stopHermes", "Остановить", actions, async () => {
    await window.pywebview.api.cancel_hermes();
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
    requirement,
    advanced,
    actions,
    logDetails,
  );
  const footer = element("div", undefined, "plugin-settings-footer");
  button("cancelHermesSettings", "Отмена", footer, () =>
    settings.dialog.close(),
  );
  const save = button(
    "saveHermes",
    "Сохранить параметры",
    footer,
    async () => {
      await window.pywebview.api.configure_plugin("hermes", {
        model: model.value.trim(),
        context: +context.value,
      });
      editing = false;
      await refresh(true);
      await pluginsUi.refresh();
      settings.dialog.close();
      toast("Параметры Hermes сохранены");
    },
    "primary",
  );
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
    request("models")
      .then((data) =>
        modelList.replaceChildren(
          ...(data.models || []).map((name) => {
            const option = element("option");
            option.value = name;
            return option;
          }),
        ),
      )
      .catch(() => {});
  }
  pluginsUi.register("hermes", {
    configure: openSettings,
    install: openSettings,
    stop: () => window.pywebview.api.cancel_hermes(),
  });

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
      await window.pywebview.api.cancel_hermes();
      await refresh();
    },
  );
  button("hermesReportSettings", "Настройки Hermes", taskActions, openSettings);
  task.body.append(taskStatus, report, taskActions);
  async function runTask(mode) {
    const project = studioState.project;
    if (!project) throw Error("Откройте проект");
    await window.pywebview.api.run_hermes(project, mode);
    task.title.textContent =
      mode === "research"
        ? "Исследование игровых файлов"
        : "Разбор ошибок с Hermes";
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
      await window.pywebview.api.set_plugin_enabled("hermes", true);
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
  const sourceTitle = element("h3", "Исследование с ИИ");
  sourceTitle.append(
    workspaceUi.help(
      "Hermes читает структуру и ограниченные фрагменты файлов текущего проекта. Отчёт содержит наблюдения и предположения; агент не меняет игру и не получает терминал.",
      "Об исследовании файлов",
    ),
  );
  const sourceActions = element("div", undefined, "plugin-context-actions");
  const research = button(
    "hermesResearch",
    "Исследовать с Hermes",
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
  $("settingsPanel-files").querySelector(".ux-settings-group").after(sources);
  const overviewSources = element("section", undefined, "ux-overview-card");
  overviewSources.append(element("h2", "Файлы и источники"));
  button(
    "projectSources",
    "Открыть файлы и исследование",
    overviewSources,
    () => workspaceUi.openSettings("files"),
  );
  $("projectHome").querySelector(".ux-overview-grid").append(overviewSources);

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
  errorsPane.prepend(errorActions);

  const recoveryDialog = modal(
    "recoverySettingsDialog",
    "Автоповтор ошибок проекта",
  );
  function recoveryPanel(parent, suffix) {
    const section = element("section", undefined, "plugin-inline-group");
    const heading = element("h3", "Восстановление ошибок");
    heading.append(
      workspaceUi.help(
        "Встроенная функция Studio, не требует Hermes. После штатного завершения локального перевода повторяет только ошибки памяти и лимитов: до двух дополнительных проходов и до 200 строк на задание. Паузы, аварийные остановки, ручные правки и сохранённые оригиналы защищены.",
        "Об автоповторе",
      ),
    );
    const label = element("label", undefined, "plugin-switch");
    const automatic = element("input");
    automatic.id = "automaticErrorRetry" + suffix;
    automatic.type = "checkbox";
    automatic.setAttribute("role", "switch");
    const track = element("span", undefined, "plugin-switch-track");
    track.setAttribute("aria-hidden", "true");
    label.append(
      automatic,
      track,
      document.createTextNode("Повторять ошибки памяти и лимитов"),
    );
    const hint = element(
      "p",
      "Для этого проекта · сохраняется сразу · работает без Hermes",
      "sub",
    );
    const plan = element("pre", undefined, "plugin-task-report");
    plan.id = "smartRetryPlan" + suffix;
    const start = button(
      "startSmartRetry" + suffix,
      "Запустить подобранные повторы",
      section,
      async () => {
        await window.pywebview.api.start_smart_retry(studioState.project);
        await refresh();
        await window.refresh();
      },
    );
    section.prepend(heading, label, hint, plan);
    parent.append(section);
    automatic.onchange = async () => {
      const project = studioState.project,
        enabled = automatic.checked;
      recoveryPending = true;
      render();
      try {
        await window.pywebview.api.set_automatic_retry(project, enabled);
        await refresh();
      } catch (error) {
        toast(error.message);
      } finally {
        recoveryPending = false;
        render();
      }
    };
    recoveryViews.push({ automatic, plan, start });
  }
  const recoveryDetails = element("details");
  recoveryDetails.id = "errorRecoveryDetails";
  recoveryDetails.append(
    element("summary", "Автоповтор ошибок · встроенная функция"),
  );
  recoveryPanel(recoveryDetails, "");
  errorActions.after(recoveryDetails);
  recoveryPanel(recoveryDialog.body, "Settings");
  const recoveryProject = element("section", undefined, "ux-settings-group");
  const recoveryProjectHeading = element("h3", "Восстановление ошибок");
  const recoverySummary = element("p", undefined, "sub");
  recoverySummary.id = "projectRecoverySummary";
  button(
    "configureProjectRecovery",
    "Настроить автоповторы",
    recoveryProject,
    async () => {
      await refresh();
      recoveryDialog.title.textContent =
        "Автоповтор ошибок · " +
        (studioState.snapshot?.projects.find(
          (x) => x.id === studioState.project,
        )?.name || "проект");
      recoveryDialog.dialog.showModal();
    },
  );
  recoveryProject.prepend(recoveryProjectHeading, recoverySummary);
  $("settingsPanel-translation").append(recoveryProject);

  function render(fill = false) {
    if (!state) return;
    if (
      fill ||
      (!editing && ![model, context].includes(document.activeElement))
    ) {
      model.value = state.preferences.model;
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
    install.hidden = state.installed;
    install.disabled = state.busy;
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
    const plan = state.retry_plan?.rows || [];
    for (const view of recoveryViews) {
      if (!recoveryPending) view.automatic.checked = !!state.automatic_retry;
      view.automatic.disabled = recoveryPending || !studioState.project;
      view.start.disabled = state.busy || !plan.length;
      setText(
        view.plan,
        plan.length
          ? plan
              .map(
                (row) =>
                  `#${row.record}: ${row.reason}\nКонтекст ${row.parameters.context}, ответ ${row.parameters.max_output}`,
              )
              .join("\n\n")
          : "Нет подходящих ошибок завершённого локального перевода",
      );
    }
    setText(
      recoverySummary,
      state.automatic_retry
        ? "Автоповтор включён для этого проекта"
        : "Автоповтор отключён для этого проекта",
    );
  }
  async function refresh(fill = false) {
    if (!window.pywebview?.api?.get_hermes_state) return;
    const current = ++sequence,
      project = studioState.project;
    const value = await window.pywebview.api.get_hermes_state(project);
    if (current !== sequence || project !== studioState.project) return;
    state = value;
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
        recoveryDialog.dialog.close();
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
  $("projectSettingsButton").addEventListener("click", () =>
    refresh().catch((error) => toast(error.message)),
  );
  setInterval(() => {
    if (
      settings.dialog.open ||
      task.dialog.open ||
      recoveryDialog.dialog.open ||
      $("projectSettingsDialog").open ||
      studioState.viewMode === "errors"
    )
      refresh().catch((error) => setText(settingsStatus, error.message));
  }, 2500);
  return { refresh, openSettings };
})();

"use strict";
const projectJourney = (() => {
  let owner = 0,
    state = null,
    sequence = 0,
    runStage = "translate",
    runProject = 0,
    loading = false,
    submitting = false,
    modelsSequence = 0;
  const pane = element("section");
  pane.id = "projectJourney";
  const heading = element("div", undefined, "journey-heading");
  heading.append(
    element("h2", "Перевод игры: от папки до результата"),
    element("span", "Локально · без облачного API", "journey-local"),
  );
  const runtime = element(
    "p",
    "Ollama и локальные модели: ещё не проверены",
    "sub",
  );
  runtime.id = "journeyRuntime";
  const boundary = element("p", undefined, "journey-boundary");
  boundary.id = "journeyBoundary";
  const list = element("ol", undefined, "journey-steps");
  const steps = new Map();
  function action(parent, id, name, callback, primary = false) {
    const button = element("button", name, primary ? "primary" : undefined);
    button.id = id;
    button.onclick = guard(callback);
    parent.append(button);
    return button;
  }
  for (const [key, title] of [
    ["files", "Найти и добавить текст"],
    ["translate", "Перевести локально"],
    ["review", "Проверить перевод"],
    ["install", "Встроить в игру"],
  ]) {
    const node = element("li", undefined, "journey-step");
    node.id = "journeyStep-" + key;
    const titleNode = element("h3");
    titleNode.append(
      element("span", steps.size + 1, "journey-number"),
      document.createTextNode(title),
    );
    const status = element("div", undefined, "journey-state");
    const description = element("p");
    const actions = element("div", undefined, "journey-actions");
    node.append(titleNode, status, description, actions);
    list.append(node);
    steps.set(key, { node, status, description, actions });
  }
  const files = action(
    steps.get("files").actions,
    "journeyFiles",
    "Открыть поиск и импорт",
    () => inspectButton.onclick(),
    true,
  );
  const translate = action(
    steps.get("translate").actions,
    "journeyTranslate",
    "Подготовить локальный перевод",
    () => openRun("translate"),
    true,
  );
  const glossary = action(
    steps.get("translate").actions,
    "journeyGlossary",
    "Персонажи и термины",
    () => viewKnowledge.onclick(),
  );
  const review = action(
    steps.get("review").actions,
    "journeyReview",
    "Подготовить локальную редактуру",
    () => openRun("review"),
  );
  action(
    steps.get("review").actions,
    "journeyErrors",
    "Ошибки и повторы",
    async () => {
      showView("errors");
      await loadErrors();
    },
  );
  const exportFile = action(
    steps.get("install").actions,
    "journeyExport",
    "Экспорт отдельного файла…",
    openExport,
  );
  const modules = element("details", undefined, "journey-modules");
  modules.id = "journeyModules";
  modules.append(element("summary", "Что нужно для локального перевода"));
  for (const [name, text] of [
    [
      "Поиск и импорт",
      "Встроены в Studio. Поддержка отдельных файлов не означает поддержку всех архивов игры.",
    ],
    [
      "Ollama и локальная модель",
      "Исполняют перевод, анализ персонажей и редактуру. Доступность моделей проверяется в окне подготовки запуска.",
    ],
    [
      "Справочник и повторы",
      "Встроены в Studio. Для них не требуется плагин агента.",
    ],
    [
      "Установка перевода",
      "Для поддерживаемых движков используется отдельный модуль подготовки и сборки. Его состояние показано на шаге 4. Облачный API для этого не нужен.",
    ],
    [
      "Плагины и облако",
      "Необязательные дополнения. Для локального перевода и редактуры API-ключ и облачная модель не нужны.",
    ],
  ]) {
    const row = element("div", undefined, "journey-module");
    row.append(element("strong", name), element("span", text));
    modules.append(row);
  }
  action(modules, "journeyPlugins", "Посмотреть необязательные плагины", () =>
    pluginsUi.open(),
  );
  pane.append(heading, runtime, boundary, list, modules);
  home.prepend(pane);
  function modal(id, title) {
    const dialog = element("dialog", undefined, "plugin-settings-dialog");
    dialog.id = id;
    const body = element("div", undefined, "plugin-settings-body");
    const name = element("h2", title);
    name.id = id + "Title";
    dialog.setAttribute("aria-labelledby", name.id);
    body.append(name);
    dialog.append(body);
    document.body.append(dialog);
    dialog.addEventListener("close", () => {
      if (dialog === run.dialog) runProject = 0;
    });
    return { dialog, body, title: name };
  }
  const run = modal("journeyRunDialog", "Локальный перевод");
  const runText = element("p", undefined, "journey-run-summary");
  const modelLabel = element("label", "Локальная модель Ollama");
  modelLabel.htmlFor = "journeyRunModel";
  const model = element("select");
  model.id = "journeyRunModel";
  const modelState = element("p", undefined, "sub");
  modelState.id = "journeyRunModelState";
  modelState.setAttribute("role", "status");
  const reload = action(
    run.body,
    "journeyReloadModels",
    "Обновить список моделей",
    loadModels,
  );
  const blocked = element("p", undefined, "journey-boundary");
  blocked.id = "journeyRunBlocked";
  const queue = action(
    run.body,
    "journeyOpenQueue",
    "Открыть существующую очередь",
    () => {
      run.dialog.close();
      showView("queue");
      loadRows();
    },
  );
  const startButton = action(
    run.body,
    "journeyStartLocal",
    "Запустить локальный перевод",
    startLocal,
    true,
  );
  action(run.body, "journeyRunParameters", "Изменить параметры проекта", () => {
    run.dialog.close();
    workspaceUi.openSettings("translation");
  });
  action(run.body, "journeyRunClose", "Закрыть", () => run.dialog.close());
  run.body.prepend(runText, modelLabel, model, modelState, blocked);
  let modelsOnline = false;
  function renderRun() {
    if (!state || !run.dialog.open || runProject !== studioState.project)
      return;
    const busy =
      state.jobs.length > 0 ||
      !!studioState.snapshot?.projects.some((p) => p.active);
    blocked.hidden = !busy;
    queue.hidden = !busy;
    setText(
      blocked,
      "Есть незавершённая очередь. Сначала продолжите или завершите её в разделе «Очередь и история». Этот экран не меняет её модель и не запускает её автоматически.",
    );
    const eligible = runStage === "translate" ? state.empty : state.translated;
    startButton.disabled =
      loading ||
      submitting ||
      !modelsOnline ||
      !model.value ||
      busy ||
      !eligible;
    model.disabled = loading;
    reload.disabled = loading;
  }
  async function loadModels() {
    const project = runProject,
      own = ++modelsSequence;
    if (!project) return;
    loading = true;
    modelsOnline = false;
    renderRun();
    setText(modelState, "Проверяем Ollama и установленные модели…");
    try {
      const result = await request("models");
      studioLifecycle.notify("localModels", result);
      if (
        own !== modelsSequence ||
        runProject !== project ||
        studioState.project !== project
      )
        return;
      const chosen = model.value || settings().model;
      const names = result.models || [];
      modelsOnline = !!result.online;
      const placeholder = element(
        "option",
        names.length
          ? "Выберите локальную модель"
          : "Нет доступных локальных моделей",
      );
      placeholder.value = "";
      model.replaceChildren(
        placeholder,
        ...names.map((name) => {
          const option = element("option", name);
          option.value = name;
          return option;
        }),
      );
      model.value = names.includes(chosen)
        ? chosen
        : names.length === 1
          ? names[0]
          : "";
      setText(
        modelState,
        !result.online
          ? "Ollama недоступна. Запустите локальный сервис и обновите список."
          : !names.length
            ? "Ollama доступна, но моделей нет. Сначала установите модель в Ollama."
            : "Используются установленные модели. Облачный API и плагин агента в этом запуске не участвуют.",
      );
    } catch (error) {
      setText(modelState, error.message);
    } finally {
      if (own === modelsSequence) {
        loading = false;
        renderRun();
      }
    }
  }
  model.onchange = renderRun;
  async function openRun(stage) {
    await refresh();
    if (!state) throw Error("Откройте проект");
    if (studioState.dirty.size)
      throw Error("Сохраните или отмените правки перед запуском");
    runStage = stage;
    runProject = studioState.project;
    setText(
      run.title,
      stage === "translate"
        ? "Локальный перевод"
        : "Локальная редактура перевода",
    );
    setText(
      startButton,
      stage === "translate"
        ? "Запустить локальный перевод"
        : "Запустить локальную редактуру",
    );
    setText(
      runText,
      stage === "translate"
        ? "Весь импортированный проект · готовые строки сохраняются. При включённом справочнике Studio сначала соберёт персонажей, отношения и термины из текста. Параметры и лимиты берутся из настроек проекта. Выбранная здесь модель применяется только к этому запуску."
        : "Редактура переведённых строк импортированного проекта. Модель ищет ошибки смысла и языка; запуск игры, шрифты и расположение текста этим не проверяются. Ручные правки и сохранённые оригиналы защищены. Выбранная здесь модель применяется только к этому запуску.",
    );
    run.dialog.showModal();
    renderRun();
    await loadModels();
  }
  async function startLocal() {
    if (startButton.disabled || runProject !== studioState.project) return;
    const project = runProject,
      values = settings();
    values.model = model.value;
    values.provider = "local";
    values.execution_kind = "local";
    delete values.mcp_session;
    delete values.mcp_model_hint;
    submitting = true;
    startButton.disabled = true;
    try {
      await request("job", {
        project,
        stage: runStage,
        provider: "local",
        settings: values,
        file: null,
        retry: false,
        persist_settings: false,
      });
      run.dialog.close();
      showView("queue");
      await window.refresh();
      await loadRows();
      toast("Локальная задача создана · прогресс и журнал в приложении");
    } finally {
      submitting = false;
      renderRun();
    }
  }
  const exporting = modal(
    "journeyExportDialog",
    "Экспорт файла — без установки в игру",
  );
  const exportSelect = element("select");
  exportSelect.id = "journeyExportFile";
  const exportLabel = element("label", "Файл проекта");
  exportLabel.htmlFor = exportSelect.id;
  const exportInfo = element("p", undefined, "journey-run-summary");
  exportInfo.id = "journeyExportInfo";
  exporting.body.append(exportLabel, exportSelect, exportInfo);
  action(
    exporting.body,
    "journeyOpenExport",
    "Выбрать путь сохранения…",
    () => {
      if (studioState.dirty.size)
        throw Error("Сохраните правки перед экспортом");
      const file = state?.files.find((f) => f.id === +exportSelect.value);
      if (!file) throw Error("Выберите файл");
      studioState.file = file.id;
      exporting.dialog.close();
      $("exportButton").onclick();
    },
    true,
  );
  action(exporting.body, "journeyExportClose", "Закрыть", () =>
    exporting.dialog.close(),
  );
  function renderExport() {
    const file = state?.files.find((f) => f.id === +exportSelect.value);
    setText(
      exportInfo,
      file?.export_kind === "rpy"
        ? "Результат: файл существующего слоя Ren’Py (.rpy). Его сохранение ещё не устанавливает перевод, не добавляет язык и не проверяет игру."
        : "Результат: JSON-корпус с оригиналом и переводом. Это данные для дальнейшей интеграции, а не готовый патч игры.",
    );
  }
  exportSelect.onchange = renderExport;
  function openExport() {
    if (!state?.files.length) throw Error("Сначала добавьте текст проекта");
    if (studioState.dirty.size) throw Error("Сохраните правки перед экспортом");
    exportSelect.replaceChildren(
      ...state.files.map((file) => {
        const option = element(
          "option",
          file.path.split(/[\\/]/).pop() + " → ." + file.export_kind,
        );
        option.value = file.id;
        return option;
      }),
    );
    if (state.files.some((f) => f.id === studioState.file))
      exportSelect.value = studioState.file;
    renderExport();
    exporting.dialog.showModal();
  }
  function step(key, status, description, kind) {
    const view = steps.get(key);
    setText(view.status, status);
    setText(view.description, description);
    view.node.dataset.state = kind;
  }
  function render() {
    document.body.dataset.projectGuide =
      studioState.viewMode !== "home"
        ? "off"
        : studioState.job
          ? "task"
          : "idle";
    const exists = !!studioState.project;
    list.hidden = !exists || !state;
    modules.hidden = !exists;
    pane.setAttribute("aria-busy", String(exists && !state));
    setText(
      boundary,
      !exists
        ? "Откройте папку распакованной игры. Studio покажет доступные шаги и ограничения именно этого проекта."
        : state?.engine_module?.supported
          ? "Модуль движка доступен. Подготовьте отдельную копию, пройдите локальный цикл и проверьте результат в игре."
          : "Для полного цикла нужен модуль подготовки и сборки этого движка. Локальный перевод и редактура доступны для импортированных строк.",
    );
    if (!state) return;
    const engine = state.engine?.name || "Движок ещё не определён";
    step(
      "files",
      state.total
        ? "Импортировано: " +
            countLabel(state.total, "строка", "строки", "строк")
        : "Текст ещё не добавлен",
      engine +
        (!state.engine_module?.prepared && state.pending_files.length
          ? ` · найдено ещё файлов для импорта: ${state.pending_files.length}`
          : "") +
        (!state.engine_module?.prepared && state.archives
          ? `\nАрхивов: ${state.archives}. Извлечение содержимого не реализовано.`
          : "") +
        (state.scan_partial ? "\nСканирование неполное." : "") +
        (state.engine_module?.prepared
          ? "\nИмпортирован штатный слой Ren’Py. Динамически создаваемый текст и надписи в изображениях требуют проверки в игре."
          : "\nПолнота извлечения текста ещё не подтверждена."),
      (!state.engine_module?.prepared && state.pending_files.length) ||
        state.scan_partial
        ? "attention"
        : "available",
    );
    const work = state.jobs.find((j) => j.stage === "translate");
    step(
      "translate",
      work
        ? `Очередь: ${jobLabels[work.state] || work.state}`
        : state.empty
          ? `Осталось без перевода: ${state.empty}`
          : state.total
            ? "Импортированные строки обработаны"
            : "Ожидает импорта",
      "Ollama переводит импортированный текст. Справочник персонажей и терминов помогает с контекстом.",
      work ? "active" : "available",
    );
    step(
      "review",
      `После редактуры/проверки: ${state.edited} · ручных пометок: ${state.manual_review}`,
      "Редактура проверяет текст. Запуск игры, шрифты и меню языка проверяются отдельно.",
      state.errors || state.manual_review ? "attention" : "available",
    );
    step(
      "install",
      state.engine_module?.built
        ? "Копия собрана"
        : state.installation.available
          ? "Доступен модуль движка"
          : "Нет включённого модуля сборки",
      state.installation.reason,
      state.installation.available ? "available" : "blocked",
    );
    translate.disabled = !state.empty;
    review.disabled = !state.translated;
    exportFile.disabled = !state.files.length;
    files.disabled = !exists || !!state.engine_module?.prepared;
    setText(
      files,
      state.engine_module?.prepared
        ? "Слой подготовлен движком"
        : "Открыть поиск и импорт",
    );
    glossary.disabled = !exists;
    renderRun();
  }
  async function refresh() {
    if (owner !== studioState.project) {
      owner = studioState.project;
      state = null;
      modelsSequence++;
      loading = false;
      run.dialog.close();
      exporting.dialog.close();
      render();
    }
    const project = studioState.project,
      own = ++sequence;
    if (!project) {
      state = null;
      render();
      return;
    }
    const value = await request("project-workflow?project=" + project);
    if (project !== studioState.project || own !== sequence) return;
    state = value;
    render();
    studioLifecycle.notify("projectWorkflow", value);
  }
  studioLifecycle.register(
    "refresh",
    "projectJourney",
    async ({ previousProject }) => {
      if (
        owner !== studioState.project ||
        previousProject !== studioState.project
      ) {
        state = null;
        run.dialog.close();
        exporting.dialog.close();
        render();
      }
      if (studioState.viewMode === "home" || run.dialog.open) await refresh();
    },
  );
  studioLifecycle.register("localModels", "journeyRuntime", (result) => {
    setText(
      runtime,
      !result.online
        ? "Ollama недоступна · импорт и работа с текстом доступны без модели"
        : result.models?.length
          ? `Ollama доступна · установлено моделей: ${result.models.length} · облачный ключ не нужен`
          : "Ollama доступна · сначала установите локальную модель",
    );
  });
  studioLifecycle.register("navigate", "projectJourney", () => {
    render();
    if (studioState.viewMode === "home")
      refresh().catch((e) => toast(e.message));
  });
  render();
  return { refresh, openRun };
})();

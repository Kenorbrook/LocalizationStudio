export function create(api) {
  const {
    element,
    guard,
    setText,
    $,
    studioState,
    settings,
    openProject,
    showView,
    refresh,
  } = api.ui;
  let state = null,
    project = 0,
    engine = false,
    sequence = 0,
    expectedPreparation = 0,
    editing = false,
    moving = false,
    compatible = true,
    inspectionError = "";
  const sources = element("div", undefined, "plugin-context-actions");
  sources.hidden = true;
  const installs = element("div", undefined, "plugin-context-actions");
  installs.hidden = true;
  api.mount("project.workflow.files", sources);
  api.mount("project.workflow.install", installs);
  function button(parent, id, text, callback, primary = false) {
    const node = element("button", text, primary ? "primary" : undefined);
    node.id = id;
    node.onclick = guard(callback);
    parent.append(node);
    return node;
  }
  const prepareEntry = button(
    sources,
    "renpyPrepareEntry",
    "Подготовить копию Ren’Py",
    openSettings,
    true,
  );
  const workspaceEntry = button(
    sources,
    "renpyWorkspace",
    "Открыть рабочую копию",
    openWorkspace,
  );
  const buildEntry = button(
    installs,
    "renpyBuildEntry",
    "Сборка копии и язык",
    openSettings,
    true,
  );
  const dialog = element("dialog", undefined, "plugin-settings-dialog");
  dialog.id = "renpyGameDialog";
  dialog.setAttribute("aria-labelledby", "renpyGameTitle");
  const body = element("div", undefined, "plugin-settings-body"),
    title = element("h2", "Локализация игры Ren’Py");
  title.id = "renpyGameTitle";
  const status = element("p", undefined, "journey-run-summary");
  status.id = "renpyGameStatus";
  status.setAttribute("role", "status");
  const pathLabel = element("label", "Новая папка копии игры");
  pathLabel.htmlFor = "renpyCopyPath";
  const path = element("input");
  path.id = "renpyCopyPath";
  path.style.width = "100%";
  const size = element("p", undefined, "sub");
  size.id = "renpyCopySize";
  const progress = element("progress");
  progress.id = "renpyCopyProgress";
  progress.style.width = "100%";
  progress.max = 1;
  const prepare = button(
    body,
    "renpyPrepare",
    "Создать копию и добавить текст",
    async () => {
      if (!project) throw Error("Откройте проект");
      expectedPreparation = project;
      state = await api.call("prepare", project, path.value);
      render();
      await poll();
    },
    true,
  );
  const modeLabel = element("label", "Как переключать язык");
  modeLabel.htmlFor = "renpyLanguageMode";
  const mode = element("select");
  mode.id = "renpyLanguageMode";
  mode.style.width = "100%";
  for (const [value, text] of [
    ["menu", "Кнопки в меню игры"],
    ["launcher", "Выбор перед запуском через Studio"],
  ]) {
    const option = element("option", text);
    option.value = value;
    mode.append(option);
  }
  const modelLabel = element("label", "Локальная модель Ollama");
  modelLabel.htmlFor = "renpyCycleModel";
  const model = element("select");
  model.id = "renpyCycleModel";
  model.style.width = "100%";
  const modelStatus = element("p", undefined, "sub");
  modelStatus.id = "renpyModelStatus";
  const cycle = button(
    body,
    "renpyStartCycle",
    "Перевести → проверить → собрать",
    async () => {
      if (!model.value) throw Error("Выберите локальную модель");
      await openWorkspace();
      const values = {
        ...settings(),
        model: model.value,
        provider: "local",
        execution_kind: "local",
      };
      state = await api.call("pipeline", api.project(), values, mode.value);
      render();
      dialog.close();
      showView("queue");
      await refresh();
    },
    true,
  );
  const build = button(
    body,
    "renpyBuild",
    "Собрать из готовых переводов",
    async () => {
      state = await api.call("build", project, mode.value);
      render();
    },
  );
  const rollback = button(
    body,
    "renpyRollback",
    "Отменить последнюю сборку",
    async () => {
      state = await api.call("rollback", project);
      render();
      await refresh();
    },
  );
  const playRu = button(
    body,
    "renpyPlayRussian",
    "Запустить копию: русский",
    () => api.call("launch", project, "russian"),
    true,
  );
  const playOriginal = button(
    body,
    "renpyPlayOriginal",
    "Запустить копию: оригинал",
    () => api.call("launch", project, "original"),
  );
  const stop = button(
    body,
    "renpyStop",
    "Остановить операцию модуля",
    async () => {
      await api.call("cancel");
      await poll();
    },
  );
  const note = api.ui.workspaceUi.help(
    "Оригинальная папка игры не изменяется. Подготовка создаёт отдельный проект копии. Готовые переводы переносятся только при однозначном совпадении сцены, говорящего и оригинала. Ошибки и спорные строки останавливают сборку.",
    "Как работает локализация Ren’Py",
  );
  const outcome = element("p", undefined, "sub");
  outcome.id = "renpyGameOutcome";
  body.prepend(title, status, pathLabel, path, size, progress);
  prepare.after(modeLabel, mode, modelLabel, model, modelStatus);
  title.append(note);
  const journal = element("pre");
  journal.id = "renpyEngineLog";
  journal.hidden = true;
  journal.style.whiteSpace = "pre-wrap";
  button(body, "renpyReadLog", "Журнал подготовки и сборки", async () => {
    setText(journal, await api.call("log", project));
    journal.hidden = false;
  });
  body.append(outcome, journal);
  button(body, "renpyClose", "Закрыть", () => dialog.close());
  dialog.append(body);
  api.mount("app.dialogs", dialog);
  path.oninput = () => {
    editing = true;
  };
  let modelsOnline = false;
  async function models() {
    const target = api.project();
    const value = await api.call("models");
    if (api.disposed || target !== api.project()) return;
    modelsOnline = !!value.online;
    const previous = model.value || settings().model;
    const placeholder = element("option", "Выберите установленную модель");
    placeholder.value = "";
    model.replaceChildren(
      placeholder,
      ...(value.models || []).map((name) => {
        const option = element("option", name);
        option.value = name;
        return option;
      }),
    );
    model.value = (value.models || []).includes(previous)
      ? previous
      : value.models?.length === 1
        ? value.models[0]
        : "";
    setText(
      modelStatus,
      value.online
        ? "Используется только локальный Ollama. Облачный ключ и Hermes не нужны."
        : "Ollama недоступна. Подготовка и сборка копии доступны без модели.",
    );
    render();
  }
  button(body, "renpyReloadModels", "Обновить модели", models);
  model.onchange = render;
  function render() {
    sources.hidden = installs.hidden = !engine;
    const ready = !!state?.prepared,
      busy = !!state?.busy;
    workspaceEntry.hidden = !ready;
    prepareEntry.hidden = ready;
    setText(
      buildEntry,
      state?.built ? "Запуск переведённой копии" : "Сборка копии и язык",
    );
    setText(
      status,
      inspectionError || state?.message || "Проверяем папку игры…",
    );
    path.disabled = busy || ready;
    prepare.disabled = busy || ready || !compatible || !!inspectionError;
    prepare.hidden = ready;
    pathLabel.hidden = path.hidden = ready;
    cycle.disabled =
      busy || !ready || !compatible || !modelsOnline || !model.value;
    build.disabled = busy || !ready || !compatible;
    mode.disabled = busy;
    model.disabled = busy;
    playRu.hidden = playOriginal.hidden = !state?.built;
    playRu.disabled = playOriginal.disabled = busy;
    rollback.hidden = !state?.rollback_available;
    rollback.disabled = busy;
    stop.hidden = !busy;
    progress.hidden = !busy || !state?.progress;
    progress.value = state?.progress?.total
      ? state.progress.done / state.progress.total
      : 0;
    setText(
      outcome,
      state?.copy
        ? "Копия: " +
            state.copy +
            (state.built
              ? "\nДвижок загрузил переводы и шрифт. Проверьте внешний вид и сцены в запущенной копии."
              : "")
        : "",
    );
  }
  async function poll() {
    if (!api.project()) return;
    const current = ++sequence,
      target = api.project();
    const value = await api.call("state", target);
    if (api.disposed || current !== sequence || target !== api.project())
      return;
    project = target;
    state = value;
    render();
    if (
      expectedPreparation === target &&
      value.prepared &&
      !value.busy &&
      value.workspace_project
    ) {
      expectedPreparation = 0;
      moving = true;
      try {
        await openProject(value.workspace_project);
        project = value.workspace_project;
      } finally {
        moving = false;
      }
      await refresh();
    }
  }
  async function openWorkspace() {
    if (!state?.workspace_project) throw Error("Сначала подготовьте копию");
    const target = state.workspace_project;
    if (api.project() !== target) {
      moving = true;
      try {
        await openProject(target);
      } finally {
        moving = false;
      }
    }
    project = target;
    return target;
  }
  async function openSettings() {
    if (!api.project()) throw Error("Откройте проект игры Ren’Py");
    project = api.project();
    editing = false;
    compatible = true;
    inspectionError = "";
    journal.hidden = true;
    setText(journal, "");
    if (!dialog.open) dialog.showModal();
    await poll();
    if (!state?.prepared) {
      try {
        const found = await api.call("inspect", project);
        if (project !== api.project() || api.disposed) return;
        compatible = true;
        if (!editing) path.value = found.destination;
        setText(
          size,
          `Ren’Py ${found.version} · файлов ${found.files} · для копии примерно ${(found.bytes / 1024 / 1024).toLocaleString("ru", { maximumFractionDigits: 0 })} МБ`,
        );
      } catch (error) {
        inspectionError = error.message;
        compatible = false;
        render();
      }
    }
    await models();
  }
  api.lifecycle.register("projectWorkflow", "engineState", (value) => {
    if (moving) return;
    engine = value.engine?.id === "renpy";
    compatible = !!value.engine_module?.supported;
    if (value.project !== project && !state?.busy) {
      project = value.project;
      state = null;
    }
    render();
    if (engine) poll().catch((error) => setText(status, error.message));
  });
  api.lifecycle.register("refresh", "engineProject", () => {
    if (
      !moving &&
      project &&
      project !== api.project() &&
      expectedPreparation !== project
    ) {
      dialog.close();
      state = null;
      project = api.project();
    }
  });
  api.interval(() => {
    if (dialog.open || state?.busy)
      poll().catch((error) => setText(status, error.message));
  }, 1000);
  render();
  return {
    configure: openSettings,
    install: openSettings,
    stop: () => api.call("cancel"),
  };
}

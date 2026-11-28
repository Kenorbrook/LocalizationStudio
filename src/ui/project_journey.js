"use strict";
const projectJourney = (() => {
  let owner = 0,
    state = null,
    sequence = 0;
  const pane = element("section");
  pane.id = "projectJourney";
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
    ["install", "Встроить в игру"],
  ]) {
    const node = element("li", undefined, "journey-step");
    node.id = "journeyStep-" + key;
    const titleNode = element("h3");
    titleNode.append(document.createTextNode(title));
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
  const exportFile = action(
    steps.get("install").actions,
    "journeyExport",
    "Экспорт перевода…",
    openExport,
  );
  const pluginTools = element("div", undefined, "project-plugin-tools");
  pane.append(boundary, list, pluginTools);
  const preparation = settingsPopup(
    "projectPreparation",
    "Подготовка и сборка",
    pane,
  );
  const preparationEntry = element(
    "button",
    "Подготовка и сборка",
    "ux-project-settings-button",
  );
  preparationEntry.id = "projectPreparationEntry";
  $("projectSettingsButton").after(preparationEntry);
  preparationEntry.onclick = guard(async () => {
    if (!studioState.project) throw Error("Откройте проект");
    preparation.showModal();
    await refresh();
  });
  const exportBody = element("div");
  const exporting = {
    dialog: settingsPopup(
      "journeyExportDialog",
      "Экспорт перевода",
      exportBody,
    ),
    body: exportBody,
  };
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
        ? "Файл слоя перевода Ren’Py (.rpy)"
        : "JSON с оригиналом и переводом",
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
    const exists = !!studioState.project;
    list.hidden = !exists || !state;
    pane.setAttribute("aria-busy", String(exists && !state));
    setText(
      boundary,
      !exists
        ? "Откройте папку распакованной игры. Studio покажет доступные шаги и ограничения именно этого проекта."
        : state?.engine_module?.supported
          ? state.engine_module?.prepared
            ? "Рабочая копия подготовлена."
            : ""
          : "Для сборки нужен модуль движка.",
    );
    boundary.hidden = !boundary.textContent;
    preparationEntry.disabled = !exists;
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
          ? `\nЕщё ${state.pending_files.length} файлов найдено при сканировании. Они не входят в число импортированных строк.`
          : "") +
        (!state.engine_module?.prepared && state.archives
          ? `\nАрхивов игры: ${state.archives}. Studio не читает текст внутри этих архивов при обычном импорте.`
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
    exportFile.disabled = !state.files.length;
    files.disabled = !exists || !!state.engine_module?.prepared;
    setText(
      files,
      state.engine_module?.prepared
        ? "Слой подготовлен движком"
        : "Открыть поиск и импорт",
    );
  }
  async function refresh() {
    if (owner !== studioState.project) {
      owner = studioState.project;
      state = null;
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
        exporting.dialog.close();
        render();
      }
      if (preparation.open || owner !== studioState.project) await refresh();
    },
  );
  studioLifecycle.register("navigate", "projectJourney", () => {
    render();
    if (preparation.open) refresh().catch((e) => toast(e.message));
  });
  render();
  return { refresh };
})();

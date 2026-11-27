(() => {
  const area = element("section", undefined, "parallel-queues");
  area.id = "parallelQueues";
  const newTask = element("button", "Запустить параллельно");
  newTask.id = "startParallelQueue";
  const splitTask = element("button", "Разделить очередь");
  splitTask.id = "splitParallelQueue";
  const toolbar = element("div", undefined, "flex wrap");
  const overview = element("div", undefined, "parallel-queue-list");
  toolbar.append(newTask, splitTask);
  area.append(toolbar, overview);
  processTabs.after(area);

  const form = element("form", undefined, "parallel-form");
  const dialog = settingsPopup(
    "parallelQueueDialog",
    "Параллельная задача",
    form,
  );
  const stage = element("select");
  const source = element("select");
  const file = element("select");
  const executor = element("select");
  const model = element("input");
  const suggestions = element("datalist");
  suggestions.id = "parallelCloudModels";
  model.setAttribute("list", suggestions.id);
  const discover = element("button", "Загрузить модели");
  discover.type = "button";
  const count = element("input");
  count.type = "number";
  count.min = 0;
  count.max = 1000000;
  count.required = true;
  const status = element("p", undefined, "sub");
  status.setAttribute("role", "status");
  status.hidden = true;
  const submit = element("button", "Запустить", "primary");
  submit.type = "submit";
  const actions = element("div", undefined, "flex wrap");
  const cancel = element("button", "Отмена");
  cancel.type = "button";
  cancel.onclick = () => dialog.close();
  actions.append(submit, cancel);
  function field(label, input) {
    const box = element("label");
    box.append(element("span", label), input);
    return box;
  }
  function option(value, label) {
    const item = element("option", label);
    item.value = value;
    return item;
  }
  const stageField = field("Задача", stage);
  const sourceField = field("Исходная очередь", source);
  const modelField = field("Модель API", model);
  const countField = field("", count);
  form.append(
    stageField,
    sourceField,
    field("Файл", file),
    countField,
    field("Исполнитель", executor),
    modelField,
    discover,
    suggestions,
    status,
    actions,
  );
  function message(value) {
    setText(status, value);
    status.hidden = !value;
  }

  const showStates = new Set([
    "running",
    "queued",
    "paused",
    "held",
    "waiting",
  ]);
  const selectable = new Set(["running", "queued", "paused", "held"]);
  function activeQueues() {
    return (studioState.snapshot?.jobs || []).filter((job) =>
      showStates.has(job.state),
    );
  }
  function splitQueues() {
    return activeQueues().filter(
      (job) =>
        job.stage === "translate" &&
        ["running", "queued", "paused"].includes(job.state) &&
        job.total - job.done > 1,
    );
  }
  function modelLabel(job) {
    const config = JSON.parse(job.settings || "{}");
    if (job.provider === "cloud")
      return (
        apiConnectionName(config.cloud_connection) +
        " · " +
        (config.cloud_model || "API")
      );
    return (
      (job.provider === "mcp" ? config.mcp_model_hint : config.model) ||
      "Локально"
    );
  }
  function queueTitle(job) {
    return (stageTitles[job.stage] || "Задача") + " · " + modelLabel(job);
  }
  let rendered = "";
  function render() {
    const queues = activeQueues();
    splitTask.hidden = !splitQueues().length;
    const selected = studioState.job?.id;
    const signature = JSON.stringify(
      queues.map((job) => [
        job.id,
        job.state,
        job.done,
        job.total,
        !!job.retry_at,
        job.worker_active,
        job.stage,
        job.settings,
        modelLabel(job),
        selected === job.id,
      ]),
    );
    if (rendered === signature) return;
    rendered = signature;
    overview.replaceChildren(
      ...queues.map((job) => {
        const row = element("div", undefined, "parallel-queue-row");
        const title = element(
          "button",
          queueTitle(job),
          "parallel-queue-title",
        );
        title.disabled = !selectable.has(job.state);
        title.setAttribute("aria-pressed", String(selected === job.id));
        title.onclick = guard(async () => {
          if (studioState.dirty.size)
            throw Error("Сохраните или отмените правки");
          studioState.selectedJobs.set(studioState.project, job.id);
          await refresh();
        });
        const detail = element(
          "span",
          (job.state === "running" && job.retry_at
            ? "Ожидание API"
            : jobLabels[job.state]) +
            " · " +
            jobCount(job),
          "sub",
        );
        const controls = element("div", undefined, "flex wrap");
        for (const [mode, caption] of [
          [
            job.state === "running" ? "pause" : "resume",
            job.state === "running" ? "Пауза" : "Продолжить",
          ],
          ["cancel", "Отменить"],
        ]) {
          const button = element("button", caption);
          button.disabled =
            mode === "resume" &&
            (job.worker_active || !["paused", "held"].includes(job.state));
          if (mode === "resume" && ["queued", "waiting"].includes(job.state))
            continue;
          button.onclick = guard(async () => {
            if (mode === "resume" && studioState.dirty.size)
              throw Error("Сохраните или отмените правки");
            await request("control", {
              id: job.id,
              mode,
              preserve_project_settings: true,
            });
            await refresh();
          });
          controls.append(button);
        }
        row.append(title, detail, controls);
        return row;
      }),
    );
  }
  studioLifecycle.register("refresh", "parallelQueues", render);
  studioLifecycle.register("navigate", "parallelQueues", render);

  let project = 0;
  let splitting = false;
  let connections = [];
  let busy = false;
  let openVersion = 0;
  function chosenConnection() {
    return connections.find((item) => executor.value === "api:" + item.id);
  }
  function renderExecutor(prefill = "") {
    const connection = chosenConnection();
    modelField.hidden = !connection;
    discover.hidden = !connection;
    model.required = !!connection;
    model.value =
      prefill || (connection?.models?.length === 1 ? connection.models[0] : "");
    suggestions.replaceChildren(
      ...(connection?.models || []).map((id) => option(id, id)),
    );
  }
  executor.onchange = () => renderExecutor();
  function changeSource() {
    const selected = studioState.snapshot.jobs.find(
      (job) => job.id === +source.value,
    );
    if (selected && splitting)
      count.max = Math.max(0, selected.total - selected.done - 1);
  }
  source.onchange = changeSource;
  discover.onclick = async () => {
    const connection = chosenConnection();
    if (!connection) return;
    discover.disabled = true;
    message("");
    try {
      connection.models = await window.pywebview.api.discover_cloud_models(
        connection.id,
      );
      if (chosenConnection() === connection) renderExecutor(model.value);
    } catch (error) {
      message(error.message);
    } finally {
      discover.disabled = false;
    }
  };
  async function open(split) {
    if (studioState.dirty.size) throw Error("Сохраните или отмените правки");
    project = studioState.project;
    const owner = project;
    const version = ++openVersion;
    splitting = split;
    setText(
      $("parallelQueueDialogTitle"),
      split ? "Разделить очередь" : "Параллельная задача",
    );
    stageField.hidden = split;
    sourceField.hidden = !split;
    stage.replaceChildren(
      option("translate", "Перевести новые строки"),
      option("review", "Редактировать готовый перевод"),
      option("cloud", "Проверить перевод через API"),
    );
    stage.value = split ? "translate" : "review";
    source.replaceChildren(
      ...splitQueues().map((job) => option(job.id, queueTitle(job))),
    );
    if (splitQueues().some((job) => job.id === studioState.job?.id))
      source.value = studioState.job.id;
    file.replaceChildren(
      option("", "Все файлы"),
      ...studioState.snapshot.files.map((item) =>
        option(item.id, item.path.split(/[\\/]/).pop()),
      ),
    );
    setText(
      countField.firstElementChild,
      split
        ? "Строк во вторую очередь (0 — около половины)"
        : "Строк в задачу (0 — все свободные)",
    );
    count.value = 0;
    count.max = 1000000;
    changeSource();
    submit.disabled = true;
    connections = [];
    executor.replaceChildren(option("", "Загрузка…"));
    renderExecutor();
    message("");
    dialog.showModal();
    const [local, cloud] = await Promise.allSettled([
      request("models"),
      window.pywebview?.api?.cloud_connections?.(),
    ]);
    if (
      !dialog.open ||
      studioState.project !== owner ||
      version !== openVersion
    )
      return;
    connections = (cloud.value || []).filter(
      (item) => !item.projects || item.projects.includes(owner),
    );
    const defaults = settings();
    executor.replaceChildren(
      option("", "Выберите исполнителя"),
      ...(local.value?.models || []).map((name) =>
        option("local:" + name, "Ollama · " + name),
      ),
      ...connections.map((item) =>
        option("api:" + item.id, "API · " + item.name),
      ),
    );
    const preferred =
      defaults.execution_kind === "cloud"
        ? "api:" + defaults.cloud_connection
        : "local:" + defaults.model;
    if ([...executor.options].some((item) => item.value === preferred))
      executor.value = preferred;
    renderExecutor(
      chosenConnection()?.id === defaults.cloud_connection
        ? defaults.cloud_model
        : "",
    );
    submit.disabled = executor.options.length < 2;
    if (submit.disabled)
      message("Нет установленных моделей или доступных API-ключей");
  }
  newTask.onclick = guard(() => open(false));
  splitTask.onclick = guard(() => open(true));
  form.onsubmit = async (event) => {
    event.preventDefault();
    if (
      busy ||
      !project ||
      project !== studioState.project ||
      !form.reportValidity()
    )
      return;
    const connection = chosenConnection();
    if (!executor.value) return message("Выберите исполнителя");
    if (splitting && !+source.value)
      return message("Выберите исходную очередь");
    const provider = connection ? "cloud" : "local";
    if (stage.value === "cloud" && !connection)
      return message("Выберите API-ключ");
    const values = { ...settings(), execution_kind: provider, provider };
    if (connection)
      Object.assign(values, {
        endpoint: connection.endpoint,
        cloud_connection: connection.id,
        cloud_model: model.value.trim(),
      });
    else
      Object.assign(values, {
        model: executor.value.slice(6),
        cloud_connection: "",
        cloud_model: "",
        endpoint: "",
      });
    busy = true;
    submit.disabled = true;
    message("");
    try {
      const result = await request("parallel-job", {
        project,
        stage: stage.value,
        provider,
        settings: values,
        split_from: splitting ? +source.value : 0,
        file: +file.value || null,
        take: +count.value,
      });
      studioState.selectedJobs.set(project, result.job);
      dialog.close();
      await refresh();
    } catch (error) {
      message(error.message);
    } finally {
      busy = false;
      submit.disabled = false;
    }
  };
})();

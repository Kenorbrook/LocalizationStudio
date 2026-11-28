const projectQueues = (() => {
  const area = element("section", undefined, "parallel-queues");
  area.id = "parallelQueues";
  const newTask = element("button", "Новая задача");
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
  const tuning = element("div", undefined, "parallel-tuning");
  const inputs = Object.fromEntries(
    [
      ["context", "Окно контекста, токенов", 1024, 131072, 8192],
      ["max_output", "Лимит ответа, токенов", 100, 100000, 1200],
      ["context_before", "Фраз перед строкой", 0, 100, 12],
      ["context_after", "Фраз после строки", 0, 100, 8],
      ["run_lines", "Лимит строк (0 — без лимита)", 0, 1000000, 0],
      ["run_minutes", "Лимит минут (0 — без лимита)", 0, 10080, 0],
    ].map(([key, label, min, max, fallback]) => {
      const input = element("input");
      input.type = "number";
      input.min = min;
      input.max = max;
      input.required = true;
      if (key === "run_minutes") input.step = "any";
      tuning.append(field(label, input));
      return [key, { input, fallback }];
    }),
  );
  form.append(
    stageField,
    sourceField,
    field("Файл", file),
    countField,
    field("Исполнитель", executor),
    modelField,
    discover,
    suggestions,
    tuning,
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
    setText(newTask, queues.length ? "Запустить параллельно" : "Новая задача");
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
        JSON.parse(job.settings || "{}").run_minutes
          ? Math.floor((job.run_elapsed || 0) / 5)
          : 0,
        job.run_done,
        job.error,
        selected === job.id,
      ]),
    );
    if (rendered === signature) return;
    rendered = signature;
    const expanded = new Set(
      [...overview.querySelectorAll("details[open]")].map(
        (node) => node.dataset.job,
      ),
    );
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
        const options = element("details", undefined, "queue-options");
        options.dataset.job = job.id;
        options.open = expanded.has(String(job.id));
        options.append(element("summary", "Действия"));
        const parameters = JSON.parse(job.settings || "{}");
        options.append(
          element(
            "p",
            [
              (parameters.source_language || "English") +
                " → " +
                (parameters.target_language || "Russian"),
              "Контекст: " +
                (parameters.context || 8192).toLocaleString("ru-RU") +
                " токенов",
              "Лимит ответа: " +
                (parameters.max_output || 1200).toLocaleString("ru-RU") +
                " токенов",
              "Соседние фразы: " +
                (parameters.context_before ?? 12) +
                " до, " +
                (parameters.context_after ?? 8) +
                " после",
            ].join("\n"),
            "queue-parameters",
          ),
        );
        const edit = element("button", "Настроить");
        edit.disabled =
          job.worker_active ||
          !["paused", "waiting", "held"].includes(job.state) ||
          job.provider === "mcp";
        edit.onclick = guard(() => open(false, job));
        options.append(edit);
        if (job.state === "waiting") {
          const startNow = element("button", "Запустить сейчас");
          startNow.onclick = guard(async () => {
            if (studioState.dirty.size)
              throw Error("Сохраните или отмените правки");
            await request("control", {
              id: job.id,
              mode: "start",
              preserve_project_settings: true,
            });
            await refresh();
          });
          options.append(startNow);
          const waiting = queues.filter((item) => item.state === "waiting");
          for (const [direction, label] of [
            ["up", "Поднять"],
            ["down", "Опустить"],
          ]) {
            const priority = element("button", label);
            priority.disabled =
              waiting[direction === "up" ? 0 : waiting.length - 1]?.id ===
              job.id;
            priority.onclick = guard(async () => {
              await request("queue-priority", { id: job.id, direction });
              await refresh();
            });
            options.append(priority);
          }
        }
        const log = element("button", "Журнал");
        log.onclick = guard(() => showJobLog(job));
        options.append(log);
        if (job.error)
          detail.append(element("span", job.error, "queue-diagnostic"));
        if (parameters.run_lines || parameters.run_minutes) {
          const limit = job.run_started
            ? describeRunProgress(job).text
            : parameters.run_lines
              ? "Лимит запуска: " + parameters.run_lines + " строк"
              : "Лимит запуска: " + parameters.run_minutes + " мин";
          detail.append(element("span", limit, "queue-diagnostic"));
        }
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
            if (mode === "cancel") {
              setText($("infoTitle"), "Отменить задачу #" + job.id + "?");
              const confirm = element("button", "Отменить задачу");
              confirm.onclick = guard(async () => {
                await request("control", { id: job.id, mode });
                $("infoDialog").close();
                await refresh();
              });
              $("infoContent").replaceChildren(
                element("p", queueTitle(job)),
                confirm,
              );
              $("infoDialog").showModal();
              return;
            }
            if (mode === "resume" && studioState.dirty.size)
              throw Error("Сохраните или отмените правки");
            await request("control", {
              id: job.id,
              mode,
              preserve_project_settings: true,
            });
            await refresh();
          });
          if (mode === "cancel") options.append(button);
          else controls.append(button);
        }
        controls.append(options);
        row.append(title, detail, controls);
        return row;
      }),
    );
  }
  studioLifecycle.register("refresh", "parallelQueues", render);
  studioLifecycle.register("navigate", "parallelQueues", render);

  let project = 0;
  let splitting = false;
  let editing = null;
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
  async function open(split, job = null, initialStage = "review") {
    if (studioState.dirty.size) throw Error("Сохраните или отмените правки");
    project = studioState.project;
    const owner = project;
    const version = ++openVersion;
    splitting = split;
    editing = job;
    tuning.hidden = !job;
    setText(
      $("parallelQueueDialogTitle"),
      job
        ? "Настройки очереди #" + job.id
        : split
          ? "Разделить очередь"
          : "Новая задача",
    );
    setText(submit, job ? "Сохранить" : "Запустить");
    stageField.hidden = split || !!job;
    sourceField.hidden = !split || !!job;
    file.parentElement.hidden = !!job;
    countField.hidden = !!job;
    stage.replaceChildren(
      option("translate", "Перевести новые строки"),
      option("review", "Редактировать готовый перевод"),
      option("cloud", "Проверить перевод через API"),
    );
    stage.value =
      job?.stage === "analyze"
        ? "translate"
        : job?.stage || (split ? "translate" : initialStage);
    const config = job ? JSON.parse(job.settings || "{}") : null;
    for (const [key, { input, fallback }] of Object.entries(inputs))
      input.value = config?.[key] ?? fallback;
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
    const defaults = config || settings();
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
      (job?.provider || defaults.execution_kind) === "cloud"
        ? "api:" + defaults.cloud_connection
        : "local:" + defaults.model;
    if (
      job?.provider === "local" &&
      defaults.model &&
      ![...executor.options].some((item) => item.value === preferred)
    )
      executor.append(option(preferred, "Ollama · " + defaults.model));
    if ([...executor.options].some((item) => item.value === preferred))
      executor.value = preferred;
    renderExecutor(
      chosenConnection()?.id === defaults.cloud_connection
        ? defaults.cloud_model
        : "",
    );
    submit.disabled = executor.options.length < 2;
    if (submit.disabled)
      message(
        local.value?.online === false
          ? "Ollama недоступна. Запустите Ollama или добавьте API-ключ."
          : "Нет установленных моделей или доступных API-ключей",
      );
  }
  newTask.onclick = guard(() =>
    open(false, null, activeQueues().length ? "review" : "translate"),
  );
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
    const values = {
      ...(editing
        ? Object.fromEntries(
            Object.entries(inputs).map(([key, { input }]) => [
              key,
              +input.value,
            ]),
          )
        : settings()),
      execution_kind: provider,
      provider,
    };
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
      if (editing) {
        await request("queue-settings", {
          id: editing.id,
          provider,
          settings: values,
        });
        dialog.close();
        await refresh();
        return;
      }
      const analyzeFirst =
        !splitting &&
        !activeQueues().length &&
        stage.value === "translate" &&
        values.analyze_before_translate &&
        !+count.value;
      const result = await request(analyzeFirst ? "job" : "parallel-job", {
        project,
        stage: stage.value,
        provider,
        settings: values,
        split_from: splitting ? +source.value : 0,
        file: +file.value || null,
        take: +count.value,
        persist_settings: false,
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
  const logContent = element("div");
  const logText = element("pre", undefined, "full-job-log");
  const refreshLog = element("button", "Обновить журнал");
  const openLog = element("button", "Открыть файл");
  const logDialog = settingsPopup("fullJobLog", "Журнал задачи", logContent);
  const logActions = element("div", undefined, "flex wrap");
  logActions.append(refreshLog, openLog);
  logContent.append(logActions, logText);
  async function showJobLog(job) {
    setText($("fullJobLogTitle"), "Журнал задачи #" + job.id);
    setText(logText, "");
    const update = async () => {
      const log = await request("job-log?id=" + job.id + "&lines=2000");
      setText(logText, log.text || "Журнал пуст");
    };
    refreshLog.onclick = guard(update);
    openLog.hidden = !window.pywebview?.api?.open_job_log;
    openLog.onclick = guard(() => window.pywebview.api.open_job_log(job.id));
    logDialog.showModal();
    await update();
  }
  return { area, queues: activeQueues, title: queueTitle, open, showJobLog };
})();

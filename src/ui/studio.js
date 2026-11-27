const TOKEN = document.querySelector('meta[name="studio-token"]').content;
const $ = (id) => document.getElementById(id);
const labels = {
  empty: "Без перевода",
  translated: "Переведено",
  edited: "После редакции",
  verified: "Проверено",
  preserved: "Сохранено без перевода",
};
const jobLabels = {
  queued: "В очереди",
  running: "Работает",
  paused: "На паузе",
  done: "Завершено",
  incomplete: "Есть ошибки",
  cancelled: "Отменено",
  waiting: "Ожидает",
  held: "Ожидает продолжения",
};
function toast(message) {
  $("toast").textContent = message;
  $("toast").style.display = "block";
  setTimeout(() => ($("toast").style.display = "none"), 6500);
}
async function request(path, data) {
  const r = await fetch("/api/" + path, {
    method: data ? "POST" : "GET",
    headers: { "X-Studio-Token": TOKEN, "Content-Type": "application/json" },
    body: data ? JSON.stringify(data) : undefined,
  });
  const value = await r.json();
  if (!r.ok) throw Error(value.error || "Ошибка запроса");
  return value;
}
function guard(fn) {
  return async (...args) => {
    try {
      await fn(...args);
    } catch (e) {
      toast(e.message);
    }
  };
}
function element(tag, text, cls) {
  let e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}
function setText(target, text) {
  const node = typeof target === "string" ? $(target) : target;
  const value = String(text);
  if (node.textContent !== value) node.textContent = value;
}
function countLabel(value, one, few, many) {
  const form = new Intl.PluralRules("ru").select(value);
  return `${value.toLocaleString("ru-RU")} ${{ one, few, many }[form] || many}`;
}
function settings() {
  let s = {};
  for (let id of [
    "model",
    "provider",
    "model_sort",
    "context",
    "source_language",
    "target_language",
    "endpoint",
    "cloud_model",
    "max_calls",
    "max_output",
  ])
    s[id] = $(id).value;
  return studioLifecycle.settings(s);
}
function loadSettings(p) {
  const s = JSON.parse(p.settings || "{}");
  $("endpoint").value = "";
  $("cloud_model").value = "";
  for (const [k, v] of Object.entries(s)) {
    if ($(k)) $(k).value = v;
  }
}
let projectVersion = "",
  fileVersion = "",
  statsVersion = "",
  liveVersion = "";
const stageTitles = {
  translate: "Перевод",
  review: "Редактура",
  cloud: "Проверка",
  analyze: "Анализ проекта",
};
function showActivity() {
  const active = studioState.snapshot.activity || [];
  const live = active.filter((j) => j.state !== "paused");
  const task =
    live.find((j) => j.project === studioState.project) ||
    live[0] ||
    active.find((j) => j.project === studioState.project) ||
    active[0];
  const button = $("activeProjectJob");
  button.hidden = !task;
  if (!task) return;
  const status =
    task.state === "paused"
      ? "Пауза"
      : task.state === "running" && task.retry_at
        ? "Ожидание API"
        : stageTitles[task.stage] || "Задача";
  const label = `${task.name} · ${status}`;
  const count = `${task.done.toLocaleString("ru-RU")} / ${task.total.toLocaleString("ru-RU")}`;
  const title = `${label} · ${count}${task.model ? " · " + task.model : ""}`;
  if (button.title !== title) {
    button.title = title;
    button.replaceChildren(element("span", label), element("b", count));
  }
  button.dataset.project = task.project;
}
function jobCount(job) {
  return job
    ? `${job.done.toLocaleString("ru-RU")} / ${job.total.toLocaleString("ru-RU")}`
    : "";
}
async function refreshState(initial = false) {
  studioState.snapshot = await request(
    "state?project=" +
      studioState.project +
      "&editing=" +
      (studioState.dirty.size ? 1 : 0),
  );
  studioState.project = studioState.snapshot.project || 0;
  showActivity();
  const pv = JSON.stringify(
    studioState.snapshot.projects.map((p) => [p.id, p.name, p.active]),
  );
  if (pv !== projectVersion) {
    $("projects").replaceChildren(
      ...studioState.snapshot.projects.map((p) => {
        let o = element("option", (p.active ? "⟳ " : "") + p.name);
        o.value = p.id;
        return o;
      }),
    );
    projectVersion = pv;
  }
  $("projects").value = studioState.project;
  if (initial && studioState.project)
    loadSettings(
      studioState.snapshot.projects.find((p) => p.id == studioState.project),
    );
  if (!studioState.file && studioState.snapshot.files.length) {
    studioState.file = studioState.snapshot.files[0].id;
    await loadRows();
  }
  const fv = JSON.stringify([studioState.file, studioState.snapshot.files]);
  if (fv !== fileVersion) {
    const scroll = $("files").parentElement.scrollTop;
    $("files").replaceChildren(
      ...studioState.snapshot.files.map((f) => {
        let e = element(
          "div",
          undefined,
          "file" + (f.id === studioState.file ? " active" : ""),
        );
        let name = f.path.split(/[\\/]/).pop();
        e.append(
          element("strong", "▤ " + name),
          element("small", f.total + " строк · проверено " + (f.verified || 0)),
        );
        e.onclick = guard(async () => {
          if (studioState.dirty.size) {
            toast("Сохраните правки перед сменой файла");
            return;
          }
          studioState.file = f.id;
          studioState.offset = 0;
          await refresh();
          await loadRows();
        });
        if (f.id === studioState.file) ensureBlockPicker(e, f);
        return e;
      }),
    );
    $("files").parentElement.scrollTop = scroll;
    fileVersion = fv;
  }
  setText("errorsButton", "Ошибки · " + (studioState.snapshot.errors || 0));
  const sv = JSON.stringify(studioState.snapshot.counts);
  if (sv !== statsVersion) {
    if (!$("stats").children.length)
      $("stats").replaceChildren(
        ...["empty", "translated", "edited", "verified"].map((s) => {
          let d = element("div", undefined, "stat " + s);
          d.dataset.status = s;
          d.append(
            element("b", studioState.snapshot.counts[s] || 0),
            element("span", labels[s]),
          );
          return d;
        }),
      );
    for (const stat of $("stats").children)
      setText(
        stat.querySelector("b"),
        studioState.snapshot.counts[stat.dataset.status] || 0,
      );
    statsVersion = sv;
  }
  studioState.job =
    studioState.snapshot.jobs.find((j) => j.state === "running") ||
    studioState.snapshot.jobs.find((j) => j.state === "queued") ||
    studioState.snapshot.jobs.find((j) => j.state === "paused") ||
    studioState.snapshot.jobs.find((j) => j.state === "held") ||
    studioState.snapshot.jobs.find((j) => j.state !== "waiting") ||
    null;
  setText(
    "jobState",
    studioState.job
      ? studioState.job.state === "running" && studioState.job.retry_at
        ? "Ожидание API"
        : jobLabels[studioState.job.state] || "Задача"
      : "Нет задачи",
  );
  setText("taskStage", stageTitles[studioState.job?.stage] || "Перевод");
  $("progress").max = studioState.job?.total || 1;
  $("progress").value = studioState.job?.done || 0;
  const job = studioState.job;
  const progress = $("progressText");
  progress.hidden = !job;
  $("progress").hidden = !job;
  const progressLabel = jobCount(job);
  if (
    progress.dataset.label !== progressLabel ||
    progress.dataset.rate !== String(job?.rate || 0)
  ) {
    progress.dataset.label = progressLabel;
    progress.dataset.rate = String(job?.rate || 0);
    progress.replaceChildren(element("strong", progressLabel + " строк"));
    if (job?.state === "running" && job.rate)
      progress.append(element("span", job.rate.toFixed(1) + " строк/мин"));
  }
  $("progressError").hidden = !job?.error;
  const retryIn =
    job?.state === "running" && job.retry_at
      ? Math.max(0, Math.ceil(job.retry_at - Date.now() / 1000))
      : null;
  setText(
    "progressError",
    retryIn === null
      ? job?.error || ""
      : `${job.error} · повтор через ${retryIn} с`,
  );
  $("pause").disabled = !studioState.job || studioState.job.state !== "running";
  $("resume").disabled = !studioState.job || studioState.job.state !== "paused";
  $("cancel").disabled =
    !studioState.job ||
    !["running", "paused", "queued"].includes(studioState.job.state);
  const r = studioState.job?.current
    ? await request("current?id=" + studioState.job.current)
    : null;
  const partial =
    studioState.job &&
    ["running", "paused"].includes(studioState.job.state) &&
    studioState.job.fragment_total
      ? studioState.job.fragment_preview
      : "";
  const lv = JSON.stringify(
    r
      ? [
          r.id,
          r.source,
          r.text,
          r.status,
          partial,
          studioState.job?.fragment_done,
          studioState.job?.fragment_total,
          studioState.job?.state,
          studioState.job?.worker_active,
          !!studioState.job?.retry_at,
        ]
      : null,
  );
  if (lv !== liveVersion) {
    const top = $("live").scrollTop;
    if (r)
      $("live").replaceChildren(
        element("label", "Оригинал · #" + r.id),
        element("div", r.source),
        element(
          "label",
          partial
            ? "ЧАСТИ " +
                studioState.job.fragment_done +
                "/" +
                studioState.job.fragment_total +
                " · ПРЕДВАРИТЕЛЬНЫЙ РЕЗУЛЬТАТ (КОНЕЦ ТЕКСТА)"
            : "Перевод",
        ),
        element(
          "div",
          partial ||
            (r.text !== r.source || r.status === "preserved" ? r.text : "") ||
            pendingTranslationText(studioState.job),
        ),
      );
    else
      $("live").replaceChildren(
        element("label", "СЕЙЧАС В РАБОТЕ"),
        element("div", "Здесь будут оригинал и результат текущей строки."),
      );
    $("live").scrollTop = top;
    liveVersion = lv;
  }
  $("live").hidden = !r;
  const log = studioState.job
    ? await request("job-log?id=" + studioState.job.id)
    : { text: "" };
  const panel = $("jobLog");
  if (panel.textContent !== log.text) {
    const follow =
      panel.scrollHeight - panel.scrollTop - panel.clientHeight < 24;
    const top = panel.scrollTop;
    panel.textContent = log.text;
    panel.scrollTop = follow ? panel.scrollHeight : top;
  }
  updateProjectScreen();
  if (!studioState.dirty.size && studioState.file && !initial)
    await loadRows(true);
}
function pendingTranslationText(job) {
  if (job?.state === "running" && job.retry_at)
    return "Ожидаем повторного запроса…";
  if (job?.worker_active && ["paused", "cancelled"].includes(job.state))
    return "Завершаем текущий запрос…";
  return (
    {
      queued: "Запускаем обработчик…",
      running: "Модель работает…",
      paused: "Перевод на паузе. Результат этой строки ещё не сохранён.",
      cancelled: "Задача отменена. Результат этой строки ещё не сохранён.",
    }[job?.state] || "Результат этой строки ещё не сохранён."
  );
}
function currentRecordPage() {
  if (["home", "errors", "plugins"].includes(studioState.viewMode)) return null;
  const specialized = studioLifecycle.page(studioState.viewMode);
  if (specialized) return specialized;
  if (!studioState.file) return null;
  return {
    path: `records?file=${studioState.file}&offset=${studioState.offset}&search=${encodeURIComponent($("search").value)}&status=${$("status").value}`,
    identity: JSON.stringify([
      studioState.project,
      studioState.viewMode,
      studioState.file,
      studioState.offset,
      $("search").value,
      $("status").value,
    ]),
  };
}
function editorIsActive() {
  return (
    studioState.dirty.size ||
    document.activeElement.matches(
      "textarea, input, select, [contenteditable='true']",
    )
  );
}
async function loadRows(quiet = false) {
  const page = currentRecordPage();
  if (!page || (quiet && editorIsActive())) return;
  const data = await request(page.path);
  if (
    page.identity !== currentRecordPage()?.identity ||
    (quiet && editorIsActive())
  )
    return;
  const container = $("rows").parentElement;
  const scroll = container.scrollTop;
  renderRows(data, quiet);
  if (quiet) container.scrollTop = scroll;
  const position = data.offset ?? studioState.offset;
  const size = data.page_size || 50;
  $("prev").disabled = !position;
  $("next").disabled = position + size >= data.total;
  document
    .querySelectorAll(".record")
    .forEach((card) =>
      card.classList.toggle(
        "current",
        +card.dataset.id === studioState.job?.current,
      ),
    );
}
function humanVerified(row) {
  return row.status === "verified" && row.reviewer?.startsWith("human");
}
function renderRecordCards(data, quiet = false) {
  studioState.rows = data.rows;
  studioState.total = data.total;
  setText(
    "pageInfo",
    typeof processPageInfo === "function"
      ? processPageInfo(data)
      : studioState.total
        ? `${studioState.offset + 1}–${Math.min(studioState.offset + 50, studioState.total)} из ${studioState.total}`
        : "Нет строк",
  );
  $("prev").disabled = studioState.offset === 0;
  $("next").disabled = studioState.offset + 50 >= studioState.total;
  const destination = [];
  const existing = new Map(
    [...$("rows").querySelectorAll(".record")].map((c) => [+c.dataset.id, c]),
  );
  for (const r of studioState.rows) {
    let card = element(
      "article",
      undefined,
      "record" + (studioState.job?.current === r.id ? " current" : ""),
    );
    card.dataset.id = r.id;
    card.dataset.version = JSON.stringify([
      r.revision,
      r.status,
      r.manual,
      r.flag,
      r.processed_at,
    ]);
    let top = element("div", undefined, "record-top");
    top.append(
      element("span", "#" + (r.position + 1)),
      element("span", r.speaker || "Текст", "grow"),
      element("span", labels[r.status], "badge " + r.status),
    );
    if (r.manual) top.append(element("span", "Ручная правка · защищена"));
    let pair = element("div", undefined, "pair");
    pair.append(element("div", r.source, "original"));
    let right = element("div", undefined, "translation");
    let area = element("textarea");
    area.value = r.text;
    area.setAttribute("aria-label", "Перевод строки " + (r.position + 1));
    right.append(area);
    pair.append(right);
    let actions = element("div", undefined, "record-actions");
    function button(text, action) {
      let b = element("button", text);
      b.onclick = guard(action);
      actions.append(b);
      return b;
    }
    button("Контекст", () => showRecordContext(r.id));
    button("История", () => showRecordHistory(r));
    if (r.manual)
      button("Снять защиту ручной правки", async () => {
        await request("unlock", { id: r.id });
        await refresh();
        toast("ИИ сможет менять эту строку при следующем запуске этапа");
      });
    const saveButton = button("Сохранить", async () => {
      const saved = await request("save", {
        id: r.id,
        revision: r.revision,
        text: area.value,
      });
      studioState.dirty.delete(r.id);
      studioLifecycle.notify("recordSaved", { before: r, after: saved });
      await refresh();
      await loadRows();
      toast("Правка сохранена и защищена");
    });
    const verifyButton = button("✓ Проверено мной", async () => {
      const saved = await request("save", {
        id: r.id,
        revision: r.revision,
        text: area.value,
        verified: true,
      });
      studioState.dirty.delete(r.id);
      studioLifecycle.notify("recordSaved", { before: r, after: saved });
      await refresh();
      await loadRows();
      toast("Проверено человеком · пометки сняты");
    });
    const cancelEdit = button("Отменить правку", async () => {
      if (studioState.dirty.has(r.id)) {
        const latest = await request("current?id=" + r.id);
        area.value = latest.text;
        studioState.dirty.delete(r.id);
        await loadRows();
        toast("Несохранённые изменения отменены");
      } else {
        const saved = await request("undo", { id: r.id, revision: r.revision });
        studioLifecycle.notify("recordSaved", { before: r, after: saved });
        await loadRows();
        await refresh();
        toast("Сохранённая правка отменена");
      }
    });
    const cancelVerification = button("Отменить проверку", async () => {
      const saved = await request("unverify", {
        id: r.id,
        revision: r.revision,
      });
      studioLifecycle.notify("recordSaved", { before: r, after: saved });
      await refresh();
      await loadRows();
      toast("Проверка отменена · текст сохранён");
    });
    const unlock = [...actions.children].find(
      (b) => b.textContent === "Снять защиту ручной правки",
    );
    if (unlock)
      unlock.title =
        "Позволить ИИ снова изменять эту строку при следующем переводе или проверке";
    function updateEditorActions() {
      const changed = area.value !== r.text;
      if (changed) studioState.dirty.add(r.id);
      else studioState.dirty.delete(r.id);
      const verified = humanVerified(r);
      saveButton.hidden = !changed;
      verifyButton.hidden = verified && !changed;
      cancelVerification.hidden = !verified || changed;
      cancelEdit.hidden =
        !changed && (verified || !r.manual || !r.reviewer?.startsWith("human"));
      cancelEdit.disabled = !changed && !r.manual;
      if (unlock) unlock.hidden = verified;
    }
    area.oninput = updateEditorActions;
    updateEditorActions();
    card.append(top, pair, actions);
    const old = existing.get(r.id);
    destination.push(
      quiet && old?.dataset.version === card.dataset.version ? old : card,
    );
  }
  if (!studioState.rows.length)
    destination.push(
      quiet && $("rows").querySelector(".empty")
        ? $("rows").querySelector(".empty")
        : element("div", "Нет строк для отображения", "empty"),
    );
  let cursor = $("rows").firstChild;
  for (const node of destination) {
    if (node !== cursor) $("rows").insertBefore(node, cursor);
    cursor = node.nextSibling;
  }
  while (cursor) {
    const next = cursor.nextSibling;
    cursor.remove();
    cursor = next;
  }
}
async function start(stage, retry = false) {
  if (!studioState.project) throw Error("Сначала откройте проект");
  if (studioState.dirty.size)
    throw Error("Сохраните ручные правки перед запуском");
  if (
    (stage === "cloud" || $("provider").value === "cloud") &&
    !$("cloud_enabled").checked
  )
    throw Error("Включите облачные модели в дополнительных настройках проекта");
  studioState.lastStage = stage;
  await request("job", {
    project: studioState.project,
    stage,
    provider:
      stage === "cloud" && $("provider").value === "local"
        ? "cloud"
        : $("provider").value,
    settings: settings(),
    file: $("scope").value === "file" ? studioState.file : null,
    retry,
  });
  await refresh();
  toast("Задача запущена · прогресс и журнал справа");
}
$("newProject").onclick = () => {
  $("chooseProjectFolder").hidden =
    !window.pywebview?.api?.choose_project_folder;
  $("projectDialog").showModal();
};
$("chooseProjectFolder").onclick = guard(async () => {
  const selected = studioState.snapshot?.projects.find(
    (p) => p.id === studioState.project,
  );
  const path = await window.pywebview.api.choose_project_folder(
    $("rootPath").value || selected?.root || "",
  );
  if (path) {
    $("rootPath").value = path;
    $("openProject").focus();
  }
});
$("activeProjectJob").onclick = guard(async () => {
  const target = Number($("activeProjectJob").dataset.project);
  if (target !== studioState.project) {
    $("projects").value = target;
    await $("projects").onchange();
  }
  if (studioState.project === target)
    document.querySelector(".right").scrollTop = 0;
});
$("openProject").onclick = guard(async () => {
  if (studioState.dirty.size) throw Error("Сохраните или отмените правки");
  studioLifecycle.notify("projectNavigation");
  let r = await request("project", {
    root: $("rootPath").value,
    scan: $("scan").checked,
  });
  studioState.project = r.project.id;
  studioState.file = 0;
  studioState.offset = 0;
  $("projectDialog").close();
  await refresh(true);
  if (r.status === "existing") {
    toast("Проект уже добавлен");
    return;
  }
  if (r.status === "restored") {
    toast("Проект восстановлен из архива");
    return;
  }
  toast(
    "Добавлено строк: " +
      r.import.added +
      (r.import.errors.length ? " · ошибок: " + r.import.errors.length : ""),
  );
});
$("projects").onchange = guard(async () => {
  if (studioState.dirty.size) {
    $("projects").value = studioState.project;
    toast("Сохраните правки перед сменой проекта");
    return;
  }
  studioLifecycle.notify("projectNavigation");
  studioState.project = +$("projects").value;
  studioState.file = 0;
  studioState.offset = 0;
  await refresh(true);
});
$("importButton").onclick = () => {
  if (!studioState.project) return toast("Сначала откройте проект");
  $("importDialog").showModal();
};
$("doImport").onclick = guard(async () => {
  let count = 0;
  const uploaded = $("uploadFiles").files;
  if (uploaded.length) {
    let r = await request("upload", {
      project: studioState.project,
      files: await Promise.all(
        [...uploaded].map(async (f) => ({
          name: f.name,
          text: await f.text(),
        })),
      ),
    });
    count += r.added;
    for (const e of r.errors) toast(e.message);
  }
  const paths = $("importPaths")
    .value.split("\n")
    .map((x) => x.trim())
    .filter(Boolean);
  if (paths.length) {
    let r = await request("import", { project: studioState.project, paths });
    count += r.added;
    for (const e of r.errors) toast(e.message);
  }
  $("importDialog").close();
  $("uploadFiles").value = "";
  $("importPaths").value = "";
  await refresh();
  toast("Добавлено строк: " + count);
});
for (let b of document.querySelectorAll("[data-close]"))
  b.onclick = () => b.closest("dialog").close();
for (let b of document.querySelectorAll("[data-stage]"))
  b.onclick = guard(() => start(b.dataset.stage));
$("retry").onclick = guard(() => start(studioState.lastStage, true));
function resumeExecutionSettings(job) {
  const options = settings();
  if (job.provider !== options.provider) {
    for (const key of [
      "cloud_connection",
      "cloud_model",
      "endpoint",
      "model",
      "mcp_session",
      "mcp_model_hint",
    ])
      delete options[key];
  }
  return options;
}
for (let mode of ["pause", "resume", "cancel"])
  $(mode).onclick = guard(async () => {
    if (
      mode === "resume" &&
      studioState.job?.provider === "cloud" &&
      !$("cloud_enabled").checked
    )
      throw Error(
        "Это облачная очередь. Включите облачные модели в дополнительных настройках или отмените её и создайте локальную задачу",
      );
    const options =
      mode === "resume" ? resumeExecutionSettings(studioState.job) : undefined;
    if (
      options?.provider === "cloud" &&
      studioState.job.provider === "cloud" &&
      !options.cloud_model
    )
      throw Error("Выберите модель API в настройках проекта");
    await request("control", {
      id: studioState.job.id,
      mode,
      settings: options,
      limits: mode === "resume" ? readRunLimits() : undefined,
      neighbors: mode === "resume" ? readNeighborContext() : undefined,
      auto_foreign: mode === "resume" ? $("auto_foreign").checked : undefined,
    });
    await refresh();
  });
$("saveSettings").onclick = guard(async () => {
  if (!studioState.project) throw Error("Сначала откройте проект");
  studioLifecycle.notify("validateProjectSettings");
  const projectSettings = settings();
  delete projectSettings.run_lines;
  delete projectSettings.run_minutes;
  const result = await request("settings", {
    project: studioState.project,
    settings: projectSettings,
    preferences: {
      max_phrases: +$("historyMaxPhrases").value,
      max_seconds: +$("historyMaxSeconds").value,
      page_size: +$("processPageSize").value,
    },
    key: $("apiKey").value,
  });
  $("apiKey").value = "";
  studioState.processPrefs = result.preferences;
  studioState.processOffset = 0;
  studioLifecycle.notify("projectSettingsSaved");
  await refresh();
  if (studioState.viewMode === "queue" && !studioState.dirty.size)
    await loadRows();
  toast("Настройки сохранены");
});
for (const [id, direction] of [
  ["prev", -1],
  ["next", 1],
]) {
  $(id).onclick = guard(async () => {
    if (studioState.dirty.size) throw Error("Сохраните правки");
    const page = studioLifecycle.page(studioState.viewMode);
    if (page?.move) page.move(direction);
    else studioState.offset = Math.max(0, studioState.offset + direction * 50);
    await loadRows();
  });
}
let searchTimer;
$("search").oninput = () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(
    guard(async () => {
      if (studioState.dirty.size) return;
      studioState.offset = 0;
      await loadRows();
    }),
    350,
  );
};
$("status").onchange = guard(async () => {
  if (studioState.dirty.size) throw Error("Сохраните правки");
  studioState.offset = 0;
  await loadRows();
});
$("exportButton").onclick = () => {
  if (!studioState.file) return;
  const p = studioState.snapshot.files.find((f) => f.id === studioState.file);
  $("exportPath").value =
    p.path +
    ".studio-export." +
    (p.kind === "renpy" && p.path.replaceAll("\\", "/").includes("/tl/")
      ? "rpy"
      : "json");
  $("exportDialog").showModal();
};
$("doExport").onclick = guard(async () => {
  if (studioState.dirty.size) throw Error("Сохраните правки перед экспортом");
  let r = await request("export", {
    file: studioState.file,
    destination: $("exportPath").value,
  });
  $("exportDialog").close();
  toast("Сохранено: " + r.path);
});
$("mcpButton").onclick = async () => {
  $("infoTitle").textContent = "MCP · внешняя ИИ";
  $("infoContent").replaceChildren(
    element(
      "p",
      "Скопируйте конфигурацию из localization_studio/mcp_config.json в настройки своего MCP-клиента. Инструменты позволяют читать проект блоками, предлагать правки и подтверждать проверенные строки. Предложение становится переводом только после вашего принятия в приложении.",
    ),
    element(
      "p",
      "MCP не предоставляет модель или подписку: внешняя ИИ подключается отдельно. Для облачного API используйте настройки справа.",
    ),
  );
  studioLifecycle.notify("mcpInfo");
  $("infoDialog").showModal();
};
window.addEventListener("beforeunload", (e) => {
  if (studioState.dirty.size) {
    e.preventDefault();
    e.returnValue = "";
  }
});
window.addEventListener(
  "DOMContentLoaded",
  guard(async () => {
    await refresh(true);
    setInterval(async () => {
      if (
        studioState.busy ||
        document.querySelector("dialog[open]") ||
        document.activeElement.tagName === "TEXTAREA"
      )
        return;
      studioState.busy = true;
      try {
        await refresh();
        $("connection").hidden = true;
      } catch (e) {
        setText("connection", "Сервер приложения недоступен");
        $("connection").hidden = false;
      } finally {
        studioState.busy = false;
      }
    }, 2500);
    const inventory = await request("models");
    studioLifecycle.notify("localModels", inventory);
    if (inventory.models.length)
      applyModelList(
        inventory.model_details || inventory.models.map((name) => ({ name })),
      );
  }),
);
async function refresh(initial = false) {
  const previousProject = studioState.project;
  await refreshState(initial);
  await studioLifecycle.refresh({ initial, previousProject });
}
function renderRows(data, quiet = false) {
  renderRecordCards(data, quiet);
  studioLifecycle.render(data, quiet);
}

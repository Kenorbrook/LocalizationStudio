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
  done: "DONE",
  incomplete: "INCOMPLETE",
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
function settings() {
  let s = {};
  for (let id of [
    "model",
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
  for (const [k, v] of Object.entries(s)) {
    if ($(k)) $(k).value = v;
  }
}
let projectVersion = "",
  fileVersion = "",
  statsVersion = "",
  liveVersion = "";
async function refreshState(initial = false) {
  studioState.snapshot = await request(
    "state?project=" +
      studioState.project +
      "&editing=" +
      (studioState.dirty.size ? 1 : 0),
  );
  studioState.project = studioState.snapshot.project || 0;
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
  setText(
    "errorsButton",
    "Ошибки этого проекта · " + (studioState.snapshot.errors || 0),
  );
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
    studioState.snapshot.jobs.find((j) =>
      ["running", "queued", "paused"].includes(j.state),
    ) ||
    studioState.snapshot.jobs.find((j) => j.state === "held") ||
    studioState.snapshot.jobs.find((j) => j.state !== "waiting") ||
    null;
  setText(
    "jobState",
    studioState.job ? jobLabels[studioState.job.state] : "Нет задачи",
  );
  $("progress").max = studioState.job?.total || 1;
  $("progress").value = studioState.job?.done || 0;
  setText(
    "progressText",
    studioState.job
      ? `${studioState.job.done} / ${studioState.job.total} · ${studioState.job.stage} · ${studioState.job.error || studioState.job.provider}${studioState.job.rate ? " · " + studioState.job.rate.toFixed(1) + " строк/мин" : ""}${studioState.job.state === "running" && studioState.job.fragment_total ? " · частей " + studioState.job.fragment_done + "/" + studioState.job.fragment_total : ""}`
      : "Готово к работе",
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
          partial,
          studioState.job?.fragment_done,
          studioState.job?.fragment_total,
        ]
      : null,
  );
  if (lv !== liveVersion) {
    const top = $("live").scrollTop;
    if (r)
      $("live").replaceChildren(
        element("label", "ОРИГИНАЛ · #" + r.id),
        element("div", r.source),
        element(
          "label",
          partial
            ? "ЧАСТИ " +
                studioState.job.fragment_done +
                "/" +
                studioState.job.fragment_total +
                " · ПРЕДВАРИТЕЛЬНЫЙ РЕЗУЛЬТАТ (КОНЕЦ ТЕКСТА)"
            : "ПЕРЕВОД",
        ),
        element("div", partial || r.text || "Модель работает…"),
      );
    else
      $("live").replaceChildren(
        element("label", "СЕЙЧАС В РАБОТЕ"),
        element("div", "Здесь будут оригинал и результат текущей строки."),
      );
    $("live").scrollTop = top;
    liveVersion = lv;
  }
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
function currentRecordPage() {
  if (studioState.viewMode === "home") return null;
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
    studioState.dirty.size || document.activeElement.tagName === "TEXTAREA"
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
  const destination = document.createDocumentFragment();
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
    button("История", async () => {
      let history = await request("history?id=" + r.id);
      $("infoTitle").textContent = "История строки #" + (r.position + 1);
      $("infoContent").replaceChildren(
        ...history.map((h) => {
          let d = element("div", undefined, "error");
          d.append(
            element(
              "div",
              h.at + " · " + h.reviewer + " · " + labels[h.status],
            ),
            element("p", h.text),
            element("pre", h.reason),
          );
          return d;
        }),
      );
      $("infoDialog").showModal();
    });
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
    destination.append(
      quiet && old?.dataset.version === card.dataset.version ? old : card,
    );
  }
  if (!studioState.rows.length)
    destination.append(
      quiet && $("rows").querySelector(".empty")
        ? $("rows").querySelector(".empty")
        : element("div", "Нет строк для отображения", "empty"),
    );
  let cursor = $("rows").firstChild;
  for (const node of [...destination.childNodes]) {
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
  $("projectDialog").showModal();
};
$("openProject").onclick = guard(async () => {
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
for (let mode of ["pause", "resume", "cancel"])
  $(mode).onclick = guard(async () => {
    await request("control", {
      id: studioState.job.id,
      mode,
      settings: mode === "resume" ? settings() : undefined,
      limits: mode === "resume" ? readRunLimits() : undefined,
      neighbors: mode === "resume" ? readNeighborContext() : undefined,
      auto_foreign: mode === "resume" ? $("auto_foreign").checked : undefined,
    });
    await refresh();
  });
$("saveSettings").onclick = guard(async () => {
  if (!studioState.project) throw Error("Сначала откройте проект");
  await request("settings", {
    project: studioState.project,
    settings: settings(),
    key: $("apiKey").value,
  });
  $("apiKey").value = "";
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
    let m = await request("models");
    $("connection").textContent = m.online
      ? "● Ollama подключена · без VPN"
      : "Ollama недоступна · импорт и правки работают";
    if (m.models.length)
      applyModelList(m.model_details || m.models.map((name) => ({ name })));
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
      } catch (e) {
        $("connection").textContent =
          "Сервер недоступен; перезапустите приложение";
      } finally {
        studioState.busy = false;
      }
    }, 2500);
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

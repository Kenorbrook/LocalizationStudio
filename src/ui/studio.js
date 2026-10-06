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
let project = 0,
  file = 0,
  offset = 0,
  total = 0,
  rows = [],
  snapshot = null,
  job = null,
  busy = false,
  lastStage = "translate";
const dirty = new Set();
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
  snapshot = await request(
    "state?project=" + project + "&editing=" + (dirty.size ? 1 : 0),
  );
  project = snapshot.project || 0;
  const pv = JSON.stringify(
    snapshot.projects.map((p) => [p.id, p.name, p.active]),
  );
  if (pv !== projectVersion) {
    $("projects").replaceChildren(
      ...snapshot.projects.map((p) => {
        let o = element("option", (p.active ? "⟳ " : "") + p.name);
        o.value = p.id;
        return o;
      }),
    );
    projectVersion = pv;
  }
  $("projects").value = project;
  if (initial && project)
    loadSettings(snapshot.projects.find((p) => p.id == project));
  if (!file && snapshot.files.length) {
    file = snapshot.files[0].id;
    await loadRows();
  }
  const fv = JSON.stringify([file, snapshot.files]);
  if (fv !== fileVersion) {
    const scroll = $("files").parentElement.scrollTop;
    $("files").replaceChildren(
      ...snapshot.files.map((f) => {
        let e = element(
          "div",
          undefined,
          "file" + (f.id === file ? " active" : ""),
        );
        let name = f.path.split(/[\\/]/).pop();
        e.append(
          element("strong", "▤ " + name),
          element("small", f.total + " строк · проверено " + (f.verified || 0)),
        );
        e.onclick = guard(async () => {
          if (dirty.size) {
            toast("Сохраните правки перед сменой файла");
            return;
          }
          file = f.id;
          offset = 0;
          await refresh();
          await loadRows();
        });
        if (f.id === file) ensureBlockPicker(e, f);
        return e;
      }),
    );
    $("files").parentElement.scrollTop = scroll;
    fileVersion = fv;
  }
  setText("errorsButton", "Ошибки этого проекта · " + (snapshot.errors || 0));
  const sv = JSON.stringify(snapshot.counts);
  if (sv !== statsVersion) {
    if (!$("stats").children.length)
      $("stats").replaceChildren(
        ...["empty", "translated", "edited", "verified"].map((s) => {
          let d = element("div", undefined, "stat " + s);
          d.dataset.status = s;
          d.append(
            element("b", snapshot.counts[s] || 0),
            element("span", labels[s]),
          );
          return d;
        }),
      );
    for (const stat of $("stats").children)
      setText(
        stat.querySelector("b"),
        snapshot.counts[stat.dataset.status] || 0,
      );
    statsVersion = sv;
  }
  job =
    snapshot.jobs.find((j) =>
      ["running", "queued", "paused"].includes(j.state),
    ) ||
    snapshot.jobs.find((j) => j.state === "held") ||
    snapshot.jobs.find((j) => j.state !== "waiting") ||
    null;
  setText("jobState", job ? jobLabels[job.state] : "Нет задачи");
  $("progress").max = job?.total || 1;
  $("progress").value = job?.done || 0;
  setText(
    "progressText",
    job
      ? `${job.done} / ${job.total} · ${job.stage} · ${job.error || job.provider}${job.rate ? " · " + job.rate.toFixed(1) + " строк/мин" : ""}${job.state === "running" && job.fragment_total ? " · частей " + job.fragment_done + "/" + job.fragment_total : ""}`
      : "Готово к работе",
  );
  $("pause").disabled = !job || job.state !== "running";
  $("resume").disabled = !job || job.state !== "paused";
  $("cancel").disabled =
    !job || !["running", "paused", "queued"].includes(job.state);
  const r = job?.current ? await request("current?id=" + job.current) : null;
  const partial =
    job && ["running", "paused"].includes(job.state) && job.fragment_total
      ? job.fragment_preview
      : "";
  const lv = JSON.stringify(
    r
      ? [
          r.id,
          r.source,
          r.text,
          partial,
          job?.fragment_done,
          job?.fragment_total,
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
                job.fragment_done +
                "/" +
                job.fragment_total +
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
  const log = job ? await request("job-log?id=" + job.id) : { text: "" };
  const panel = $("jobLog");
  if (panel.textContent !== log.text) {
    const follow =
      panel.scrollHeight - panel.scrollTop - panel.clientHeight < 24;
    const top = panel.scrollTop;
    panel.textContent = log.text;
    panel.scrollTop = follow ? panel.scrollHeight : top;
  }
  updateProjectScreen();
  if (!dirty.size && file && !initial) await loadRows(true);
}
function currentRecordPage() {
  if (viewMode === "home") return null;
  const specialized = studioLifecycle.page(viewMode);
  if (specialized) return specialized;
  if (!file) return null;
  return {
    path: `records?file=${file}&offset=${offset}&search=${encodeURIComponent($("search").value)}&status=${$("status").value}`,
    identity: JSON.stringify([
      project,
      viewMode,
      file,
      offset,
      $("search").value,
      $("status").value,
    ]),
  };
}
function editorIsActive() {
  return dirty.size || document.activeElement.tagName === "TEXTAREA";
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
  const position = data.offset ?? offset;
  const size = data.page_size || 50;
  $("prev").disabled = !position;
  $("next").disabled = position + size >= data.total;
  document
    .querySelectorAll(".record")
    .forEach((card) =>
      card.classList.toggle("current", +card.dataset.id === job?.current),
    );
}
function renderRecordCards(data, quiet = false) {
  rows = data.rows;
  total = data.total;
  setText(
    "pageInfo",
    typeof processPageInfo === "function"
      ? processPageInfo(data)
      : total
        ? `${offset + 1}–${Math.min(offset + 50, total)} из ${total}`
        : "Нет строк",
  );
  $("prev").disabled = offset === 0;
  $("next").disabled = offset + 50 >= total;
  const destination = document.createDocumentFragment();
  const existing = new Map(
    [...$("rows").querySelectorAll(".record")].map((c) => [+c.dataset.id, c]),
  );
  for (const r of rows) {
    let card = element(
      "article",
      undefined,
      "record" + (job?.current === r.id ? " current" : ""),
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
    area.oninput = () => dirty.add(r.id);
    right.append(area);
    pair.append(right);
    let actions = element("div", undefined, "record-actions");
    function button(text, action) {
      let b = element("button", text);
      b.onclick = guard(action);
      actions.append(b);
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
    button("Сохранить", async () => {
      await request("save", {
        id: r.id,
        revision: r.revision,
        text: area.value,
      });
      dirty.delete(r.id);
      await refresh();
      toast("Правка сохранена и защищена");
    });
    button("✓ Проверено мной", async () => {
      await request("save", {
        id: r.id,
        revision: r.revision,
        text: area.value,
        verified: true,
      });
      dirty.delete(r.id);
      await refresh();
      toast("Текущая версия проверена человеком");
    });
    button("Отменить правку", async () => {
      if (dirty.has(r.id)) {
        const latest = await request("current?id=" + r.id);
        area.value = latest.text;
        dirty.delete(r.id);
        await loadRows();
        toast("Несохранённые изменения отменены");
      } else {
        await request("undo", { id: r.id, revision: r.revision });
        await loadRows();
        await refresh();
        toast("Сохранённая правка отменена");
      }
    });
    const cancelEdit = actions.lastChild;
    cancelEdit.disabled = !r.manual;
    area.addEventListener("input", () => (cancelEdit.disabled = false));
    if (r.manual) {
      const unlock = [...actions.children].find(
        (b) => b.textContent === "Снять защиту ручной правки",
      );
      unlock.title =
        "Позволить ИИ снова изменять эту строку при следующем переводе или проверке";
    }
    card.append(top, pair, actions);
    const old = existing.get(r.id);
    destination.append(
      quiet && old?.dataset.version === card.dataset.version ? old : card,
    );
  }
  if (!rows.length)
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
  if (!project) throw Error("Сначала откройте проект");
  if (dirty.size) throw Error("Сохраните ручные правки перед запуском");
  lastStage = stage;
  await request("job", {
    project,
    stage,
    provider:
      stage === "cloud" && $("provider").value === "local"
        ? "cloud"
        : $("provider").value,
    settings: settings(),
    file: $("scope").value === "file" ? file : null,
    retry,
  });
  await refresh();
  toast("Задача запущена · прогресс и журнал справа");
}
$("newProject").onclick = () => {
  $("projectDialog").showModal();
};
$("openProject").onclick = guard(async () => {
  let r = await request("project", {
    root: $("rootPath").value,
    scan: $("scan").checked,
  });
  project = r.project.id;
  file = 0;
  offset = 0;
  $("projectDialog").close();
  await refresh(true);
  toast(
    "Добавлено строк: " +
      r.import.added +
      (r.import.errors.length ? " · ошибок: " + r.import.errors.length : ""),
  );
});
$("projects").onchange = guard(async () => {
  if (dirty.size) {
    $("projects").value = project;
    toast("Сохраните правки перед сменой проекта");
    return;
  }
  project = +$("projects").value;
  file = 0;
  offset = 0;
  await refresh(true);
});
$("importButton").onclick = () => {
  if (!project) return toast("Сначала откройте проект");
  $("importDialog").showModal();
};
$("doImport").onclick = guard(async () => {
  let count = 0;
  const uploaded = $("uploadFiles").files;
  if (uploaded.length) {
    let r = await request("upload", {
      project,
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
    let r = await request("import", { project, paths });
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
$("retry").onclick = guard(() => start(lastStage, true));
for (let mode of ["pause", "resume", "cancel"])
  $(mode).onclick = guard(async () => {
    await request("control", {
      id: job.id,
      mode,
      settings: mode === "resume" ? settings() : undefined,
      limits: mode === "resume" ? readRunLimits() : undefined,
      neighbors: mode === "resume" ? readNeighborContext() : undefined,
      auto_foreign: mode === "resume" ? $("auto_foreign").checked : undefined,
    });
    await refresh();
  });
$("saveSettings").onclick = guard(async () => {
  if (!project) throw Error("Сначала откройте проект");
  await request("settings", {
    project,
    settings: settings(),
    key: $("apiKey").value,
  });
  $("apiKey").value = "";
  toast("Настройки сохранены");
});
$("prev").onclick = guard(async () => {
  if (dirty.size) throw Error("Сохраните правки");
  offset = Math.max(0, offset - 50);
  await loadRows();
});
$("next").onclick = guard(async () => {
  if (dirty.size) throw Error("Сохраните правки");
  offset += 50;
  await loadRows();
});
let searchTimer;
$("search").oninput = () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(
    guard(async () => {
      if (dirty.size) return;
      offset = 0;
      await loadRows();
    }),
    350,
  );
};
$("status").onchange = guard(async () => {
  if (dirty.size) throw Error("Сохраните правки");
  offset = 0;
  await loadRows();
});
$("exportButton").onclick = () => {
  if (!file) return;
  const p = snapshot.files.find((f) => f.id === file);
  $("exportPath").value =
    p.path +
    ".studio-export." +
    (p.kind === "renpy" && p.path.replaceAll("\\", "/").includes("/tl/")
      ? "rpy"
      : "json");
  $("exportDialog").showModal();
};
$("doExport").onclick = guard(async () => {
  if (dirty.size) throw Error("Сохраните правки перед экспортом");
  let r = await request("export", { file, destination: $("exportPath").value });
  $("exportDialog").close();
  toast("Сохранено: " + r.path);
});
$("errorsButton").onclick = guard(async () => {
  let errors = await request("errors?project=" + project);
  $("infoTitle").textContent = "Ошибки · последние 100";
  $("infoContent").replaceChildren(
    ...errors.map((e) => {
      let d = element("div", undefined, "error");
      d.append(
        element(
          "div",
          e.at +
            " · задача " +
            (e.job || "импорт") +
            " · строка " +
            (e.record || "—"),
        ),
        element("div", e.source || ""),
        element("p", e.message),
      );
      if (e.record) {
        let b = element("button", "Открыть строку");
        b.onclick = guard(async () => {
          if (dirty.size) throw Error("Сохраните правки");
          showView("text");
          let r = await request("current?id=" + e.record);
          file = r.file;
          offset = Math.floor(r.position / 50) * 50;
          $("search").value = "";
          $("status").value = "";
          $("infoDialog").close();
          await refresh();
          await loadRows();
        });
        d.append(b);
      }
      return d;
    }),
  );
  if (!errors.length)
    $("infoContent").append(element("p", "Открытых ошибок нет"));
  $("infoDialog").showModal();
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
  $("infoDialog").showModal();
};
window.addEventListener("beforeunload", (e) => {
  if (dirty.size) {
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
        busy ||
        document.querySelector("dialog[open]") ||
        document.activeElement.tagName === "TEXTAREA"
      )
        return;
      busy = true;
      try {
        await refresh();
      } catch (e) {
        $("connection").textContent =
          "Сервер недоступен; перезапустите приложение";
      } finally {
        busy = false;
      }
    }, 2500);
  }),
);
async function refresh(initial = false) {
  const previousProject = project;
  await refreshState(initial);
  await studioLifecycle.refresh({ initial, previousProject });
}
function renderRows(data, quiet = false) {
  renderRecordCards(data, quiet);
  studioLifecycle.render(data, quiet);
}

// Translation process: bounded future rows, retained history, and project folders.
const processControls = element("div");
processControls.id = "processControls";
processControls.hidden = true;
const processTabs = element("div", undefined, "flex wrap");
const futureTab = element("button", "Очередь"),
  historyTab = element("button", "История");
futureTab.id = "futureTab";
historyTab.id = "historyTab";
processTabs.append(futureTab, historyTab);
processControls.append(processTabs);
const historySettings = element("details");
historySettings.id = "historySettings";
historySettings.append(element("summary", "Размер очереди и показ истории"));
const settingsGrid = element("div", undefined, "flex wrap");
for (const [id, label, value] of [
  ["historyMaxPhrases", "Последних фраз в истории (0 — без лимита)", 200],
  ["historyMaxSeconds", "Хранить в истории, секунд (0 — без лимита)", 600],
  ["processPageSize", "Строк на странице / в очереди (10–200)", 50],
]) {
  const box = element("label", label, "sub");
  const input = element("input");
  input.id = id;
  input.type = "number";
  input.min = id === "processPageSize" ? 10 : 0;
  input.value = value;
  input.style.cssText = "display:block;width:190px;margin-top:6px";
  box.append(input);
  settingsGrid.append(box);
}
const saveHistorySettings = element("button", "Применить");
saveHistorySettings.id = "saveHistorySettings";
historySettings.append(
  settingsGrid,
  element(
    "p",
    "Оба ограничения применяются одновременно. Они скрывают старые результаты только здесь: весь перевод остаётся в проекте. Даже без лимитов список показывается страницами.",
    "sub",
  ),
  saveHistorySettings,
);
processControls.append(historySettings);
textPane.prepend(processControls);
const badFolder = element("button", "▣ Брак · 0"),
  reviewFolder = element("button", "▣ Ручная проверка · 0");
badFolder.id = "badFolder";
reviewFolder.id = "reviewFolder";
projectTools.append(badFolder, reviewFolder);
function updateProcessControls() {
  processControls.hidden = studioState.viewMode !== "queue";
  futureTab.classList.toggle("primary", studioState.processTab === "queue");
  historyTab.classList.toggle("primary", studioState.processTab === "history");
  textPane
    .querySelectorAll(".pagination button")
    .forEach(
      (button) =>
        (button.hidden =
          studioState.viewMode === "queue" &&
          studioState.processTab === "queue"),
    );
  setText(badFolder, "▣ Брак · " + (studioState.snapshot?.flags?.bad || 0));
  setText(
    reviewFolder,
    "▣ Ручная проверка · " + (studioState.snapshot?.flags?.review || 0),
  );
}
studioLifecycle.register("navigate", "processControls", updateProcessControls);
function processPageInfo(data) {
  if (studioState.viewMode === "queue" && studioState.processTab === "queue")
    return `В очереди: ${data.total} · показаны следующие ${data.rows.length} записей`;
  const start = data.offset || 0;
  return data.total
    ? `${start + 1}–${Math.min(start + data.rows.length, data.total)} из ${data.total}${studioState.viewMode === "queue" ? " · история процесса" : ""}`
    : studioState.viewMode === "queue"
      ? "История пуста или скрыта установленными ограничениями"
      : studioState.viewMode === "flags"
        ? "В этой папке нет помеченных строк"
        : "Нет строк";
}
async function switchProcess(tab) {
  if (studioState.dirty.size)
    throw Error("Сохраните или отмените правки перед сменой вкладки");
  studioState.processTab = tab;
  studioState.processOffset = 0;
  showView("queue");
  await loadRows();
}
futureTab.onclick = guard(() => switchProcess("queue"));
historyTab.onclick = guard(() => switchProcess("history"));
for (const [button, kind] of [
  [badFolder, "bad"],
  [reviewFolder, "review"],
])
  button.onclick = guard(async () => {
    if (studioState.dirty.size) throw Error("Сохраните или отмените правки");
    studioState.markKind = kind;
    studioState.marksOffset = 0;
    showView("flags");
    await loadRows();
  });
saveHistorySettings.onclick = guard(async () => {
  studioState.processPrefs = await request("process-settings", {
    project: studioState.project,
    preferences: {
      max_phrases: +$("historyMaxPhrases").value,
      max_seconds: +$("historyMaxSeconds").value,
      page_size: +$("processPageSize").value,
    },
  });
  studioState.processOffset = 0;
  if (!studioState.dirty.size) await loadRows();
  toast("Настройки процесса сохранены для проекта");
});
for (const mode of ["queue", "flags", "preserved"]) {
  studioLifecycle.registerPage(mode, () => {
    const identity = JSON.stringify([
      studioState.project,
      mode,
      studioState.processTab,
      studioState.markKind,
      studioState.processOffset,
      studioState.marksOffset,
    ]);
    const paths = {
      preserved: () =>
        `preserved?project=${studioState.project}&offset=${studioState.marksOffset}`,
      flags: () =>
        `marked?project=${studioState.project}&kind=${studioState.markKind}&offset=${studioState.marksOffset}`,
      queue: () =>
        studioState.processTab === "history"
          ? `process-history?project=${studioState.project}&offset=${studioState.processOffset}`
          : `queue?project=${studioState.project}`,
    };
    return {
      identity,
      path: paths[mode](),
      move(direction) {
        if (mode === "flags" || mode === "preserved")
          studioState.marksOffset = Math.max(
            0,
            studioState.marksOffset + direction * 50,
          );
        else if (studioState.processTab === "history")
          studioState.processOffset = Math.max(
            0,
            studioState.processOffset +
              direction * studioState.processPrefs.page_size,
          );
      },
    };
  });
}
studioLifecycle.register("render", "processRender", (data, quiet = false) => {
  const byId = new Map(data.rows.map((row) => [row.id, row]));
  for (const card of $("rows").querySelectorAll(".record")) {
    const row = byId.get(+card.dataset.id);
    if (!row || card.dataset.markControls) return;
    card.dataset.markControls = "1";
    const actions = card.querySelector(".record-actions"),
      top = card.querySelector(".record-top");
    if (studioState.viewMode !== "text" && row.path) {
      const filename = element("span", row.path.split(/[\\/]/).pop(), "sub");
      filename.title = row.path;
      top.insertBefore(filename, top.lastChild);
    }
    if (/^[^\p{L}\p{N}]+$/u.test(row.source)) {
      card.classList.add("punctuation");
      card.querySelector("textarea").style.minHeight = "32px";
      card.querySelector("textarea").style.height = "32px";
    }
    if (row.flag) {
      const label = element(
        "span",
        row.flag === "bad" ? "Брак" : "Нужна ручная проверка",
        "badge",
      );
      label.style.color = label.style.borderColor =
        row.flag === "bad" ? "var(--red)" : "var(--yellow)";
      top.append(label);
    }
    if (row.processed_at) {
      const stamp = element(
        "span",
        new Date(row.processed_at * 1000).toLocaleTimeString("ru-RU"),
        "sub",
      );
      top.append(stamp);
    }
    for (const [label, kind] of row.flag
      ? [["Снять пометку", ""]]
      : [
          ["Брак", "bad"],
          ["Ручная проверка", "review"],
        ]) {
      const button = element("button", label);
      button.title =
        kind === "bad"
          ? "Пометить текущий перевод как заведомо плохой; пометка сохраняется после редактуры"
          : kind === "review"
            ? "Отложить строку для ручной проверки; пометка сохраняется после редактуры"
            : "Убрать пометку; существующая ручная правка останется защищённой";
      button.onclick = guard(async () => {
        if (studioState.dirty.has(row.id))
          throw Error("Сохраните или отмените правку перед пометкой");
        await request("mark", { id: row.id, revision: row.revision, kind });
        await refresh();
        await loadRows();
        toast(kind ? "Строка добавлена в папку проекта" : "Пометка снята");
      });
      actions.append(button);
    }
  }
});
studioLifecycle.register(
  "refresh",
  "processRefresh",
  async ({ initial, previousProject: oldProject }) => {
    updateProcessControls();
    if (
      studioState.project &&
      studioState.processPrefsProject !== studioState.project
    ) {
      const pid = studioState.project,
        pref = await request("process-settings?project=" + pid);
      if (pid !== studioState.project) return;
      studioState.processPrefs = pref;
      studioState.processPrefsProject = pid;
      studioState.processOffset = studioState.marksOffset = 0;
      $("historyMaxPhrases").value = pref.max_phrases;
      $("historyMaxSeconds").value = pref.max_seconds;
      $("processPageSize").value = pref.page_size;
    }
  },
);

// Folder review reuses the same model/language/context controls as ordinary jobs.
const folderControls = element("div");
folderControls.id = "folderReviewControls";
folderControls.hidden = true;
textPane.prepend(folderControls);
const folderSettingsOrigin = element("span");
settingsMenu.before(folderSettingsOrigin);
const folderReviewHint = element(
  "p",
  "Повторная редактура всех переведённых строк этой папки, включая уже отредактированные. Пометки сохраняются до ручного снятия. Модель, контекст, языки и лимиты задаются ниже.",
  "sub",
);
const folderInstruction = element("textarea");
folderInstruction.id = "folderReviewInstruction";
folderInstruction.maxLength = 4000;
folderInstruction.rows = 4;
folderInstruction.style.width = "100%";
const defaultFolderInstruction =
  "Это фразы, отмеченные человеком как неудачные или сомнительные. Тщательно сравни перевод с оригиналом и контекстом. Проверь смысл, естественность речи, род и обращения персонажей, пропуски и лишние детали. Сохрани служебные символы. Не меняй корректный перевод без конкретной причины; объясни найденные проблемы.";
const manualLabel = element("label");
const folderManual = element("input");
folderManual.id = "folderReviewManual";
folderManual.type = "checkbox";
manualLabel.append(
  folderManual,
  document.createTextNode(
    " Включить ручные правки: разрешить выбранной модели изменить их текст",
  ),
);
const folderRun = element("button", "Отправить заново на редактуру", "primary");
folderRun.id = "folderReviewRun";
const folderClear = element("button");
folderClear.id = "folderClearMarks";
folderControls.append(
  folderReviewHint,
  element("label", "Дополнительная инструкция редактору"),
  folderInstruction,
  manualLabel,
  element("div", undefined, "flex wrap"),
);
folderControls.lastChild.append(folderRun, folderClear);
let folderIdentity = "";
function updateFolderControls() {
  const visible = studioState.viewMode === "flags";
  folderControls.hidden = !visible;
  $("scope").hidden = visible;
  if (visible) {
    if (settingsMenu.parentNode !== folderControls)
      folderControls.insertBefore(settingsMenu, folderReviewHint);
    settingsMenu.open = true;
    const identity = studioState.project + ":" + studioState.markKind;
    if (identity !== folderIdentity) {
      folderInstruction.value =
        localStorage.getItem("review-instruction:" + identity) ||
        defaultFolderInstruction;
      folderManual.checked = false;
      folderIdentity = identity;
    }
    setText(
      folderClear,
      studioState.markKind === "bad"
        ? "Снять «Брак» со всех строк проекта"
        : "Снять «Ручная проверка» со всех строк проекта",
    );
  } else if (settingsMenu.parentNode === folderControls) {
    folderSettingsOrigin.after(settingsMenu);
  }
}
folderInstruction.oninput = () =>
  localStorage.setItem(
    "review-instruction:" + folderIdentity,
    folderInstruction.value,
  );
folderRun.onclick = guard(async () => {
  if (studioState.dirty.size) throw Error("Сохраните или отмените правки");
  newRunLimits.read();
  await request("job", {
    project: studioState.project,
    stage: "review",
    provider: $("provider").value,
    settings: { ...settings(), review_instruction: folderInstruction.value },
    mark_kind: studioState.markKind,
    allow_manual: folderManual.checked,
  });
  await refresh();
  toast("Редактура папки запущена; пометки останутся");
});
folderClear.onclick = guard(async () => {
  if (studioState.dirty.size) throw Error("Сохраните или отмените правки");
  const result = await request("clear-marks", {
    project: studioState.project,
    kind: studioState.markKind,
  });
  studioState.marksOffset = 0;
  await refresh();
  await loadRows();
  toast("Снято пометок: " + result.cleared);
});
studioLifecycle.register("navigate", "folderControls", updateFolderControls);
studioLifecycle.register(
  "refresh",
  "folderRefresh",
  async ({ initial, previousProject: oldProject }) => {
    updateFolderControls();
  },
);

const preservedFolder = element("button", "▣ Сохранено без перевода · 0");
preservedFolder.id = "preservedFolder";
projectTools.append(preservedFolder);
preservedFolder.onclick = guard(async () => {
  if (studioState.dirty.size) throw Error("Сохраните или отмените правки");
  studioState.marksOffset = 0;
  showView("preserved");
  await loadRows();
});
studioLifecycle.register(
  "refresh",
  "preservedRefresh",
  async ({ initial, previousProject: oldProject }) => {
    setText(
      preservedFolder,
      "▣ Сохранено без перевода · " +
        (studioState.snapshot?.counts?.preserved || 0),
    );
  },
);
studioLifecycle.register("render", "literalRender", (data, quiet = false) => {
  const map = new Map(data.rows.map((row) => [row.id, row]));
  for (const card of $("rows").querySelectorAll(".record")) {
    const row = map.get(+card.dataset.id);
    if (!row || card.dataset.literalControls) return;
    card.dataset.literalControls = "1";
    const actions = card.querySelector(".record-actions");
    if (row.language_note && row.flag === "review" && !row.preserve_kind) {
      const note = element("p", row.language_note, "sub");
      note.style.padding = "0 16px";
      card.querySelector(".record-top").after(note);
    }
    if (row.preserve_kind) {
      const reason = element(
        "p",
        "Почему оставлен оригинал: " + row.preserve_reason,
        "sub",
      );
      reason.style.padding = "0 16px";
      card.querySelector(".record-top").after(reason);
      const contextButton = [...actions.querySelectorAll("button")].find(
        (b) => b.textContent === "Контекст",
      );
      const area = card.querySelector("textarea");
      area.readOnly = true;
      area.oninput = null;
      area.setAttribute("aria-label", "Оригинал сохранён без перевода");
      const translate = element("button", "Перевести");
      translate.title =
        "Добавить в конец очереди, обработать следующей или перевести вручную";
      translate.onclick = guard(() => showPreservedTranslation(row));
      actions.replaceChildren(translate, contextButton);
    } else {
      const menu = element("details");
      menu.append(element("summary", "Оставить оригинал без перевода"));
      const reason = element("input");
      reason.placeholder = "Почему: другой язык, авторский приём…";
      reason.maxLength = 1000;
      reason.style.width = "100%";
      const preserve = element("button", "Другой язык / авторский приём");
      preserve.onclick = guard(async () => {
        if (studioState.dirty.has(row.id))
          throw Error("Сохраните или отмените правку");
        if (!reason.value.trim())
          throw Error("Укажите причину сохранения оригинала");
        await request("preserve", {
          id: row.id,
          revision: row.revision,
          kind: "foreign",
          reason: reason.value.trim(),
        });
        await refresh();
        await loadRows();
      });
      menu.append(reason, preserve);
      actions.append(menu);
    }
  }
});
// Error retries are separate FIFO jobs with their own immutable settings.
function budgetText(b) {
  return `Контекст: ${b.configured} токенов. Полный запрос: ≈${b.estimated_full}; короткий фрагмент без соседей: ≈${b.estimated_minimum}; рекомендуемый запас: ≈${b.recommended}. Резерв ответа: ${b.output_reserve}. Это оценка, без обращения к модели.`;
}
function modalError(e, where) {
  where.textContent = e.message || String(e);
  where.style.color = "var(--red)";
}
$("errorsButton").onclick = async () => {
  try {
    const [errors, retryPlan] = await Promise.all([
        request("errors?project=" + studioState.project),
        request("error-retry-plan", { project: studioState.project }),
      ]),
      content = $("infoContent");
    $("infoTitle").textContent = "Ошибки этого проекта";
    content.replaceChildren(
      element(
        "p",
        "Повторы добавляются отдельными очередями после текущей задачи. Модель и параметры каждой очереди сохраняются отдельно.",
        "sub",
      ),
    );
    const bulk = element(
      "button",
      "Повторить все с новыми параметрами",
      "primary",
    );
    bulk.id = "retryAllErrors";
    bulk.disabled = !Object.values(retryPlan).some((n) => n > 0);
    bulk.style.margin = "0 0 16px";
    bulk.onclick = () =>
      showErrorParameters({
        bulk: true,
        stage:
          Object.keys(retryPlan).find((s) => retryPlan[s] > 0) || "translate",
        plan: retryPlan,
      });
    content.append(bulk);
    for (const e of errors) {
      const card = element("div", undefined, "error");
      card.append(
        element(
          "div",
          `${e.at} · задача ${e.job || "импорт"} · строка ${e.record || "—"}`,
        ),
        element("div", e.source || ""),
        element("p", e.message),
      );
      const estimate = element("div", undefined, "sub");
      estimate.style.margin = "12px 0";
      try {
        const b = JSON.parse(e.budget_json || "{}");
        if (b.configured) estimate.textContent = budgetText(b);
      } catch {}
      const repeat = element("button", "Повторить");
      repeat.dataset.errorRepeat = e.id;
      repeat.onclick = () => showErrorRetry(e);
      if (e.record && e.stage) card.append(estimate, repeat);
      content.append(card);
    }
    if (!errors.length) content.append(element("p", "Открытых ошибок нет"));
    $("infoDialog").showModal();
    content.scrollTop = 0;
  } catch (e) {
    toast(e.message);
  }
};
async function showErrorRetry(error) {
  const content = $("infoContent");
  $("infoTitle").textContent = "Повторить строку";
  content.replaceChildren(element("p", error.source || ""));
  const note = element(
    "p",
    "Будет создана отдельная очередь. Она начнётся после основной и ранее добавленных очередей. На паузе цепочка ждёт продолжения.",
    "sub",
  );
  content.append(note);
  const message = element("p");
  message.setAttribute("role", "status");
  for (const [mode, label] of [
    ["original", "Добавить в очередь без изменений"],
    ["custom", "Добавить с новыми параметрами"],
    ["manual", "Перевести вручную"],
  ]) {
    const b = element("button", label);
    b.style.cssText = "display:block;width:100%;margin:12px 0";
    b.dataset.retryChoice = mode;
    b.onclick = async () => {
      try {
        if (studioState.dirty.size)
          throw Error("Сохраните или отмените правки");
        if (mode === "manual") {
          const row = await request("current?id=" + error.record);
          return showPreservedManual(row, "manual-error");
        }
        if (mode === "custom") return showErrorParameters(error);
        b.disabled = true;
        const result = await request("enqueue-error-retry", {
          project: studioState.project,
          record: error.record,
          stage: error.stage,
          mode: "original",
          settings: settings(),
        });
        $("infoDialog").close();
        await refresh();
        toast("Добавлена очередь повтора №" + result.job);
      } catch (e) {
        modalError(e, message);
      } finally {
        b.disabled = false;
      }
    };
    content.append(b);
  }
  content.append(message);
  if (!$("infoDialog").open) $("infoDialog").showModal();
  content.scrollTop = 0;
}
function showErrorParameters(error) {
  const content = $("infoContent");
  $("infoTitle").textContent = error.bulk
    ? "Повторить все ошибки с новыми параметрами"
    : "Новые параметры повтора";
  content.replaceChildren(
    element(
      "p",
      error.bulk
        ? "Все подходящие строки выбранного этапа во всём проекте. Ручные правки защищены. Каждая фраза добавится один раз."
        : error.source || "",
    ),
  );
  const form = element("div");
  form.style.cssText =
    "display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px";
  const picker = executionModel.cloneNode(true);
  picker.removeAttribute("id");
  picker.id = "retryExecutionModel";
  const modelLabel = element("label", "Модель этой очереди");
  modelLabel.style.gridColumn = "1 / -1";
  picker.style.cssText = "display:block;width:100%;margin-top:6px";
  modelLabel.append(picker);
  form.append(modelLabel);
  picker.value = executionModel.value;
  const fields = {},
    base = settings();
  for (const [key, label, min, max] of [
    ["context_before", "Фраз до", 0, 100],
    ["context_after", "Фраз после", 0, 100],
    ["context", "Контекст, токенов", 1024, 131072],
    ["max_output", "Лимит ответа, токенов", 100, 100000],
  ]) {
    const labelNode = element("label", label),
      input = element("input");
    input.type = "number";
    input.min = min;
    input.max = max;
    input.step = 1;
    input.value = base[key];
    input.style.cssText =
      "display:block;width:100%;box-sizing:border-box;margin-top:6px";
    input.dataset.retryParameter = key;
    fields[key] = input;
    labelNode.append(input);
    form.append(labelNode);
  }
  if (error.bulk) {
    const stageLabel = element("label", "Этап очереди"),
      stagePicker = element("select");
    stagePicker.id = "retryBulkStage";
    stageLabel.style.gridColumn = "1 / -1";
    stagePicker.style.cssText = "display:block;width:100%;margin-top:6px";
    for (const [stage, label] of Object.entries({
      translate: "Перевод",
      review: "Редактура",
      cloud: "Облачная проверка",
    })) {
      const option = element(
        "option",
        label + " · " + error.plan[stage] + " строк",
      );
      option.value = stage;
      option.disabled = !error.plan[stage];
      stagePicker.append(option);
    }
    stagePicker.value = error.stage;
    stagePicker.onchange = () => {
      error.stage = stagePicker.value;
    };
    stageLabel.append(stagePicker);
    form.prepend(stageLabel);
  }
  const estimate = element("div", undefined, "sub");
  estimate.id = "retryBudget";
  estimate.style.cssText = "margin:14px 0;line-height:1.7";
  estimate.setAttribute("aria-live", "polite");
  const message = element("p");
  message.setAttribute("role", "status");
  function read() {
    const values = {};
    for (const [key, input] of Object.entries(fields)) {
      if (!input.checkValidity() || !Number.isInteger(+input.value))
        throw Error("Проверьте параметры строки");
      values[key] = +input.value;
    }
    const selected = picker.value,
      s = { ...base, ...values };
    let provider = "local";
    if (selected === "cloud") provider = "cloud";
    else if (selected.startsWith("mcp:")) {
      provider = "mcp";
      s.mcp_session = selected.slice(4);
    } else s.model = selected.slice(6);
    return {
      provider,
      settings: s,
      overrides: {
        context: values.context,
        context_before: values.context_before,
        context_after: values.context_after,
      },
    };
  }
  let timer,
    sequence = 0;
  async function estimateNow() {
    const version = ++sequence;
    try {
      const input = read();
      estimate.textContent = "Пересчитываем оценку…";
      const b = await request(
        error.bulk ? "error-retry-budget" : "error-budget",
        {
          project: studioState.project,
          record: error.record,
          stage: error.stage,
          settings: input.settings,
        },
      );
      if (version === sequence && form.isConnected)
        estimate.textContent =
          (error.bulk
            ? `Оценка для ${b.records} строк, показан самый большой запрос. Полностью помещаются: ${b.fits_count} из ${b.records}. `
            : "") + budgetText(b);
    } catch (e) {
      if (version === sequence && form.isConnected)
        estimate.textContent = e.message;
    }
  }
  function changed() {
    ++sequence;
    clearTimeout(timer);
    timer = setTimeout(estimateNow, 250);
  }
  form.addEventListener("input", changed);
  form.addEventListener("change", changed);
  const send = element("button", "Добавить отдельную очередь", "primary");
  send.id = "enqueueCustomRetry";
  send.onclick = async () => {
    try {
      if (studioState.dirty.size) throw Error("Сохраните или отмените правки");
      const input = read();
      send.disabled = true;
      const result = await request("enqueue-error-retry", {
        project: studioState.project,
        record: error.record,
        stage: error.stage,
        mode: "custom",
        ...input,
      });
      $("infoDialog").close();
      await refresh();
      toast(
        "Добавлена очередь №" +
          result.job +
          " · " +
          result.records +
          " строк с новыми параметрами",
      );
    } catch (e) {
      modalError(e, message);
    } finally {
      send.disabled = false;
    }
  };
  const back = element("button", "← Выбор повтора");
  back.onclick = () =>
    error.bulk ? $("errorsButton").onclick() : showErrorRetry(error);
  const actions = element("div", undefined, "flex wrap");
  actions.append(send, back);
  const hint = element(
    "p",
    "Параметры применятся только к новой очереди. Основная задача и настройки проекта сохранятся.",
    "sub",
  );
  for (const node of [hint, estimate, actions, message])
    node.style.gridColumn = "1 / -1";
  form.append(hint, estimate, actions, message);
  content.append(form);
  content.scrollTop = 0;
  estimateNow();
}

async function showPreservedTranslation(row) {
  if (studioState.dirty.size) throw Error("Сохраните или отмените правки");
  const dialog = $("infoDialog"),
    content = $("infoContent");
  $("infoTitle").textContent = "Перевести фразу";
  content.replaceChildren(element("p", row.source));
  content.append(
    element(
      "p",
      "В активной задаче используются её модель и параметры. Если задачи нет, перевод начнётся с настройками справа. На паузе фраза останется в очереди до продолжения.",
      "sub",
    ),
  );
  for (const [mode, label] of [
    ["end", "Добавить в конец очереди"],
    ["front", "Проверить без очереди — обработать следующей"],
    ["manual", "Ручной перевод"],
  ]) {
    const button = element("button", label);
    button.dataset.preservedMode = mode;
    button.style.cssText = "display:block;margin:12px 0;width:100%";
    button.onclick = guard(async () => {
      if (mode === "manual") return showPreservedManual(row);
      const result = await request("translate-preserved", {
        project: studioState.project,
        id: row.id,
        revision: row.revision,
        mode,
        provider: $("provider").value,
        settings: settings(),
      });
      dialog.close();
      await refresh();
      await loadRows();
      toast(
        result.paused
          ? "Строка добавлена. Продолжите задачу после паузы"
          : mode === "front"
            ? "Фраза будет обработана после текущего запроса"
            : "Фраза добавлена в конец очереди",
      );
    });
    content.append(button);
  }
  content.append(
    element(
      "p",
      "Обработка без очереди не прерывает текущий запрос к модели: выбранная фраза пойдёт сразу после него.",
      "sub",
    ),
  );
  dialog.showModal();
  $("infoClose").focus({ preventScroll: true });
  content.scrollTop = 0;
}
async function showPreservedManual(row, saveAction = "translate-preserved") {
  await showRecordContext(row.id);
  $("infoTitle").textContent = "Ручной перевод · контекст";
  const selected = $("contextSelected"),
    card = selected.querySelector(".context-card"),
    area = element("textarea");
  area.id = "preservedManualText";
  area.value = row.text || row.source;
  area.rows = 5;
  area.style.cssText = "width:100%;box-sizing:border-box;margin-top:12px";
  area.setAttribute("aria-label", "Ручной перевод выбранной фразы");
  card.lastChild.replaceWith(area);
  card.append(
    element(
      "p",
      "После сохранения фраза перейдёт в обычный текст проекта. Ручной перевод защищён от перезаписи ИИ.",
      "sub",
    ),
  );
  // Expanding context must not rebuild an editor containing an unsaved draft.
  for (const b of $("infoContent").querySelectorAll("button"))
    if (b.textContent === "Показать больше контекста") b.remove();
  const save = element("button", "Сохранить перевод");
  save.id = "preservedManualSave";
  $("infoFooter").prepend(save);
  area.oninput = () => studioState.dirty.add(row.id);
  const message = element("p");
  message.setAttribute("role", "status");
  card.append(message);
  const cleanup = () => {
    studioState.dirty.delete(row.id);
    save.remove();
  };
  $("infoDialog").addEventListener("close", cleanup, { once: true });
  save.onclick = async () => {
    try {
      save.disabled = true;
      await request(saveAction, {
        project: studioState.project,
        id: row.id,
        revision: row.revision,
        mode: "manual",
        text: area.value,
      });
      $("infoDialog").close();
      await refresh();
      await loadRows();
      toast("Ручной перевод сохранён и защищён");
    } catch (e) {
      modalError(e, message);
    } finally {
      save.disabled = false;
    }
  };
  $("infoContent").scrollTop = Math.max(
    0,
    selected.offsetTop - $("infoContent").offsetTop - 12,
  );
  area.focus({ preventScroll: true });
}

// Two-line previews with one expansion control; editors never scroll internally.
const textPreviewStyle = element("style");
textPreviewStyle.textContent = `
.record .pair{align-items:start}
.record .original,.record .translation{min-width:0;overflow-wrap:anywhere}
.record .record-text-body{min-width:0;overflow:hidden;white-space:pre-wrap;overflow-wrap:anywhere}
.record .record-text-body.is-clipped{-webkit-mask-image:linear-gradient(to bottom,#000 0%,#000 45%,transparent 100%);mask-image:linear-gradient(to bottom,#000 0%,#000 45%,transparent 100%)}
.record .translation textarea{display:block;min-height:0!important;resize:none!important;overflow:hidden!important;white-space:pre-wrap;overflow-wrap:anywhere;scrollbar-width:none}
.record .translation textarea::-webkit-scrollbar{display:none}
.record .record-expand{display:block;margin:0 auto 10px;padding:4px 13px;background:transparent;border-color:transparent;color:var(--muted);font-size:12px}
.record .record-expand:hover{background:#263239;color:var(--ink)}
`;
document.head.append(textPreviewStyle);
const recordExpansion = new Map(),
  previewCards = new Map();
function fitRecordPreview(card) {
  const preview = previewCards.get(card);
  if (!preview || !card.isConnected || !card.offsetWidth) return;
  const { source, editor, target, button } = preview;
  const css = getComputedStyle(editor),
    sourceCss = getComputedStyle(source);
  const editorLine = parseFloat(css.lineHeight) || 23,
    sourceLine = parseFloat(sourceCss.lineHeight) || 23;
  const border =
    parseFloat(css.borderTopWidth) + parseFloat(css.borderBottomWidth);
  const padding = parseFloat(css.paddingTop) + parseFloat(css.paddingBottom);
  editor.style.minHeight = "0";
  editor.style.height = "0px";
  const fullEditorHeight = Math.max(
    editor.scrollHeight + border,
    editorLine + padding + border,
  );
  const editorLimit = editorLine * 2 + padding + border,
    sourceLimit = sourceLine * 2;
  const sourceOverflow = source.scrollHeight > sourceLimit + 1,
    targetOverflow = fullEditorHeight > editorLimit + 1,
    overflow = sourceOverflow || targetOverflow;
  let expanded = recordExpansion.get(+card.dataset.id) || false;
  if (!overflow) {
    expanded = false;
    recordExpansion.delete(+card.dataset.id);
  }
  source.style.maxHeight = expanded ? "none" : sourceLimit + "px";
  editor.style.height =
    (expanded ? fullEditorHeight : Math.min(fullEditorHeight, editorLimit)) +
    "px";
  source.classList.toggle("is-clipped", sourceOverflow && !expanded);
  target.classList.toggle("is-clipped", targetOverflow && !expanded);
  button.hidden = !overflow;
  setText(button, expanded ? "▴ Свернуть текст" : "▾ Раскрыть текст");
  button.setAttribute("aria-expanded", String(expanded));
  button.setAttribute(
    "aria-label",
    expanded ? "Свернуть оригинал и перевод" : "Раскрыть оригинал и перевод",
  );
}
const previewObserver = new ResizeObserver((entries) => {
  for (const { target } of entries) fitRecordPreview(target.closest(".record"));
});
function attachRecordPreviews() {
  for (const [card] of previewCards)
    if (!card.isConnected) {
      previewObserver.unobserve(card.querySelector(".pair"));
      previewCards.delete(card);
    }
  for (const card of $("rows").querySelectorAll(".record")) {
    if (!previewCards.has(card)) {
      const original = card.querySelector(".original"),
        editor = card.querySelector("textarea"),
        target = element("div", undefined, "record-text-body"),
        source = element("div", undefined, "record-text-body");
      source.textContent = original.textContent;
      original.replaceChildren(source);
      editor.before(target);
      target.append(editor);
      const button = element("button", undefined, "record-expand");
      button.type = "button";
      card.querySelector(".pair").after(button);
      previewCards.set(card, { source, editor, target, button });
      button.onclick = () => {
        const id = +card.dataset.id;
        recordExpansion.set(id, !recordExpansion.get(id));
        if (recordExpansion.size > 200)
          recordExpansion.delete(recordExpansion.keys().next().value);
        fitRecordPreview(card);
      };
      editor.addEventListener("focus", () => {
        if (!editor.readOnly) {
          recordExpansion.set(+card.dataset.id, true);
          fitRecordPreview(card);
        }
      });
      editor.addEventListener("input", () => fitRecordPreview(card));
      previewObserver.observe(card.querySelector(".pair"));
    }
    fitRecordPreview(card);
  }
}
studioLifecycle.register("render", "previewRender", (...args) => {
  attachRecordPreviews();
});

const chainPanel = element("section");
chainPanel.id = "jobChain";
projectTools.before(chainPanel);
let chainVersion = "";
studioLifecycle.register("refresh", "chainRefresh", async (context) => {
  const jobs = (studioState.snapshot?.jobs || [])
    .filter((j) =>
      ["running", "queued", "paused", "waiting", "held"].includes(j.state),
    )
    .sort((a, b) => a.id - b.id);
  const signature = JSON.stringify([
    studioState.project,
    jobs.map((j) => [j.id, j.state, j.done, j.total, j.settings]),
  ]);
  if (signature === chainVersion) return;
  chainVersion = signature;
  chainPanel.replaceChildren();
  chainPanel.hidden = !jobs.length;
  if (!jobs.length) return;
  chainPanel.append(element("h2", "Очереди обработки · " + jobs.length));
  jobs.forEach((j, i) => {
    const s = JSON.parse(j.settings || "{}"),
      card = element("div", undefined, "error"),
      title = element(
        "strong",
        `${i + 1}. ${s._retry_origin ? "Повтор ошибок" : "Основная очередь"} · №${j.id} · ${jobLabels[j.state] || j.state}`,
      ),
      description = element(
        "div",
        `${{ translate: "Перевод", review: "Редактура", cloud: "Проверка" }[j.stage]} · ${(j.provider === "cloud" ? s.cloud_model : j.provider === "mcp" ? s.mcp_model_hint : s.model) || "MCP"} · ${j.provider} · ${j.done}/${j.total} строк`,
        "sub",
      ),
      parameters = element(
        "div",
        `Контекст ${s.context || 8192} · ответ ${s.max_output || 1200} · соседей ${s.context_before ?? 12} до / ${s.context_after ?? 8} после · ${s.source_language || "English"} → ${s.target_language || "Russian"}`,
        "sub",
      );
    card.append(title, description, parameters);
    chainPanel.append(card);
  });
});

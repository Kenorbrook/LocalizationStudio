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
const markedVisit = { key: "", retained: new Set() };
function syncMarkedVisit() {
  const key =
    studioState.viewMode === "flags"
      ? `${studioState.project}/${studioState.markKind}`
      : "";
  if (key !== markedVisit.key) {
    markedVisit.key = key;
    markedVisit.retained.clear();
  }
}
studioLifecycle.register("navigate", "markedVisit", syncMarkedVisit);
studioLifecycle.register("refresh", "markedVisit", syncMarkedVisit);
studioLifecycle.register("recordSaved", "markedVisit", ({ before, after }) => {
  syncMarkedVisit();
  if (!markedVisit.key) return;
  if (
    humanVerified(after) &&
    !after.flag &&
    before.flag === studioState.markKind
  )
    markedVisit.retained.add(after.id);
  else if (!humanVerified(after)) markedVisit.retained.delete(after.id);
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
      [...markedVisit.retained],
    ]);
    const paths = {
      preserved: () =>
        `preserved?project=${studioState.project}&offset=${studioState.marksOffset}`,
      flags: () =>
        `marked?project=${studioState.project}&kind=${studioState.markKind}&offset=${studioState.marksOffset}&retained=${[...markedVisit.retained].join(",")}`,
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
    if (humanVerified(row)) continue;
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

const folderControls = element("div");
folderControls.id = "folderReviewControls";
folderControls.hidden = true;
textPane.prepend(folderControls);
const folderSettingsOrigin = element("span");
settingsMenu.before(folderSettingsOrigin);
const folderReviewHint = element(
  "p",
  "Повторная редактура всех переведённых строк этой папки, включая уже отредактированные. Пометки сохраняются после редактуры; снимаются вручную или кнопкой «Проверено мной». Модель, контекст, языки и лимиты задаются ниже.",
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
const folderOpen = element(
  "button",
  "Отправить на редактуру с новыми параметрами",
);
folderOpen.id = "folderReviewOpen";
const folderDialog = element("dialog");
folderDialog.id = "folderReviewDialog";
folderDialog.setAttribute("aria-labelledby", "folderReviewTitle");
const folderHeader = element("div", undefined, "updates-header");
const folderTitle = element("h2");
folderTitle.id = "folderReviewTitle";
const folderClose = element("button", "×");
folderClose.id = "folderReviewClose";
folderClose.setAttribute("aria-label", "Закрыть параметры редактуры");
folderHeader.append(folderTitle, folderClose);
const folderBody = element("div", undefined, "folder-review-body");
folderBody.append(...folderControls.childNodes);
folderBody.lastChild.append(folderRun);
folderDialog.append(folderHeader, folderBody);
document.body.append(folderDialog);
folderControls.append(folderOpen, folderClear);
let folderSettingsWasOpen = false;
function restoreFolderSettings() {
  if (settingsMenu.parentNode !== folderBody) return;
  folderSettingsOrigin.after(settingsMenu);
  settingsMenu.open = folderSettingsWasOpen;
  settingsMenu.hidden = studioState.viewMode === "flags";
}
folderDialog.addEventListener("close", restoreFolderSettings);
folderDialog.addEventListener("cancel", restoreFolderSettings);
folderClose.onclick = () => folderDialog.close();
folderOpen.onclick = () => {
  folderSettingsWasOpen = settingsMenu.open;
  folderBody.insertBefore(settingsMenu, folderReviewHint);
  settingsMenu.hidden = false;
  settingsMenu.open = true;
  setText(
    folderTitle,
    studioState.markKind === "bad"
      ? "Редактура папки «Брак»"
      : "Редактура папки «Ручная проверка»",
  );
  folderDialog.showModal();
  folderBody.scrollTop = 0;
  folderClose.focus({ preventScroll: true });
};
let folderIdentity = "";
function updateFolderControls() {
  const visible = studioState.viewMode === "flags";
  folderControls.hidden = !visible;
  $("scope").hidden = visible;
  if (!visible && folderDialog.open) {
    folderDialog.close();
    restoreFolderSettings();
  }
  settingsMenu.hidden = visible && !folderDialog.open;
  if (visible) {
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
  folderDialog.close();
  restoreFolderSettings();
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
    } else if (!humanVerified(row)) {
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

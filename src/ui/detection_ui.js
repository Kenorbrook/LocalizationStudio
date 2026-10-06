// Project-scoped offline inspection, loaded once per project, never on each poll.
const inspectButton = element("button", "Структура игры");
inspectButton.id = "inspectProject";
projectTools.append(inspectButton);
const detectionSummary = element(
  "p",
  "Определение движка ещё не выполнялось.",
  "sub",
);
detectionSummary.id = "detectionSummary";
summary.after(detectionSummary);
detectionSummary.hidden = true;
const detectionDialog = element("dialog");
detectionDialog.id = "detectionDialog";
detectionDialog.append(element("h1", "Структура игры и доступный текст"));
const detectionBody = element("div");
detectionBody.id = "detectionBody";
detectionBody.style.cssText =
  "max-height:58vh;overflow:auto;scrollbar-gutter:stable";
const detectionActions = element("div", undefined, "flex wrap");
detectionActions.style.marginTop = "15px";
const rescanButton = element("button", "Повторить анализ"),
  importDetectedButton = element(
    "button",
    "Импортировать выбранные",
    "primary",
  ),
  closeDetectionButton = element("button", "Закрыть");
importDetectedButton.id = "importDetected";
rescanButton.id = "rescanProject";
detectionActions.append(
  rescanButton,
  importDetectedButton,
  closeDetectionButton,
);
detectionDialog.append(detectionBody, detectionActions);
document.body.append(detectionDialog);
let detectionProject = 0,
  detectionReport = null,
  detectionPage = 0,
  detectionMode = "text",
  detectionSelected = new Set(),
  detectionBusy = false,
  detectionLoadedProject = -1;
function updateDetectionSummary(report) {
  detectionSummary.hidden = false;
  setText(
    detectionSummary,
    report
      ? `${report.engine.name}${report.engine.version ? " · " + report.engine.version : ""} · ${report.engine.confidence === "high" ? "подтверждены признаки" : report.engine.confidence === "tentative" ? "предварительное определение" : "нужна проверка"} · файлов для импорта: ${report.capabilities.importable_files}${report.partial ? " · анализ неполный" : ""}`
      : "Определение движка ещё не выполнялось. Нажмите «Структура игры».",
  );
}
function updateDetectionImport() {
  inspectButton.disabled = rescanButton.disabled = detectionBusy;
  importDetectedButton.disabled = detectionBusy || !detectionSelected.size;
  setText(
    importDetectedButton,
    "Импортировать выбранные" +
      (detectionSelected.size ? " · " + detectionSelected.size : ""),
  );
}
function renderDetection() {
  const report = detectionReport;
  detectionBody.replaceChildren();
  if (!report) return;
  detectionBody.append(
    element(
      "strong",
      report.engine.name +
        (report.engine.version ? " · " + report.engine.version : ""),
    ),
    element(
      "p",
      `Папка: ${report.root}\nПросмотрено файлов: ${report.scanned_files} · ${report.at} · ${report.elapsed_seconds} с`,
      "sub",
    ),
  );
  if (report.partial)
    detectionBody.append(
      element(
        "p",
        "Анализ неполный: часть файлов или содержимого не просмотрена.",
        "sub",
      ),
    );
  for (const [title, value] of [
    ["Где хранится текст", report.storage],
    ["Как внедрять перевод", report.replacement],
    ["Выбор языка", report.language_switch],
  ])
    detectionBody.append(element("h2", title), element("p", value));
  if (report.languages.length)
    detectionBody.append(
      element(
        "p",
        "Найдены папки переводов: " + report.languages.join(", "),
        "sub",
      ),
    );
  const evidence = element("details");
  evidence.append(element("summary", "Признаки движка и ограничения анализа"));
  for (const engine of report.engines) {
    evidence.append(
      element(
        "strong",
        engine.name +
          (engine.confidence === "high"
            ? " — подтверждённые признаки"
            : " — предварительно"),
      ),
    );
    for (const proof of engine.evidence)
      evidence.append(
        element("pre", proof.reason + "\n" + proof.paths.join("\n")),
      );
  }
  for (const message of [...report.warnings, ...report.errors])
    evidence.append(element("p", message, "sub"));
  detectionBody.append(evidence);
  const tabs = element("div", undefined, "flex wrap");
  tabs.style.marginTop = "18px";
  for (const [mode, label] of [
    ["text", "Текст и сценарии"],
    ["archives", "Архивы и бинарные ресурсы"],
  ]) {
    const button = element(
      "button",
      label +
        (mode === "text"
          ? " · " + report.candidates.length
          : " · " + report.archives.length),
      detectionMode === mode ? "primary" : "",
    );
    button.onclick = () => {
      detectionMode = mode;
      detectionPage = 0;
      renderDetection();
    };
    tabs.append(button);
  }
  detectionBody.append(tabs);
  const list = detectionMode === "text" ? report.candidates : report.archives;
  const pages = Math.max(1, Math.ceil(list.length / 50));
  detectionPage = Math.min(detectionPage, pages - 1);
  const pagination = () => {
    const controls = element("div", undefined, "flex pagination");
    const prev = element("button", "← Назад"),
      next = element("button", "Далее →");
    prev.disabled = !detectionPage;
    next.disabled = detectionPage >= pages - 1;
    prev.onclick = () => {
      detectionPage--;
      renderDetection();
    };
    next.onclick = () => {
      detectionPage++;
      renderDetection();
    };
    controls.append(
      prev,
      element("span", `Блок ${detectionPage + 1} / ${pages}`, "sub"),
      next,
    );
    return controls;
  };
  detectionBody.append(pagination());
  const roles = {
    script: "Сценарий",
    translation: "Слой перевода",
    corpus: "Корпус перевода",
    text: "Текст",
    subtitle: "Субтитры",
    resource: "Ресурс",
    candidate: "Кандидат",
  };
  for (const item of list.slice(detectionPage * 50, (detectionPage + 1) * 50)) {
    const card = element("div", undefined, "error");
    card.style.overflowWrap = "anywhere";
    const title = element("div", undefined, "flex");
    if (detectionMode === "text" && item.supported) {
      const checkbox = element("input");
      checkbox.type = "checkbox";
      checkbox.style.cssText = "width:auto;margin:0";
      checkbox.checked = detectionSelected.has(item.path);
      checkbox.onchange = () => {
        if (checkbox.checked) detectionSelected.add(item.path);
        else detectionSelected.delete(item.path);
        updateDetectionImport();
      };
      title.append(checkbox);
    }
    title.append(element("strong", item.path));
    card.append(
      title,
      element(
        "div",
        `${roles[item.role] || "Контейнер"} · ${item.format} · ${(item.size / 1024).toFixed(1)} КиБ${item.encoding ? " · " + item.encoding : ""}`,
        "sub",
      ),
    );
    card.append(
      element(
        "div",
        item.reason || "Извлечение этого контейнера пока не реализовано.",
      ),
    );
    if (item.samples?.length)
      card.append(element("pre", item.samples.join("\n…\n")));
    if (item.sample_truncated)
      card.append(
        element(
          "small",
          "Образец взят из начала файла, а не из всего содержимого.",
          "sub",
        ),
      );
    detectionBody.append(card);
  }
  if (!list.length)
    detectionBody.append(
      element("p", "В этом разделе файлов не найдено.", "sub"),
    );
  detectionBody.append(pagination());
  updateDetectionImport();
  detectionBody.scrollTop = 0;
}
async function runDetection() {
  detectionBusy = true;
  rescanButton.disabled = true;
  updateDetectionImport();
  detectionBody.replaceChildren(
    element("span", undefined, "spinner"),
    element("span", "Проверка файлов на диске…"),
  );
  try {
    detectionReport = await request("analyze-project", {
      project: detectionProject,
    });
    detectionSelected.clear();
    detectionPage = 0;
    if (studioState.project === detectionProject)
      updateDetectionSummary(detectionReport);
    renderDetection();
  } catch (error) {
    detectionBody.replaceChildren(element("p", error.message));
    throw error;
  } finally {
    detectionBusy = false;
    rescanButton.disabled = false;
    updateDetectionImport();
  }
}
inspectButton.onclick = guard(async () => {
  if (!studioState.project) throw Error("Сначала откройте проект");
  detectionProject = studioState.project;
  detectionSelected.clear();
  detectionPage = 0;
  detectionMode = "text";
  detectionDialog.showModal();
  const data = await request("project-analysis?project=" + detectionProject);
  detectionReport = data.report;
  if (detectionReport) renderDetection();
  else await runDetection();
});
rescanButton.onclick = guard(runDetection);
closeDetectionButton.onclick = () => detectionDialog.close();
importDetectedButton.onclick = guard(async () => {
  if (studioState.dirty.size)
    throw Error("Сохраните или отмените правки перед импортом");
  detectionBusy = true;
  updateDetectionImport();
  try {
    const result = await request("import-detected", {
      project: detectionProject,
      paths: [...detectionSelected],
    });
    detectionSelected.clear();
    renderDetection();
    await refresh();
    toast(
      "Добавлено строк: " +
        result.added +
        (result.errors.length ? " · ошибок: " + result.errors.length : ""),
    );
  } finally {
    detectionBusy = false;
    updateDetectionImport();
  }
});
let detectionFetch = 0;
studioLifecycle.register(
  "refresh",
  "detectionRefresh",
  async ({ initial, previousProject: oldProject }) => {
    if (initial || detectionLoadedProject !== studioState.project) {
      const pid = studioState.project,
        fetchId = ++detectionFetch;
      if (detectionLoadedProject !== pid) detectionSummary.hidden = true;
      detectionLoadedProject = pid;
      if (pid) {
        const data = await request("project-analysis?project=" + pid);
        if (studioState.project === pid && detectionFetch === fetchId)
          updateDetectionSummary(data.report);
      }
    }
  },
);

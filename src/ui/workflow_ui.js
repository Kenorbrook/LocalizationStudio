// Human context, local/API/MCP model selection, and bounded translation runs.
const continuationLimits = createRunLimitControls(
  "runLimits",
  "Параметры следующего продолжения",
  "Применятся после нажатия «Продолжить»: новый отсчёт с нуля. После достижения лимита очередь встанет на паузу; текущий запрос сначала завершится.",
);
const newRunLimits = createRunLimitControls(
  "newRunLimits",
  "Лимит нового запуска",
  "Применится к новой задаче после нажатия запуска перевода, редактуры или проверки. Лимит считается с начала запуска; ошибки тоже входят в число обработанных строк. При достижении лимита очередь сохраняется на паузе.",
);
settingsMenu.append(newRunLimits.section);
const limitMenu = continuationLimits.section;
const limitMode = continuationLimits.mode;
const limitValue = continuationLimits.value;
actionHint.after(limitMenu);
function readRunLimits(validate = true) {
  return continuationLimits.read(validate);
}
function updateLimitInput() {
  continuationLimits.update();
}
const mcpSessionInput = element("input");
mcpSessionInput.id = "mcp_session";
mcpSessionInput.hidden = true;
settingsMenu.append(mcpSessionInput);
const outputInput = $("max_output");
const outputLabel = outputInput.previousElementSibling;
setText(outputLabel, "Лимит ответа модели, токенов");
$("context").after(
  outputLabel,
  outputInput,
  element(
    "p",
    "Длинный текст разбивается на части по абзацам и предложениям. Готовые части сохраняются для продолжения.",
    "sub",
  ),
);
const executionModel = element("select");
executionModel.id = "executionModel";
const executionLabel = element("label", "Модель для нового запуска");
settingsMenu.querySelector("summary").after(executionLabel, executionModel);
const connectMcp = element("button", "Подключить MCP");
connectMcp.id = "connectMcp";
executionModel.after(connectMcp);
connectMcp.onclick = () => {
  $("mcpButton").onclick();
};
const modelHint = element("input");
modelHint.id = "mcp_model_hint";
modelHint.placeholder = "Необязательно: предпочтительная модель MCP";
settingsMenu.append(modelHint);
// The local picker remains the source of local model metadata, without a second visible choice.
$("model").hidden = true;
const localLabel = $("model").previousElementSibling;
if (localLabel?.tagName === "LABEL") localLabel.hidden = true;
$("provider").hidden = true;
const providerLabel = $("provider").previousElementSibling;
if (providerLabel?.tagName === "LABEL") providerLabel.hidden = true;
let knownConnections = [],
  executionVersion = "";
function updateExecutionChoices() {
  const selected =
    $("provider").value === "mcp"
      ? "mcp:" + mcpSessionInput.value
      : $("provider").value === "cloud"
        ? "cloud"
        : "local:" + $("model").value;
  const local = [...$("model").options].map((o) => [o.value, o.textContent]);
  const signature = JSON.stringify([
    local,
    knownConnections.map((c) => [c.session, c.client, c.model, c.sampling]),
    $("cloud_model").value,
    selected,
  ]);
  if (signature !== executionVersion) {
    const localGroup = element("optgroup");
    localGroup.label = "Локальные модели";
    for (const [id, label] of local) {
      const option = element("option", label);
      option.value = "local:" + id;
      localGroup.append(option);
    }
    const external = element("optgroup");
    external.label = "Облачный API и MCP";
    const api = element(
      "option",
      $("cloud_model").value
        ? "API · " + $("cloud_model").value
        : "Подключить облачную модель через API…",
    );
    api.value = "cloud";
    external.append(api);
    for (const c of knownConnections) {
      const option = element(
        "option",
        `MCP · ${c.client} · ${c.model || "модель выбирает клиент"}${c.sampling ? "" : " (только чтение и предложения)"}`,
      );
      option.value = "mcp:" + c.session;
      option.disabled = !c.sampling;
      external.append(option);
    }
    if (
      selected.startsWith("mcp:") &&
      !knownConnections.some((c) => "mcp:" + c.session === selected)
    ) {
      const option = element("option", "MCP отключён — подключите клиент");
      option.value = selected;
      option.disabled = true;
      external.append(option);
    }
    executionModel.replaceChildren(localGroup, external);
    executionModel.value = selected;
    executionVersion = signature;
  }
  connectMcp.hidden = knownConnections.some((c) => c.sampling);
  setText(
    connectMcp,
    knownConnections.length ? "Настроить MCP" : "Подключить MCP",
  );
  modelHint.hidden = $("provider").value !== "mcp";
  modelSort.hidden = modelSortLabel.hidden = $("provider").value !== "local";
  modelInfo.hidden = $("provider").value !== "local";
}
executionModel.onchange = () => {
  const value = executionModel.value;
  if (value === "cloud") {
    $("provider").value = "cloud";
    if (!$("cloud_model").value) {
      reviewMenu.open = cloudConnection.open = true;
      $("endpoint").focus();
    }
  } else if (value.startsWith("mcp:")) {
    $("provider").value = "mcp";
    mcpSessionInput.value = value.slice(4);
  } else {
    $("provider").value = "local";
    $("model").value = value.slice(6);
    updateModelInfo();
  }
  updateExecutionChoices();
  updateTaskActions();
};
const mcpProviderOption = element("option", "MCP-клиент");
mcpProviderOption.value = "mcp";
$("provider").append(mcpProviderOption);
studioLifecycle.register("settings", "originalSettings", () => {
  return {
    ...newRunLimits.read(false),
    execution_kind: $("provider").value,
    mcp_session: mcpSessionInput.value,
    mcp_model_hint: modelHint.value,
  };
});
const workflowStart = start;
start = function (...args) {
  newRunLimits.read();
  return workflowStart(...args);
};
const workflowLoadSettings = loadSettings;
loadSettings = function (p) {
  workflowLoadSettings(p);
  const s = JSON.parse(p.settings || "{}");
  newRunLimits.load(s);
  $("provider").value = s.execution_kind || "local";
  mcpSessionInput.value = s.mcp_session || "";
  modelHint.value = s.mcp_model_hint || "";
  updateExecutionChoices();
};
const inventoryApply = applyModelList;
applyModelList = function (...args) {
  inventoryApply(...args);
  updateExecutionChoices();
};
let limitsProject = 0;
let limitsJob = 0;
studioLifecycle.register(
  "refresh",
  "workflowRefresh",
  async ({ initial, previousProject: oldProject }) => {
    knownConnections = window.connectionSnapshot || [];
    updateExecutionChoices();
    if (initial || limitsProject !== project) {
      const s = JSON.parse(
        snapshot.projects.find((p) => p.id === project)?.settings || "{}",
      );
      newRunLimits.load(s);
      limitsProject = project;
    }
    if (initial || limitsJob !== job?.id) {
      continuationLimits.load(JSON.parse(job?.settings || "{}"));
      limitsJob = job?.id;
    }
    limitMenu.hidden = !["paused", "held"].includes(job?.state);
    const progress = describeRunProgress(job);
    setText(runLimitText, progress.text);
    runLimitProgress.hidden =
      !job || progress.fraction === null || !job.run_started;
    runLimitProgress.value = Math.min(1, Math.max(0, progress.fraction || 0));
    runLimitText.hidden = !job;
  },
);
// Only the body scrolls; title and close controls stay visible in every info dialog.
const infoDialog = $("infoDialog"),
  infoHeader = element("div", undefined, "flex"),
  infoClose = element("button", "×");
infoHeader.id = "infoHeader";
infoHeader.style.cssText =
  "flex-shrink:0;justify-content:space-between;gap:16px";
infoClose.id = "infoClose";
infoClose.setAttribute("aria-label", "Закрыть окно");
infoClose.title = "Закрыть";
infoClose.onclick = () => infoDialog.close();
infoHeader.append($("infoTitle"), infoClose);
infoDialog.prepend(infoHeader);
$("infoTitle").style.margin = 0;
const infoFooter = element("div");
infoFooter.id = "infoFooter";
infoFooter.style.cssText =
  "flex-shrink:0;padding-top:14px;border-top:1px solid var(--line)";
infoFooter.append(infoDialog.querySelector("[data-close]"));
infoDialog.append(infoFooter);
const infoStyle = element("style");
infoStyle.textContent =
  "#infoDialog[open]{display:flex;flex-direction:column;overflow:hidden;max-height:85vh}#infoContent{min-height:0;overflow:auto;scrollbar-gutter:stable;overflow-anchor:none;margin:16px 0}#infoClose{font-size:24px;line-height:1;padding:5px 11px}#infoContent .context-card p{color:var(--ink);white-space:pre-wrap}#infoContent .context-card div{white-space:pre-wrap}";
document.head.append(infoStyle);
let contextRequest = 0;
infoDialog.addEventListener("close", () => {
  contextRequest++;
});
async function showRecordContext(id, radius = 10) {
  id = Number(id);
  const generation = ++contextRequest;
  const data = await request(`context?id=${id}&radius=${radius}`);
  if (generation !== contextRequest) return;
  $("infoTitle").textContent = "Контекст строки";
  const content = $("infoContent");
  content.replaceChildren();
  const loc = data.location;
  let location = `Файл: ${loc.file}`;
  if (loc.line) location += " · строка " + loc.line;
  if (loc.row) location += " · запись " + loc.row;
  if (loc.source_file)
    location += "\nИсходник: " + loc.source_file + ":" + loc.source_line;
  if (loc.json_path)
    location += "\nПуть в JSON: " + JSON.stringify(loc.json_path);
  location +=
    "\nСцена / блок: " +
    (loc.scene || "не указан") +
    "\nГоворящий: " +
    (loc.speaker || "не установлен");
  const details = element("details");
  details.style.marginTop = 0;
  details.append(
    element("summary", "Где находится фраза и кто её произносит"),
    element("pre", location),
  );
  const meta = data.metadata.speaker_metadata;
  if (meta.profile && Object.keys(meta.profile).length)
    details.append(
      element(
        "pre",
        "Правила голоса: " + JSON.stringify(meta.profile, null, 2),
      ),
    );
  content.append(details);
  content.append(
    element(
      "p",
      "Порядок: предыдущие строки → выбранная фраза → последующие строки. Это порядок записей того же файла и блока, не обязательно порядок прохождения игры. Последующие фразы тоже могут пояснять смысл; для меню это соседние пункты.",
      "sub",
    ),
  );
  const ordered = [...data.rows].sort((a, b) => a.position - b.position),
    target = ordered.find((r) => r.id === id);
  if (!target) throw Error("Выбранная строка не найдена в контексте");
  function section(title, rows, empty) {
    const box = element("section");
    box.append(element("h2", title));
    if (!rows.length) box.append(element("p", empty, "sub"));
    for (const r of rows) {
      const card = element(
        "div",
        undefined,
        "error context-card" + (r.id === id ? " current" : ""),
      );
      card.dataset.contextId = r.id;
      card.style.border = r.id === id ? "1px solid var(--accent)" : "";
      card.append(
        element(
          "strong",
          `${r.id === id ? "▶ Выбранная фраза · " : ""}#${r.position + 1} · ${r.speaker || "Говорящий не установлен"}`,
        ),
        element("p", r.source),
        element("div", r.text || "Пока без перевода"),
      );
      box.append(card);
    }
    content.append(box);
    return box;
  }
  const before = ordered.filter((r) => r.position < target.position),
    after = ordered.filter((r) => r.position > target.position);
  section(
    "До выбранной фразы · " + before.length,
    before,
    data.before_count
      ? "В пределах показанного диапазона предыдущих строк нет. Более ранние записи этого блока находятся дальше."
      : "В этом блоке перед выбранной фразой ничего не предшествовало.",
  );
  const selected = section("Выбранная фраза", [target], "");
  selected.id = "contextSelected";
  section(
    "После выбранной фразы · " + after.length,
    after,
    data.after_count
      ? "В пределах показанного диапазона следующих строк нет. Более поздние записи этого блока находятся дальше."
      : "В этом блоке после выбранной фразы следующих строк нет.",
  );
  if (radius < 30) {
    const more = element("button", "Показать больше контекста");
    more.onclick = guard(() => showRecordContext(id, 30));
    content.append(more);
  }
  if (!infoDialog.open) infoDialog.showModal();
  infoClose.focus({ preventScroll: true });
  infoDialog.scrollTop = 0;
  content.scrollTop = 0;
  const reveal = () => {
    if (generation !== contextRequest || !infoDialog.open) return;
    const offset =
      selected.getBoundingClientRect().top -
      content.getBoundingClientRect().top;
    content.scrollTop = Math.max(0, content.scrollTop + offset - 12);
    if (!before.length) content.scrollTop = 0;
  };
  reveal();
  requestAnimationFrame(reveal);
}
updateExecutionChoices();

// Source neighbors are independent of the model's total token budget.
const neighborControls = element("div");
neighborControls.id = "neighborControls";
const neighborFields = element("div", undefined, "flex");
for (const [id, label, value] of [
  ["context_before", "Фраз до текущей", 12],
  ["context_after", "Фраз после текущей", 8],
]) {
  const box = element("div", undefined, "grow");
  const input = element("input");
  input.id = id;
  input.type = "number";
  input.min = 0;
  input.max = 100;
  input.step = 1;
  input.value = value;
  box.append(element("label", label), input);
  neighborFields.append(box);
}
neighborControls.append(
  neighborFields,
  element(
    "p",
    "Соседние фразы — в оригинале, в пределах того же файла и сцены. 0 отключает соответствующую сторону. Длинные соседние фразы сокращаются до 300 символов; при нехватке токенов дальние убираются. Число фраз применяется к новому запуску и при продолжении задачи после паузы.",
    "sub",
  ),
);
$("context").after(neighborControls);
function readNeighborContext() {
  const result = {};
  for (const key of ["context_before", "context_after"]) {
    const value = +$(key).value;
    if (!Number.isInteger(value) || value < 0 || value > 100)
      throw Error("Укажите целое число соседних фраз от 0 до 100");
    result[key] = value;
  }
  return result;
}
studioLifecycle.register("settings", "neighborOriginalSettings", () => {
  return { ...readNeighborContext() };
});
const neighborLoadSettings = loadSettings;
loadSettings = function (p) {
  neighborLoadSettings(p);
  const saved = JSON.parse(p.settings || "{}");
  $("context_before").value = saved.context_before ?? 12;
  $("context_after").value = saved.context_after ?? 8;
};

const foreignPolicy = element("label");
const autoForeign = element("input");
autoForeign.id = "auto_foreign";
autoForeign.type = "checkbox";
autoForeign.checked = true;
foreignPolicy.append(
  autoForeign,
  document.createTextNode(" Сохранять реплики на другом языке"),
);
neighborControls.after(
  foreignPolicy,
  element(
    "p",
    "Язык определяется локально перед переводом. Уверенные случаи сохраняются в оригинале с причиной; сомнительные получают пометку «Ручная проверка». Имена и отдельные слова автоматически не исключаются.",
    "sub",
  ),
);
studioLifecycle.register("settings", "languageSettings", () => {
  return { auto_foreign: autoForeign.checked };
});
const languageLoad = loadSettings;
loadSettings = function (p) {
  languageLoad(p);
  autoForeign.checked = JSON.parse(p.settings || "{}").auto_foreign !== false;
};

// Keep the live job visually separate from controls for later work.
// Move existing nodes once so polling and editor state retain their identity.
const taskCard = element("section");
taskCard.id = "taskCard";
taskCard.setAttribute("aria-label", "Текущая задача");
const taskHeading = $("jobState").parentElement;
taskHeading.classList.add("task-heading");
const runLimitText = element("p", undefined, "sub");
runLimitText.id = "runLimitText";
const runLimitProgress = element("progress");
runLimitProgress.id = "runLimitProgress";
runLimitProgress.max = 1;
runLimitProgress.setAttribute("aria-label", "Прогресс лимита этого запуска");
runLimitProgress.style.width = "100%";
taskCard.append(
  taskHeading,
  $("progress"),
  $("progressText"),
  runLimitText,
  runLimitProgress,
  activeSettings,
  $("live"),
  mainAction,
  actionHint,
  limitMenu,
  taskMenu,
  $("logDetails"),
  originalControls,
);
const setupCard = element("section");
setupCard.id = "setupCard";
setupCard.setAttribute("aria-label", "Настройки и проверка");
const setupHeading = element("h2", "Настройки и проверка");
setupHeading.className = "sidebar-heading";
setupCard.append(setupHeading);
for (const node of [...right.childNodes]) setupCard.append(node);
right.append(taskCard, setupCard);
const sidebarStyle = element("style");
sidebarStyle.textContent = `
.right{padding:16px 12px;background:#11191e;scrollbar-gutter:stable}
#taskCard,#setupCard{border:1px solid var(--line);border-radius:12px;padding:16px 14px;min-width:0}
#taskCard{background:#1a252b;border-top:3px solid var(--accent)}
#taskCard .task-heading{gap:10px;margin-bottom:8px}
#taskCard .task-heading strong{font-size:14px}
#taskCard #progress{display:block;width:100%;margin:14px 0 9px}
#taskCard #progressText{font-variant-numeric:tabular-nums}
#taskCard #activeSettings{margin:8px 0 14px;overflow-wrap:anywhere}
#taskCard #live{margin:0 0 16px;background:#111b20;min-height:170px;max-height:300px;overflow:auto}
#taskCard #mainAction{margin:0;width:100%}
#taskCard #actionHint{margin:8px 0 14px}
#taskCard>details{margin:0;padding:12px 0;border-top:1px solid #34434b}
#taskCard>details:last-of-type{padding-bottom:0}
#taskCard>details>summary{line-height:1.5}
#setupCard{margin-top:20px;background:#151e23}
#setupCard .sidebar-heading{margin:0 0 10px;color:var(--muted);font-size:11px;letter-spacing:1px}
#setupCard>details{margin:0;padding:13px 0;border-top:1px solid var(--line)}
#setupCard>.sidebar-heading+details{border-top:0}
#setupCard>details>summary{line-height:1.5}
#setupCard>.sub{margin:14px 0 0;padding-top:14px;border-top:1px solid var(--line)}
`;
document.head.append(sidebarStyle);

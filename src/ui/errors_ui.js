const errorsPane = element("section");
errorsPane.id = "errorsPane";
errorsPane.hidden = true;
const errorList = element("div");
errorList.id = "errorList";
const errorPagination = element("div", undefined, "pagination");
const errorPrev = element("button", "← Предыдущий блок"),
  errorNext = element("button", "Следующий блок →"),
  errorPageInfo = element("span", undefined, "sub");
errorPrev.id = "errorsPrev";
errorNext.id = "errorsNext";
errorPageInfo.id = "errorsPageInfo";
errorPagination.append(errorPrev, errorPageInfo, errorNext);
errorsPane.append(errorPagination, errorList);
center.append(errorsPane);
let errorOffset = 0,
  errorProject = 0,
  errorListSignature = "";
function updateErrorsPane() {
  errorsPane.hidden = studioState.viewMode !== "errors";
  if (errorProject !== studioState.project) {
    errorProject = studioState.project;
    errorOffset = 0;
    errorListSignature = "";
  }
}
$("errorsButton").onclick = guard(async () => {
  if (studioState.dirty.size) throw Error("Сохраните или отмените правки");
  $("infoDialog").close();
  showView("errors");
  await loadErrors();
});
errorPrev.onclick = guard(async () => {
  errorOffset = Math.max(0, errorOffset - 100);
  await loadErrors();
  center.scrollTop = 0;
});
errorNext.onclick = guard(async () => {
  errorOffset += 100;
  await loadErrors();
  center.scrollTop = 0;
});
studioLifecycle.register("navigate", "errorsPane", updateErrorsPane);
studioLifecycle.register("refresh", "errorsPage", async () => {
  updateErrorsPane();
  await loadErrors();
});
// Error retries are separate FIFO jobs with their own immutable settings.
function budgetText(b) {
  return `Контекст: ${b.configured} токенов. Полный запрос: ≈${b.estimated_full}; короткий фрагмент без соседей: ≈${b.estimated_minimum}; рекомендуемый запас: ≈${b.recommended}. Резерв ответа: ${b.output_reserve}. Это оценка, без обращения к модели.`;
}
function modalError(e, where) {
  where.textContent = e.message || String(e);
  where.style.color = "var(--red)";
}
async function loadErrors() {
  if (studioState.viewMode !== "errors") return;
  const identity = studioState.project + ":" + errorOffset;
  try {
    const [errors, retryPlan] = await Promise.all([
        request(
          "errors?project=" + studioState.project + "&offset=" + errorOffset,
        ),
        request("error-retry-plan", { project: studioState.project }),
      ]),
      content = errorList;
    if (
      studioState.viewMode !== "errors" ||
      identity !== studioState.project + ":" + errorOffset
    )
      return;
    errorPrev.disabled = !errorOffset;
    errorNext.disabled =
      errorOffset + errors.length >= studioState.snapshot.errors;
    setText(
      errorPageInfo,
      errors.length
        ? `${errorOffset + 1}–${errorOffset + errors.length} из ${studioState.snapshot.errors || errors.length}`
        : "Открытых ошибок нет",
    );
    const signature = JSON.stringify([identity, errors, retryPlan]);
    if (signature === errorListSignature) return;
    errorListSignature = signature;
    const scroll = center.scrollTop;
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
    center.scrollTop = scroll;
  } catch (e) {
    toast(e.message);
  }
}
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
  back.onclick = () => {
    if (error.bulk) $("infoDialog").close();
    else showErrorRetry(error);
  };
  if (error.bulk) back.textContent = "← К списку ошибок";
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
  if (!$("infoDialog").open) $("infoDialog").showModal();
}

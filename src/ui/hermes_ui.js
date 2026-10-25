"use strict";
const hermesUi = (() => {
  let state = null;
  let project = 0;
  let editing = false;
  const plugins = element("details", undefined, "ux-advanced");
  plugins.id = "applicationPlugins";
  plugins.append(element("summary", "Плагины"));
  const panel = element("section", undefined, "app-about");
  panel.id = "hermesPlugin";
  const heading = element("div", undefined, "app-about-heading");
  heading.append(
    element("h3", "Hermes Agent"),
    workspaceUi.help(
      "Необязательный локальный агент для исследования игровых файлов. Среда скачивается отдельно с зависимостями и не входит в ZIP приложения. Использует установленную модель Ollama. Изменения переводов через агента не разрешены.",
      "О плагине Hermes",
    ),
  );
  const enabled = element("input");
  enabled.id = "hermesEnabled";
  enabled.type = "checkbox";
  const enableLabel = element("label");
  enableLabel.append(enabled, document.createTextNode(" Включить плагин"));
  const controls = element("div");
  const model = element("input");
  model.id = "hermesModel";
  model.placeholder = "Имя установленной модели Ollama";
  model.setAttribute("aria-label", "Модель Hermes");
  model.setAttribute("list", "hermesModels");
  const modelList = element("datalist");
  modelList.id = "hermesModels";
  const context = element("input");
  context.id = "hermesContext";
  context.type = "number";
  context.min = 64000;
  context.max = 131072;
  context.value = 65536;
  const contextLabel = element(
    "label",
    "Контекст Hermes, минимум 64 000 токенов",
  );
  contextLabel.append(
    context,
    workspaceUi.help(
      "Актуальный Hermes требует минимум 64 000 токенов и модель с вызовом инструментов. Большой контекст требует больше памяти; он задаётся отдельно от параметров переводчика. Проверка подключения не запускает перевод.",
      "Контекст Hermes",
    ),
  );
  const buttons = element("div", undefined, "flex wrap");
  function button(id, title, parent, action) {
    const result = element("button", title);
    result.id = id;
    result.onclick = guard(action);
    parent.append(result);
    return result;
  }
  const save = button("saveHermes", "Сохранить", buttons, async () => {
    state = await window.pywebview.api.configure_hermes({
      enabled: enabled.checked,
      model: model.value.trim(),
      context: +context.value,
    });
    editing = false;
    render(state, true);
  });
  button("cancelHermesSettings", "Отменить", buttons, () => {
    editing = false;
    if (state) render(state, true);
  });
  const install = button(
    "installHermes",
    "Скачать и установить",
    buttons,
    async () => render(await window.pywebview.api.install_hermes()),
  );
  const check = button(
    "checkHermes",
    "Проверить подключение",
    buttons,
    async () => render(await window.pywebview.api.check_hermes()),
  );
  const stop = button("stopHermes", "Остановить", buttons, async () =>
    render(await window.pywebview.api.cancel_hermes()),
  );
  const status = element("p", "Не подключён", "sub");
  status.id = "hermesStatus";
  status.setAttribute("role", "status");
  const logDetails = element("details");
  logDetails.append(element("summary", "Журнал плагина"));
  const log = element("pre");
  log.id = "hermesLog";
  log.style.cssText =
    "white-space:pre-wrap;overflow-wrap:anywhere;max-height:220px;overflow:auto";
  logDetails.append(log);
  controls.append(model, modelList, contextLabel, buttons);
  panel.append(heading, enableLabel, controls, status, logDetails);
  plugins.append(panel);
  updatesBody.insertBefore(plugins, appAbout);
  for (const input of [enabled, model, context])
    input.addEventListener("input", () => {
      editing = true;
      controls.hidden = !enabled.checked;
    });
  updatesDialog.addEventListener("close", () => {
    editing = false;
    if (state) render(state, true);
  });

  const dialog = element("dialog");
  dialog.id = "hermesProjectDialog";
  dialog.style.cssText = "width:min(800px,90vw);max-height:85vh;overflow:auto";
  const title = element("h2", "ИИ-помощник проекта");
  const close = button("closeHermesProject", "Закрыть", dialog, () =>
    dialog.close(),
  );
  const taskButtons = element("div", undefined, "flex wrap");
  const research = button(
    "hermesResearch",
    "Исследовать игровые файлы",
    taskButtons,
    async () =>
      render(await window.pywebview.api.run_hermes(project, "research")),
  );
  const errors = button(
    "hermesExplainErrors",
    "Разобрать ошибки с Hermes",
    taskButtons,
    async () =>
      render(await window.pywebview.api.run_hermes(project, "errors")),
  );
  const stopTask = button(
    "stopHermesTask",
    "Остановить Hermes",
    taskButtons,
    async () => render(await window.pywebview.api.cancel_hermes()),
  );
  button("hermesConnectionSettings", "Подключение Hermes", taskButtons, () => {
    dialog.close();
    openAppSettings();
    plugins.open = true;
    plugins.scrollIntoView({ block: "start" });
  });
  const projectStatus = element("p", undefined, "sub");
  projectStatus.id = "hermesProjectStatus";
  const report = element("pre");
  report.id = "hermesReport";
  report.style.cssText =
    "white-space:pre-wrap;overflow-wrap:anywhere;font:inherit";
  const recovery = element("section", undefined, "app-about");
  const recoveryHeading = element("div", undefined, "app-about-heading");
  recoveryHeading.append(
    element("h3", "Автоматическое восстановление"),
    workspaceUi.help(
      "Встроенная функция Studio, Hermes для неё не нужен. После завершения локального перевода повторяет только ошибки памяти и лимитов: максимум два дополнительных прохода, до 200 строк на задание. Уменьшает контекст при нехватке памяти или меняет резерв ответа. Пауза, ручные правки, сохранённые оригиналы и другие проекты защищены.",
      "Об автоматическом восстановлении",
    ),
  );
  const automatic = element("input");
  automatic.type = "checkbox";
  automatic.id = "automaticErrorRetry";
  const autoLabel = element("label");
  autoLabel.append(
    automatic,
    document.createTextNode(
      " Повторять подходящие ошибки этого проекта автоматически",
    ),
  );
  automatic.onchange = guard(async () =>
    render(
      await window.pywebview.api.set_automatic_retry(
        project,
        automatic.checked,
      ),
    ),
  );
  const plan = element("pre");
  plan.id = "smartRetryPlan";
  plan.style.cssText = "white-space:pre-wrap;font:inherit";
  const retry = button(
    "startSmartRetry",
    "Запустить подобранные повторы",
    recovery,
    async () => {
      render(await window.pywebview.api.start_smart_retry(project));
      await refresh();
    },
  );
  recovery.prepend(recoveryHeading, autoLabel, plan);
  dialog.prepend(title, taskButtons, projectStatus, report, recovery);
  document.body.append(dialog);
  const entry = element("button", "ИИ-помощник");
  entry.id = "projectAssistant";
  $("workspaceNavigation").append(entry);
  entry.onclick = guard(async () => {
    project = studioState.project;
    if (!project) throw Error("Сначала откройте проект");
    title.textContent =
      "ИИ-помощник · " +
      (studioState.snapshot?.projects.find((p) => p.id === project)?.name ||
        "проект");
    dialog.showModal();
    close.focus({ preventScroll: true });
    await refreshPlugin(true);
  });
  function render(value, fill = false) {
    state = value;
    if (
      fill ||
      (!editing && ![enabled, model, context].includes(document.activeElement))
    ) {
      enabled.checked = value.preferences.enabled;
      model.value = value.preferences.model;
      context.value = value.preferences.context;
    }
    controls.hidden = !enabled.checked;
    for (const input of [enabled, model, context]) input.disabled = value.busy;
    save.disabled = value.busy;
    install.disabled = value.busy || value.installed;
    check.disabled = value.busy || !value.installed;
    stop.hidden = !value.busy;
    stopTask.hidden = !value.busy;
    retry.disabled = value.busy || !value.retry_plan?.rows?.length;
    setText(
      status,
      (value.installed ? "Установлен · " : "Не установлен · ") + value.message,
    );
    setText(projectStatus, value.message);
    setText(log, value.log || "Журнал пуст");
    setText(report, value.report || "");
    research.disabled = errors.disabled =
      value.busy || !value.installed || !value.preferences.enabled;
    if (value.project === project && value.retry_plan) {
      automatic.checked = !!value.automatic_retry;
      const rows = value.retry_plan.rows || [];
      retry.disabled = value.busy || !rows.length;
      setText(
        plan,
        rows.length
          ? rows
              .map(
                (row) =>
                  `#${row.record}: ${row.reason}\nКонтекст ${row.parameters.context}, ответ ${row.parameters.max_output}`,
              )
              .join("\n\n")
          : "Нет подходящих ошибок завершённого локального перевода",
      );
    }
  }
  async function refreshPlugin(fill = false) {
    if (!window.pywebview?.api?.get_hermes_state) return;
    render(
      await window.pywebview.api.get_hermes_state(dialog.open ? project : 0),
      fill,
    );
  }
  appSettings.addEventListener("click", () => {
    refreshPlugin().catch((error) => toast(error.message));
    request("models")
      .then((data) =>
        modelList.replaceChildren(
          ...(data.models || []).map((name) => {
            const option = element("option");
            option.value = name;
            return option;
          }),
        ),
      )
      .catch(() => {});
  });
  setInterval(() => {
    if (dialog.open || updatesDialog.open)
      refreshPlugin().catch((error) => setText(status, error.message));
  }, 2500);
  return { refresh: refreshPlugin };
})();

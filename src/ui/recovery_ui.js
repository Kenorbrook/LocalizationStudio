"use strict";
const recoveryUi = (() => {
  let state = null;
  let sequence = 0;
  let pending = false;
  const section = element("section", undefined, "ux-settings-group");
  section.append(element("h3", "Автоповтор ошибок"));
  const checkboxes = new Map();
  for (const [key, title] of [
    ["memory", "Не хватило памяти"],
    ["context", "Фраза не поместилась в контекст"],
    ["output", "Ответ модели обрезан"],
  ]) {
    const label = element("label", undefined, "update-option");
    const input = element("input");
    input.type = "checkbox";
    input.id = "retryCategory-" + key;
    label.append(input, document.createTextNode(title));
    section.append(label);
    checkboxes.set(key, input);
    input.onchange = async () => {
      const project = studioState.project;
      const kinds = [...checkboxes]
        .filter(([, box]) => box.checked)
        .map(([value]) => value);
      pending = true;
      render();
      try {
        const reply = await window.pywebview.api.set_retry_categories(
          project,
          kinds,
        );
        if (project === studioState.project) state = reply;
      } catch (error) {
        toast(error.message);
      } finally {
        pending = false;
        render();
        await refresh();
      }
    };
  }
  section.append(
    element(
      "p",
      "До двух дополнительных проходов после завершения локального перевода.",
      "sub",
    ),
  );
  $("settingsPanel-translation").append(section);

  const details = element("details");
  details.id = "errorRecoveryDetails";
  const plan = element("pre", undefined, "plugin-task-report");
  plan.id = "smartRetryPlan";
  const start = element("button", "Повторить с другими параметрами");
  start.id = "startSmartRetry";
  const blocked = () => {
    if (state?.busy) return "Дождитесь завершения текущей задачи";
    return "Нет подходящих ошибок завершённого локального перевода";
  };
  details.append(
    element("summary", "Повтор с другими параметрами"),
    plan,
    workspaceUi.disabledReason(start, blocked),
  );
  errorsPane.prepend(details);
  start.onclick = guard(async () => {
    await window.pywebview.api.start_smart_retry(studioState.project);
    await refresh();
    await window.refresh();
  });
  function render() {
    for (const [key, input] of checkboxes) {
      if (!pending)
        input.checked = state?.retry_categories?.includes(key) || false;
      input.disabled = pending || !studioState.project;
    }
    const rows = state?.retry_plan?.rows || [];
    start.disabled = !rows.length || state?.busy;
    plan.hidden = !rows.length;
    setText(
      plan,
      rows
        .map(
          (row) =>
            `#${row.record}: ${row.reason} · контекст ${row.parameters.context}, ответ ${row.parameters.max_output} токенов`,
        )
        .join("\n"),
    );
  }
  async function refresh() {
    if (!window.pywebview?.api?.get_recovery_state) return;
    const own = ++sequence;
    const project = studioState.project;
    const reply = await window.pywebview.api.get_recovery_state(project);
    if (own !== sequence || project !== studioState.project || pending) return;
    state = reply;
    render();
  }
  studioLifecycle.register("refresh", "coreRecovery", refresh);
  studioLifecycle.register("navigate", "coreRecovery", () =>
    refresh().catch((e) => toast(e.message)),
  );
  return { refresh };
})();

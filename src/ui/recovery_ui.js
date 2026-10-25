"use strict";
const recoveryUi = (() => {
  let state = null,
    sequence = 0,
    recoveryPending = false;
  const recoveryViews = [];
  function button(id, text, parent, action, className) {
    const node = element("button", text, className);
    node.id = id;
    node.onclick = guard(action);
    parent.append(node);
    return node;
  }
  function modal(id, titleText) {
    const dialog = element("dialog", undefined, "plugin-settings-dialog");
    dialog.id = id;
    const header = element("div", undefined, "plugin-settings-header");
    const title = element("h2", titleText);
    title.id = id + "Title";
    dialog.setAttribute("aria-labelledby", title.id);
    const close = button(id + "Close", "×", header, () => dialog.close());
    close.setAttribute("aria-label", "Закрыть «" + titleText + "»");
    header.prepend(title);
    const body = element("div", undefined, "plugin-settings-body");
    dialog.append(header, body);
    document.body.append(dialog);
    return { dialog, body, title };
  }
  const recoveryDialog = modal(
    "recoverySettingsDialog",
    "Автоповтор ошибок проекта",
  );
  function recoveryPanel(parent, suffix) {
    const section = element("section", undefined, "plugin-inline-group");
    const heading = element("h3", "Восстановление ошибок");
    heading.append(
      workspaceUi.help(
        "Встроенная функция Studio, не требует Hermes. После штатного завершения локального перевода повторяет только ошибки памяти и лимитов: до двух дополнительных проходов и до 200 строк на задание. Паузы, аварийные остановки, ручные правки и сохранённые оригиналы защищены.",
        "Об автоповторе",
      ),
    );
    const label = element("label", undefined, "plugin-switch");
    const automatic = element("input");
    automatic.id = "automaticErrorRetry" + suffix;
    automatic.type = "checkbox";
    automatic.setAttribute("role", "switch");
    const track = element("span", undefined, "plugin-switch-track");
    track.setAttribute("aria-hidden", "true");
    label.append(
      automatic,
      track,
      document.createTextNode("Повторять ошибки памяти и лимитов"),
    );
    const hint = element(
      "p",
      "Для этого проекта · сохраняется сразу · работает без Hermes",
      "sub",
    );
    const plan = element("pre", undefined, "plugin-task-report");
    plan.id = "smartRetryPlan" + suffix;
    const start = button(
      "startSmartRetry" + suffix,
      "Запустить подобранные повторы",
      section,
      async () => {
        await window.pywebview.api.start_smart_retry(studioState.project);
        await refresh();
        await window.refresh();
      },
    );
    section.prepend(heading, label, hint, plan);
    parent.append(section);
    automatic.onchange = async () => {
      const project = studioState.project,
        enabled = automatic.checked;
      recoveryPending = true;
      render();
      try {
        await window.pywebview.api.set_automatic_retry(project, enabled);
        await refresh();
      } catch (error) {
        toast(error.message);
      } finally {
        recoveryPending = false;
        render();
      }
    };
    recoveryViews.push({ automatic, plan, start });
  }
  const recoveryDetails = element("details");
  recoveryDetails.id = "errorRecoveryDetails";
  recoveryDetails.append(
    element("summary", "Автоповтор ошибок · встроенная функция"),
  );
  recoveryPanel(recoveryDetails, "");
  errorsPane.prepend(recoveryDetails);
  recoveryPanel(recoveryDialog.body, "Settings");
  const recoveryProject = element("section", undefined, "ux-settings-group");
  const recoveryProjectHeading = element("h3", "Восстановление ошибок");
  const recoverySummary = element("p", undefined, "sub");
  recoverySummary.id = "projectRecoverySummary";
  button(
    "configureProjectRecovery",
    "Настроить автоповторы",
    recoveryProject,
    async () => {
      await refresh();
      recoveryDialog.title.textContent =
        "Автоповтор ошибок · " +
        (studioState.snapshot?.projects.find(
          (x) => x.id === studioState.project,
        )?.name || "проект");
      recoveryDialog.dialog.showModal();
    },
  );
  recoveryProject.prepend(recoveryProjectHeading, recoverySummary);
  $("settingsPanel-translation").append(recoveryProject);

  function render() {
    if (!state) return;
    const plan = state.retry_plan?.rows || [];
    for (const view of recoveryViews) {
      if (!recoveryPending) view.automatic.checked = !!state.automatic_retry;
      view.automatic.disabled = recoveryPending || !studioState.project;
      view.start.disabled = state.busy || !plan.length;
      setText(
        view.plan,
        plan.length
          ? plan
              .map(
                (row) =>
                  `#${row.record}: ${row.reason}\nКонтекст ${row.parameters.context}, ответ ${row.parameters.max_output}`,
              )
              .join("\n\n")
          : "Нет подходящих ошибок завершённого локального перевода",
      );
    }
    setText(
      recoverySummary,
      state.automatic_retry
        ? "Автоповтор включён для этого проекта"
        : "Автоповтор отключён для этого проекта",
    );
  }
  async function refresh() {
    if (!window.pywebview?.api?.get_recovery_state) return;
    const own = ++sequence,
      project = studioState.project;
    const value = await window.pywebview.api.get_recovery_state(project);
    if (own !== sequence || project !== studioState.project) return;
    state = value;
    render();
  }
  studioLifecycle.register(
    "refresh",
    "coreRecovery",
    async ({ previousProject }) => {
      if (previousProject !== studioState.project)
        recoveryDialog.dialog.close();
      await refresh();
    },
  );
  studioLifecycle.register("navigate", "coreRecovery", () =>
    refresh().catch((e) => toast(e.message)),
  );
  setInterval(() => {
    if (
      recoveryDialog.dialog.open ||
      $("projectSettingsDialog").open ||
      studioState.viewMode === "errors"
    )
      refresh().catch(() => {});
  }, 2500);
  return { refresh };
})();

const updatesMenu = element("details");
updatesMenu.id = "updatesMenu";
updatesMenu.append(element("summary", "Обновления приложения"));
const updateVersion = element("p", "Версия приложения…", "sub");
updateVersion.id = "updateVersion";
const updateMessage = element("p", undefined, "sub");
updateMessage.id = "updateMessage";
updateMessage.setAttribute("role", "status");
const updateProgress = element("progress");
updateProgress.id = "updateDownloadProgress";
updateProgress.style.width = "100%";
updateProgress.hidden = true;
const checkUpdates = element("button", "Проверить обновления");
checkUpdates.id = "checkUpdates";
const downloadUpdate = element("button", "Скачать обновление");
downloadUpdate.id = "downloadUpdate";
const installUpdate = element(
  "button",
  "Установить и перезапустить",
  "primary",
);
installUpdate.id = "installUpdate";
downloadUpdate.hidden = installUpdate.hidden = true;
const actions = element("div", undefined, "update-actions");
actions.append(checkUpdates, downloadUpdate, installUpdate);
const releaseNotes = element("details");
releaseNotes.id = "updateReleaseNotes";
releaseNotes.append(element("summary", "Что нового"));
const notes = element("pre");
notes.style.cssText =
  "white-space:pre-wrap;overflow-wrap:anywhere;font:inherit";
releaseNotes.append(notes);
releaseNotes.hidden = true;
const updateOptions = {};
for (const [key, title] of [
  ["auto_check", "Проверять при запуске"],
  ["auto_download", "Автоматически скачивать новую версию"],
  ["auto_install", "Устанавливать автоматически, когда нет активных задач"],
]) {
  const label = element("label", undefined, "update-option");
  const checkbox = element("input");
  checkbox.type = "checkbox";
  checkbox.id = "update_" + key;
  label.append(checkbox, document.createTextNode(" " + title));
  updateOptions[key] = checkbox;
  checkbox.onchange = guard(async () => {
    const preferences = Object.fromEntries(
      Object.entries(updateOptions).map(([name, input]) => [
        name,
        input.checked,
      ]),
    );
    displayUpdateState(
      await window.pywebview.api.set_update_preferences(preferences),
    );
  });
  updatesMenu.append(label);
}
updatesMenu
  .querySelector("summary")
  .after(updateVersion, updateMessage, updateProgress, actions, releaseNotes);
updatesMenu.append(
  element(
    "p",
    "Источник: GitHub Releases · Kenorbrook/LocalizationStudio. Интернет нужен только для проверки и скачивания. Автоустановка ждёт завершения задач и сохранения ручных правок; затем приложение перезапустится. Перед установкой создаётся резервная копия.",
    "sub",
  ),
);
setupCard.append(updatesMenu);
const updatesStyle = element("style");
updatesStyle.textContent =
  ".update-actions{display:flex;flex-direction:column;gap:8px;margin:12px 0}.update-option{display:flex;align-items:flex-start;gap:7px;line-height:1.5;margin:12px 0}.update-option input{flex:0 0 auto;margin-top:4px}.update-actions button{width:100%}#updateReleaseNotes{margin:12px 0}";
document.head.append(updatesStyle);
let updateState = null;
function displayUpdateState(state) {
  updateState = state;
  setText(
    updateVersion,
    "Установлена версия " +
      state.current +
      (state.latest ? " · последняя " + state.latest : ""),
  );
  setText(updateMessage, state.message);
  const busy = ["checking", "downloading", "installing"].includes(state.phase);
  checkUpdates.disabled = busy;
  downloadUpdate.hidden =
    !state.supported ||
    !["available", "error"].includes(state.phase) ||
    !state.available;
  installUpdate.hidden = state.phase !== "ready";
  releaseNotes.hidden = !state.notes;
  setText(notes, state.notes || "");
  updateProgress.hidden = state.phase !== "downloading";
  updateProgress.max = state.total || 1;
  updateProgress.value = state.downloaded || 0;
  for (const [key, input] of Object.entries(updateOptions))
    input.checked = !!state.preferences[key];
}
async function refreshUpdates() {
  if (!window.pywebview?.api?.get_update_state) return;
  displayUpdateState(await window.pywebview.api.get_update_state());
}
checkUpdates.onclick = guard(async () =>
  displayUpdateState(await window.pywebview.api.check_updates()),
);
downloadUpdate.onclick = guard(async () =>
  displayUpdateState(await window.pywebview.api.download_update()),
);
installUpdate.onclick = guard(async () => {
  if (dirty.size)
    throw Error("Сохраните или отмените ручные правки перед установкой");
  $("infoTitle").textContent = "Установить версию " + updateState.latest + "?";
  $("infoContent").replaceChildren(
    element(
      "p",
      "Текущие запросы завершатся, очередь сохранится. Приложение перезапустится; работающие задачи продолжатся с прежними параметрами. Проекты, переводы и настройки останутся. Перед заменой файлов будет создана резервная копия.",
    ),
  );
  const confirm = element("button", "Установить и перезапустить", "primary");
  confirm.id = "confirmInstallUpdate";
  confirm.onclick = guard(async () => {
    displayUpdateState(await window.pywebview.api.install_update());
    $("infoDialog").close();
    updatesMenu.open = true;
  });
  $("infoContent").append(confirm);
  $("infoDialog").showModal();
});
updatesMenu.ontoggle = () => {
  if (updatesMenu.open) refreshUpdates().catch((error) => toast(error.message));
};
window.addEventListener("pywebviewready", () =>
  refreshUpdates().catch((error) => toast(error.message)),
);
window.addEventListener("DOMContentLoaded", () => {
  refreshUpdates().catch((error) => toast(error.message));
  setInterval(() => {
    if (
      updatesMenu.open ||
      ["checking", "downloading", "installing"].includes(updateState?.phase)
    )
      refreshUpdates().catch((error) => setText(updateMessage, error.message));
  }, 1000);
});

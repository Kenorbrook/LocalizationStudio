const leftSidebar = document.querySelector("main > aside:not(.right)");
leftSidebar.id = "projectSidebar";
const sidebarContent = element("div");
sidebarContent.id = "sidebarContent";
sidebarContent.append(...leftSidebar.childNodes);
leftSidebar.append(sidebarContent);
const appFooter = element("footer");
appFooter.id = "appFooter";
const versionRow = element("div", undefined, "app-version-row");
const updateVersion = element("span", "Версия…", "sub");
updateVersion.id = "updateVersion";
const appSettings = element("button", "⚙");
appSettings.id = "appSettings";
appSettings.title = "Настройки приложения";
appSettings.setAttribute("aria-label", "Настройки приложения");
versionRow.append(updateVersion, appSettings);
const footerUpdate = element("button", "Обновить", "primary");
footerUpdate.id = "footerUpdate";
footerUpdate.hidden = true;
appFooter.append(versionRow, footerUpdate);
leftSidebar.append(appFooter);
const updatesDialog = element("dialog");
updatesDialog.id = "updatesDialog";
updatesDialog.setAttribute("aria-labelledby", "updatesTitle");
const updatesHeader = element("div", undefined, "updates-header");
const updatesTitle = element("h2", "Настройки приложения");
updatesTitle.id = "updatesTitle";
const updatesClose = element("button", "×");
updatesClose.id = "updatesClose";
updatesClose.setAttribute("aria-label", "Закрыть настройки");
updatesHeader.append(updatesTitle, updatesClose);
const updatesBody = element("div");
updatesBody.id = "updatesBody";
updatesBody.append(element("h3", "Обновления"));
updatesDialog.append(updatesHeader, updatesBody);
document.body.append(updatesDialog);
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
  updatesBody.append(label);
}
updatesBody
  .querySelector("h3")
  .after(updateMessage, updateProgress, actions, releaseNotes);
const updateSource = element(
  "p",
  "Источник: GitHub Releases · Kenorbrook/LocalizationStudio. Перед установкой создаётся резервная копия; приложение перезапустится, сохранив очередь и настройки.",
  "sub",
);
updateSource.id = "updateSource";
updateSource.hidden = true;
updatesBody.append(updateSource);
let updateState = null;
function displayUpdateState(state) {
  updateState = state;
  setText(updateVersion, "Версия " + state.current);
  const available = !!state.available;
  footerUpdate.hidden = !available || !state.supported;
  footerUpdate.disabled = ["checking", "downloading", "installing"].includes(
    state.phase,
  );
  updateSource.hidden = !available;
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
  if (studioState.dirty.size)
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
    openAppSettings();
  });
  $("infoContent").append(confirm);
  $("infoDialog").showModal();
});
function openAppSettings() {
  if (!updatesDialog.open) updatesDialog.showModal();
  updatesBody.scrollTop = 0;
  updatesClose.focus({ preventScroll: true });
  refreshUpdates().catch((error) => toast(error.message));
}
appSettings.onclick = openAppSettings;
updatesClose.onclick = () => updatesDialog.close();
footerUpdate.onclick = guard(async () => {
  openAppSettings();
  if (updateState?.phase === "ready") await installUpdate.onclick();
  else if (updateState?.available) await downloadUpdate.onclick();
});
window.addEventListener("pywebviewready", () =>
  refreshUpdates().catch((error) => toast(error.message)),
);
window.addEventListener("DOMContentLoaded", () => {
  refreshUpdates().catch((error) => toast(error.message));
  setInterval(() => {
    refreshUpdates().catch((error) => {
      if (updatesDialog.open) setText(updateMessage, error.message);
    });
  }, 2500);
});

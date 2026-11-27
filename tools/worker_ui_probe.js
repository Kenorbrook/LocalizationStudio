(async () => {
  for (let i = 0; i < 150 && !studioState.project; i++) await new Promise(r => setTimeout(r, 40));
  await refresh();
  showView("text");
  await loadRows();
  if (pendingTranslationText({state: "paused", worker_active: 0}).includes("работает")) throw Error("Paused worker shown as working");
  if (pendingTranslationText({state: "cancelled", worker_active: 0}).includes("работает")) throw Error("Cancelled worker shown as working");
  if (!pendingTranslationText({state: "paused", worker_active: 1}).includes("Завершаем")) throw Error("In-flight pause not explained");
  studioLifecycle.notify("localModels", {online:false});
  if ($("connection").dataset.ollama !== "offline") throw Error("Offline status not updated");
  studioLifecycle.notify("localModels", {online:true});
  if ($("connection").dataset.ollama !== "online") throw Error("Recovered Ollama still shown as offline");
  const previousFocus=document.activeElement, previousModel=$("model").value, previousContext=$("context").value;
  await localConnection.refresh();
  if(document.activeElement!==previousFocus||$("model").value!==previousModel||$("context").value!==previousContext)throw Error("Health refresh changed input state");
  window.__featureProbe = { passed: true, ready: true };
})().catch(error => {
  window.__featureProbe = { passed: false, error: error.message, stack: error.stack };
});

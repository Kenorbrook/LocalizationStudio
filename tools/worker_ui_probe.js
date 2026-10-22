(async () => {
  for (let i = 0; i < 150 && !studioState.project; i++) await new Promise(r => setTimeout(r, 40));
  await refresh();
  showView("text");
  await loadRows();
  if (pendingTranslationText({state: "paused", worker_active: 0}).includes("работает")) throw Error("Paused worker shown as working");
  if (pendingTranslationText({state: "cancelled", worker_active: 0}).includes("работает")) throw Error("Cancelled worker shown as working");
  if (!pendingTranslationText({state: "paused", worker_active: 1}).includes("Завершаем")) throw Error("In-flight pause not explained");
  window.__featureProbe = { passed: true, ready: true };
})();

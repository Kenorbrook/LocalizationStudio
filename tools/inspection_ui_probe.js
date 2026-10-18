(async () => {
  for (let i = 0; i < 150 && !studioState.project; i++) await new Promise(r => setTimeout(r, 40));
  await refresh();
  showView("text");
  await loadRows();
  const secret = document.createElement("input");
  secret.id = "inspectionPassword";
  secret.type = "password";
  secret.value = "fixture-secret-must-not-leak";
  document.body.append(secret);
  const masked = studioInspection.snapshot();
  if (JSON.stringify(masked).includes(secret.value)) throw Error("Secret leaked");
  if (masked.controls.find(c => c.id === secret.id)?.value !== "[redacted]") throw Error("Secret control not redacted");
  secret.type = "text";
  if (studioInspection.snapshot().screenshot_available) throw Error("Revealed secret allowed");
  secret.remove();
  window.__featureProbe = { passed: true, ready: true, secretsRedacted: true };
  // The fixture changes its own UI, without OS input, so external captures must be fresh.
  let count = 0;
  setInterval(() => {
    document.title = "Inspection fixture " + ++count;
    document.body.style.borderTop = `16px solid ${count % 2 ? "red" : "blue"}`;
  }, 1000);
})();

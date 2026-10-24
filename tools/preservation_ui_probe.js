(async () => {
  const checks = {};
  const check = (name, value) => {
    checks[name] = !!value;
    if (!value) throw Error(name);
  };
  const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));
  try {
    for (let i = 0; i < 150 && !studioState.project; i++) await sleep(40);
    await refresh();
    await $("reviewFolder").onclick();
    const menu = document.querySelector("[data-preserve-menu]");
    check("manualReviewHasReason", !!menu);
    menu.open = true;
    const reason = menu.querySelector("input");
    const rowId = +reason.dataset.preserveReason;
    reason.value = "Авторская фраза";
    reason.dispatchEvent(new Event("input"));
    reason.focus({preventScroll:true});
    reason.setSelectionRange(4, 9);
    check("reasonCountsAsActiveEditor", editorIsActive());
    await refresh();
    check("refreshKeepsFocusAndSelection", document.activeElement === reason && reason.selectionStart === 4 && reason.selectionEnd === 9);
    const data = await request("marked?project=" + studioState.project + "&kind=review");
    renderRows(data, true);
    check("quietRenderNeverDetachesUnchangedCard", document.activeElement === reason && reason.isConnected && reason.selectionStart === 4 && reason.selectionEnd === 9);
    await sleep(5700);
    check("timerKeepsFocusAndDraft", document.activeElement === reason && reason.value === "Авторская фраза" && reason.isConnected && menu.open);
    const before = await request("current?id=" + rowId);
    menu.querySelector('[data-preserve-preset="Другой язык"]').click();
    check("foreignPresetFillsReason", reason.value === "Другой язык" && document.activeElement === reason);
    menu.querySelector('[data-preserve-preset="Авторский приём"]').click();
    check("authorPresetFillsReason", reason.value === "Авторский приём" && document.activeElement === reason);
    check("presetsDoNotSubmit", (await request("current?id=" + rowId)).revision === before.revision);
    reason.value += ": оставить фразу как в оригинале";
    reason.dispatchEvent(new Event("input"));
    const draft = reason.value;
    reason.blur();
    await loadRows();
    const replacement = document.querySelector(`[data-preserve-menu="${rowId}"]`);
    check("explicitReloadKeepsDraftAndOpenMenu", replacement.open && replacement.querySelector("input").value === draft);
    await replacement.querySelector("[data-preserve-submit]").onclick();
    const saved = await request("current?id=" + rowId);
    check("submitPersistsReasonAndOriginal", saved.preserve_kind === "foreign" && saved.preserve_reason === draft && saved.text === saved.source);
    check("savedDraftCleared", !preservationDrafts.has(`${studioState.project}/${rowId}`));
    check("confirmationVisible", $("toast").textContent === "Оригинал сохранён без перевода");
    check("refreshRemainsHealthy", !$("connection").textContent.includes("Сервер недоступен"));
    showView("text");
    await loadRows();
    const nextMenu = document.querySelector("[data-preserve-menu]");
    nextMenu.open = true;
    nextMenu.querySelector("input").focus({preventScroll:true});
    nextMenu.scrollIntoView({block:"center"});
    check("nextEditorAvailable", nextMenu.isConnected && nextMenu.querySelectorAll("[data-preserve-preset]").length === 2);
    window.__featureProbe = {passed:true, checks};
  } catch(error) {
    window.__featureProbe = {passed:false, checks, error:error.message, stack:error.stack};
  }
})();

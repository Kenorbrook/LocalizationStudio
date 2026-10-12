(async () => {
  try {
    for (let attempt = 0; attempt < 100 && !studioState.project; attempt++)
      await new Promise(resolve => setTimeout(resolve, 50));
    if (!studioState.project) throw Error("Project did not finish loading");
    const results = {};
    const check = (key, value) => {results[key] = !!value; if (!value) throw Error(key);};
    showView("home");
    check("analysisControls", !!$("analyzeKnowledge") && !!$("viewKnowledge"));
    $("provider").value = "cloud";
    $("cloud_model").value = "venice-uncensored-1-2";
    $("provider").dispatchEvent(new Event("change"));
    check("analysisShowsSelectedModel", $("analyzeKnowledge").textContent.includes("облако: venice-uncensored-1-2"));
    check("providerIncludedInSavedSettings", settings().provider === "cloud");
    check("automaticContextDefault", $("use_project_knowledge").checked && $("analyze_before_translate").checked);
    await $("viewKnowledge").onclick();
    check("dictionaryOpen", $("knowledgeDialog").open);
    check("evidenceVisible", $("knowledgeDialog").textContent.includes("the captain of Aurora"));
    check("uncertaintyVisible", $("knowledgeDialog").textContent.includes("Требует проверки"));
    const card = $("knowledgeDialog").querySelector("[data-knowledge-id]");
    const area = card.querySelector("textarea");
    area.value = "Уточнено человеком";
    await [...card.querySelectorAll("button")].find(b => b.textContent.includes("Подтвердить")).onclick();
    let facts = await request(`knowledge?project=${studioState.project}`);
    check("humanDecisionSaved", facts.rows.some(r => r.state === "confirmed" && r.value === "Уточнено человеком"));
    const second = [...$("knowledgeDialog").querySelectorAll("[data-knowledge-id]")].find(c => !c.textContent.includes("Подтверждено мной"));
    await [...second.querySelectorAll("button")].find(b => b.textContent === "Отклонить").onclick();
    facts = await request(`knowledge?project=${studioState.project}`);
    check("rejectionSaved", facts.rows.some(r => r.state === "rejected"));
    const rid = facts.rows.find(r => r.state === "confirmed").data.evidence[0].id;
    const context = await request(`context?id=${rid}`);
    check("memoryAutomaticallySelected", context.metadata.project_memory.some(r => r.status === "confirmed" && r.value === "Уточнено человеком"));
    check("rejectedNotSelected", !context.metadata.project_memory.some(r => r.status === "rejected"));
    check("sourceAndTranslationUnchanged", context.rows.every(r => r.revision === 0 && r.text === ""));
    window.__featureProbe = {passed: true, results};
  } catch (error) {
    window.__featureProbe = {passed: false, error: error.stack};
  }
})();

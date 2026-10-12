"use strict";
const knowledgeTools = element("section", undefined, "knowledge-panel");
knowledgeTools.id = "knowledgeTools";
knowledgeTools.append(
  element("h2", "Контекст проекта"),
  element(
    "p",
    "ИИ прочитает импортированные тексты и соберёт персонажей, отношения и термины. Сведения с цитатами подбираются к переводу автоматически. Спорные выводы ждут вашей проверки.",
    "sub",
  ),
);
const analyzeKnowledge = element("button", "Анализировать проект выбранной ИИ");
analyzeKnowledge.id = "analyzeKnowledge";
const viewKnowledge = element("button", "Справочник и неясные места");
viewKnowledge.id = "viewKnowledge";
const knowledgeActions = element("div", undefined, "flex wrap");
knowledgeActions.append(analyzeKnowledge, viewKnowledge);
knowledgeTools.append(knowledgeActions);
const autoKnowledgeLabel = element("label");
const autoKnowledge = element("input");
autoKnowledge.id = "analyze_before_translate";
autoKnowledge.type = "checkbox";
autoKnowledge.checked = true;
autoKnowledge.style.width = "auto";
autoKnowledgeLabel.append(
  autoKnowledge,
  document.createTextNode(
    " Сначала анализировать весь проект при запуске перевода (готовые блоки берутся из кэша)",
  ),
);
const useKnowledgeLabel = element("label");
const useKnowledge = element("input");
useKnowledge.id = "use_project_knowledge";
useKnowledge.type = "checkbox";
useKnowledge.checked = true;
useKnowledge.style.width = "auto";
useKnowledgeLabel.append(
  useKnowledge,
  document.createTextNode(
    " Автоматически подбирать сведения для перевода и редактуры",
  ),
);
knowledgeTools.append(autoKnowledgeLabel, useKnowledgeLabel);
$("projectHome").append(knowledgeTools);
studioLifecycle.register("settings", "knowledgeSettings", () => ({
  use_project_knowledge: useKnowledge.checked,
  analyze_before_translate: autoKnowledge.checked,
}));
let knowledgeProject = 0;
studioLifecycle.register("refresh", "knowledgeProjectSettings", () => {
  if (knowledgeProject === studioState.project) return;
  knowledgeProject = studioState.project;
  const p = studioState.snapshot.projects.find(
    (p) => p.id === studioState.project,
  );
  const values = JSON.parse(p?.settings || "{}");
  useKnowledge.checked = values.use_project_knowledge !== false;
  autoKnowledge.checked = values.analyze_before_translate !== false;
});
analyzeKnowledge.onclick = guard(async () => {
  if (!studioState.project) throw Error("Откройте проект");
  const provider = $("provider").value;
  if (provider === "mcp")
    throw Error("Для анализа выберите локальную модель или облачный API");
  await request("job", {
    project: studioState.project,
    stage: "analyze",
    provider,
    settings: settings(),
  });
  await refresh();
  toast(
    "Анализ запущен в отдельном PowerShell. Тексты и ручные правки сохраняются.",
  );
});
const knowledgeDialog = element("dialog");
knowledgeDialog.id = "knowledgeDialog";
knowledgeDialog.style.width = "min(1000px, 90vw)";
const knowledgeFilter = element("select");
for (const [value, label] of [
  ["", "Все сведения"],
  ["review", "Неясные места — проверить"],
  ["auto", "Выводы ИИ"],
  ["confirmed", "Подтверждено мной"],
  ["rejected", "Отклонено"],
]) {
  const option = element("option", label);
  option.value = value;
  knowledgeFilter.append(option);
}
const knowledgeSummary = element("p", "", "sub");
const knowledgeCards = element("div");
const knowledgePrev = element("button", "← Назад");
const knowledgeNext = element("button", "Далее →");
const knowledgeClose = element("button", "Закрыть");
knowledgeClose.onclick = () => knowledgeDialog.close();
knowledgeDialog.append(
  element("h1", "Персонажи, отношения и термины"),
  knowledgeSummary,
  knowledgeFilter,
  knowledgeCards,
  knowledgePrev,
  knowledgeNext,
  knowledgeClose,
);
document.body.append(knowledgeDialog);
let knowledgeOffset = 0,
  knowledgeOwner = 0;
async function loadKnowledge() {
  const pid = knowledgeOwner;
  const result = await request(
    `knowledge?project=${pid}&offset=${knowledgeOffset}&state=${knowledgeFilter.value}`,
  );
  if (pid !== knowledgeOwner) return;
  knowledgeSummary.textContent = `Сведений: ${result.total}. Требуют проверки: ${result.counts.review || 0}. ИИ может ошибаться; исходник и ваши правила приоритетны.`;
  knowledgeCards.replaceChildren();
  const states = {
    auto: "Вывод ИИ",
    review: "Требует проверки",
    confirmed: "Подтверждено мной",
    rejected: "Отклонено",
  };
  const kinds = {
    character: "Персонаж",
    relationship: "Отношения",
    term: "Термин",
    scene: "Сцена",
  };
  for (const item of result.rows) {
    const card = element("div", undefined, "knowledge-card");
    card.dataset.knowledgeId = item.id;
    card.append(
      element(
        "h3",
        `${kinds[item.kind]} · ${item.subject} · ${item.attribute}`,
      ),
      element(
        "p",
        `${states[item.state]}${item.live ? "" : " · Исходник изменился — повторите анализ"}`,
      ),
    );
    const value = element("textarea");
    value.value = item.value;
    value.maxLength = 800;
    card.append(value, element("p", item.data.reason));
    for (const ref of item.data.evidence)
      card.append(element("p", `Оригинал #${ref.id}: ${ref.quote}`, "sub"));
    for (const [state, label] of [
      ["confirmed", "Подтвердить / сохранить правку"],
      ["rejected", "Отклонить"],
      ["review", "Оставить вопросом"],
    ]) {
      const button = element("button", label);
      button.disabled = state === "confirmed" && !item.live;
      button.onclick = guard(async () => {
        await request("knowledge-decision", {
          project: pid,
          id: item.id,
          revision: item.revision,
          state,
          value: value.value,
        });
        await loadKnowledge();
      });
      card.append(button);
    }
    knowledgeCards.append(card);
  }
  if (!result.rows.length)
    knowledgeCards.append(
      element(
        "p",
        "Сведений пока нет. Запустите анализ или выберите другой фильтр.",
      ),
    );
  knowledgePrev.disabled = knowledgeOffset === 0;
  knowledgeNext.disabled = knowledgeOffset + 50 >= result.total;
}
viewKnowledge.onclick = guard(async () => {
  knowledgeOwner = studioState.project;
  knowledgeOffset = 0;
  await loadKnowledge();
  knowledgeDialog.showModal();
});
knowledgeFilter.onchange = guard(async () => {
  knowledgeOffset = 0;
  await loadKnowledge();
});
knowledgePrev.onclick = guard(async () => {
  knowledgeOffset = Math.max(0, knowledgeOffset - 50);
  await loadKnowledge();
});
knowledgeNext.onclick = guard(async () => {
  knowledgeOffset += 50;
  await loadKnowledge();
});

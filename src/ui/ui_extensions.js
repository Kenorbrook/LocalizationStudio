function ensureBlockPicker(active, f) {
  const existing = active.querySelector("select");
  if (existing) {
    existing.value = offset;
    return;
  }
  const select = element("select");
  select.className = "full";
  select.style.marginTop = "10px";
  select.setAttribute("aria-label", "Выбрать блок файла");
  const count = Math.ceil(f.total / 50);
  for (let i = 0; i < count; i++) {
    let o = element(
      "option",
      `Блок ${i + 1} · строки ${i * 50 + 1}–${Math.min(f.total, (i + 1) * 50)}`,
    );
    o.value = i * 50;
    select.append(o);
  }
  select.value = offset;
  select.onclick = (e) => e.stopPropagation();
  select.onchange = guard(async () => {
    if (dirty.size) {
      select.value = offset;
      throw Error("Сохраните правки перед сменой блока");
    }
    offset = +select.value;
    $("search").value = "";
    $("status").value = "";
    await loadRows();
  });
  active.append(select);
}
studioLifecycle.register(
  "refresh",
  "baseRefresh",
  async ({ initial, previousProject: oldProject }) => {
    const f = snapshot.files.find((x) => x.id === file);
    const active = document.querySelector(".file.active");
    if (f && active) ensureBlockPicker(active, f);
  },
);
const proposalsButton = element("button", "Предложения MCP");
$("errorsButton").before(proposalsButton);
proposalsButton.onclick = guard(async () => {
  const proposals = await request("proposals?project=" + project);
  $("infoTitle").textContent = "Предложения внешней ИИ";
  $("infoContent").replaceChildren();
  for (const p of proposals) {
    let card = element("div", undefined, "error");
    card.append(
      element("div", `#${p.record} · ${p.reviewer}`),
      element("p", "Оригинал: " + p.source),
      element("p", "Сейчас: " + p.current),
      element("p", "Предложение: " + p.text),
      element("pre", p.reason),
    );
    for (const [label, accept, verified] of [
      ["Принять правку", true, false],
      ["Принять и подтвердить проверку", true, true],
      ["Отклонить", false, false],
    ]) {
      let b = element("button", label);
      b.style.marginRight = "8px";
      b.onclick = guard(async () => {
        await request("proposal", { id: p.id, accept, verified });
        card.remove();
        await refresh();
        toast(accept ? "Предложение принято" : "Предложение отклонено");
      });
      card.append(b);
    }
    $("infoContent").append(card);
  }
  if (!proposals.length)
    $("infoContent").append(
      element(
        "p",
        "Ожидающих предложений нет. Они появятся после работы внешней ИИ через MCP.",
      ),
    );
  $("infoDialog").showModal();
});
// Allow progress polling during editing; quiet row updates preserve editor contents.
// The initial timer skips focused editors. A second lightweight poll handles that case.
setInterval(async () => {
  if (
    busy ||
    document.querySelector("dialog[open]") ||
    document.activeElement.tagName !== "TEXTAREA"
  )
    return;
  busy = true;
  try {
    await refresh();
  } catch {
  } finally {
    busy = false;
  }
}, 2500);
// Text can be supplied without creating a file outside the application first.
const textLabel = element(
  "p",
  "Или вставить отдельный текст (каждая непустая строка — новая запись):",
  "sub",
);
const extraText = element("textarea");
extraText.id = "extraText";
extraText.placeholder = "Вставьте недостающий оригинальный текст";
const importActions = $("doImport").parentElement;
importActions.before(textLabel, extraText);
$("doImport").onclick = guard(async () => {
  let added = 0;
  const uploaded = $("uploadFiles").files;
  const files = await Promise.all(
    [...uploaded].map(async (f) => ({ name: f.name, text: await f.text() })),
  );
  if (extraText.value.trim())
    files.push({
      name: "additional-" + Date.now() + ".txt",
      text: extraText.value,
    });
  const messages = [];
  if (files.length) {
    const r = await request("upload", { project, files });
    added += r.added;
    messages.push(...r.errors.map((e) => e.message));
  }
  const paths = $("importPaths")
    .value.split("\n")
    .map((x) => x.trim())
    .filter(Boolean);
  if (paths.length) {
    const r = await request("import", { project, paths });
    added += r.added;
    messages.push(...r.errors.map((e) => e.message));
  }
  $("importDialog").close();
  $("uploadFiles").value = "";
  $("importPaths").value = "";
  extraText.value = "";
  await refresh();
  toast(
    "Добавлено строк: " +
      added +
      (messages.length ? " · " + messages.join("; ") : ""),
  );
});

const closePrefsMenu = element("details");
closePrefsMenu.id = "closePrefsMenu";
closePrefsMenu.append(element("summary", "При закрытии окна"));
const closeChoiceStatus = element("p", undefined, "sub");
closeChoiceStatus.id = "closeChoiceStatus";
const resetCloseChoice = element("button", "Снова спрашивать при закрытии");
resetCloseChoice.id = "resetCloseChoice";
async function updateCloseChoice() {
  if (!window.pywebview?.api) return;
  const state = await window.pywebview.api.get_close_state();
  closeChoiceStatus.textContent =
    "Сейчас: " +
    ({
      tray: "сворачивать в трей",
      exit: "закрывать программу и останавливать перевод",
    }[state.choice] || "спрашивать, что делать");
  resetCloseChoice.disabled = !state.choice;
}
resetCloseChoice.onclick = guard(async () => {
  await window.pywebview.api.reset_close_choice();
  await updateCloseChoice();
  toast("При следующем закрытии появится окно с выбором действия");
});
closePrefsMenu.append(
  closeChoiceStatus,
  element(
    "p",
    "Если при закрытии вы включили «Запомнить мой выбор», крестик выполняет сохранённое действие. Кнопка ниже возвращает окно выбора: свернуть в трей, закрыть программу или отменить.",
    "sub",
  ),
  resetCloseChoice,
);
closePrefsMenu.ontoggle = () => {
  if (closePrefsMenu.open) updateCloseChoice().catch((e) => toast(e.message));
};
document.querySelector(".right").append(closePrefsMenu);
const statusHelp = element("button", "ⓘ Статусы строк");
statusHelp.id = "statusHelp";
statusHelp.setAttribute("aria-label", "Что означают цвета строк");
$("newProject").after(statusHelp);
statusHelp.onclick = () => {
  const content = $("infoContent");
  $("infoTitle").textContent = "Цвета и статусы строк";
  content.replaceChildren();
  for (const [status, text] of [
    ["empty", "Без перевода — фраза ещё не переведена."],
    ["translated", "Переведено — получен перевод, редактура ещё не выполнена."],
    ["edited", "После редактуры — перевод прошёл литературную редактуру."],
    [
      "verified",
      "Проверено — текущую версию проверила облачная ИИ или человек.",
    ],
  ]) {
    const row = element("p");
    row.append(
      element("span", undefined, "dot " + status),
      document.createTextNode(text),
    );
    content.append(row);
  }
  content.append(
    element(
      "p",
      "Статус относится к текущей версии текста и не гарантирует отсутствие ошибок. Ручные правки защищены от автоматической перезаписи.",
      "sub",
    ),
  );
  $("infoDialog").showModal();
  content.scrollTop = 0;
  $("infoClose").focus({ preventScroll: true });
};

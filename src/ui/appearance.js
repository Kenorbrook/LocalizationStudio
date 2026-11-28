const themeOptions = [
  ["studio", "Классическая"],
  ["paper", "Бумажная мастерская"],
  ["book", "Книжный разворот"],
  ["print", "Типография"],
  ["console", "Пульт звукорежиссёра"],
  ["polar", "Полярная станция"],
  ["city", "Городская навигация"],
  ["film", "Ночной монтаж"],
  ["garden", "Садовый каталог"],
];

const themeSelect = element("select");
themeSelect.id = "appTheme";
for (const [value, title] of themeOptions) {
  const option = element("option", title);
  option.value = value;
  themeSelect.append(option);
}
themeSelect.value = document.documentElement.dataset.theme;
const themeLabel = element("label", "Тема приложения");
themeLabel.htmlFor = themeSelect.id;
const themeRow = element("div", undefined, "appearance-setting");
themeRow.append(themeLabel, themeSelect);
updatesBody.prepend(themeRow);

themeSelect.onchange = guard(async () => {
  const root = document.documentElement;
  const previous = root.dataset.theme;
  const requested = themeSelect.value;
  root.dataset.theme = requested;
  themeSelect.disabled = true;
  try {
    const save = window.pywebview?.api?.set_app_theme;
    if (save) await save(requested);
  } catch (error) {
    root.dataset.theme = themeSelect.value = previous;
    throw error;
  } finally {
    themeSelect.disabled = false;
  }
});

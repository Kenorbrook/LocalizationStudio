function createRunLimitControls(id, title, hint) {
  const section = element("details");
  section.id = id;
  section.append(element("summary", title));
  const mode = element("select");
  mode.id = id === "runLimits" ? "limitMode" : "newLimitMode";
  mode.setAttribute("aria-label", title);
  for (const [value, label] of [
    ["none", "Без ограничения"],
    ["lines", "По числу строк"],
    ["time", "По времени"],
  ]) {
    const option = element("option", label);
    option.value = value;
    mode.append(option);
  }
  const value = element("input");
  value.id = id === "runLimits" ? "limitValue" : "newLimitValue";
  value.type = "number";
  value.min = 1;
  value.value = 200;
  const label = element("label");
  label.htmlFor = value.id;
  const explanation = element("p", hint, "sub");
  section.append(mode, label, value, explanation);
  function update() {
    label.hidden = value.hidden = mode.value === "none";
    label.textContent =
      mode.value === "time"
        ? "Продолжительность, минут (120 = 2 часа)"
        : "Количество обрабатываемых строк";
    value.step = mode.value === "time" ? "0.1" : "1";
  }
  function read(validate = true) {
    const amount = +value.value;
    const maximum = mode.value === "time" ? 10080 : 1000000;
    if (
      validate &&
      mode.value !== "none" &&
      (!Number.isFinite(amount) ||
        amount <= 0 ||
        amount > maximum ||
        (mode.value === "lines" && !Number.isInteger(amount)))
    )
      throw Error(
        "Укажите допустимый положительный лимит; число строк должно быть целым",
      );
    return {
      run_lines: mode.value === "lines" ? amount : 0,
      run_minutes: mode.value === "time" ? amount : 0,
    };
  }
  function load(settings) {
    mode.value = settings.run_lines
      ? "lines"
      : settings.run_minutes
        ? "time"
        : "none";
    value.value = settings.run_lines || settings.run_minutes || 200;
    update();
  }
  mode.onchange = () => {
    value.value = mode.value === "time" ? 120 : 200;
    update();
  };
  update();
  return { section, mode, value, update, read, load };
}

function describeRunProgress(job) {
  if (!job) return { text: "", fraction: 0 };
  const settings = JSON.parse(job.settings || "{}");
  if (!job.run_started)
    return {
      text: "Отсчёт этого запуска появится после запуска обработчика.",
      fraction: 0,
    };
  const elapsed = Math.max(0, job.run_elapsed || 0);
  const processed = job.run_done || 0;
  if (settings.run_lines) {
    const remaining = Math.max(0, settings.run_lines - processed);
    return {
      text: `За этот запуск: ${processed} / ${settings.run_lines} строк · осталось ${remaining}`,
      fraction: processed / settings.run_lines,
    };
  }
  if (settings.run_minutes) {
    const maximum = settings.run_minutes * 60;
    const format = (seconds) =>
      `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
    return {
      text: `За этот запуск: ${format(elapsed)} / ${format(maximum)} · осталось ${format(Math.max(0, maximum - elapsed))} · строк: ${processed}`,
      fraction: elapsed / maximum,
    };
  }
  return {
    text: `За этот запуск обработано ${processed} строк · без ограничения`,
    fraction: null,
  };
}

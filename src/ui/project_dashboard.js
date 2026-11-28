(() => {
  const dashboard = element("section", undefined, "project-dashboard");
  dashboard.id = "projectDashboard";
  const progressCard = element("section", undefined, "dashboard-card");
  const progressTitle = element("h2", "Прогресс перевода");
  const progressText = element("strong");
  const meter = element("progress");
  meter.setAttribute("aria-label", "Прогресс проекта");
  const totals = element("p", undefined, "sub");
  progressCard.append(progressTitle, progressText, meter, totals);
  const graphCard = element("section", undefined, "dashboard-card");
  const controls = element("div", undefined, "dashboard-heading");
  const period = element("select");
  const unit = element("select");
  period.setAttribute("aria-label", "Период графика");
  unit.setAttribute("aria-label", "Единица измерения");
  function addOptions(select, values) {
    for (const [id, name] of values) {
      const option = element("option", name);
      option.value = id;
      select.append(option);
    }
  }
  addOptions(period, [
    ["hour", "Последний час"],
    ["day", "Последние сутки"],
    ["session", "Последний запуск"],
  ]);
  addOptions(unit, [
    ["words", "Слова"],
    ["lines", "Строки"],
  ]);
  controls.append(element("h2", "Темп перевода"), period, unit);
  const graph = element("div", undefined, "dashboard-graph");
  const scale = element("div", undefined, "sub dashboard-scale");
  const models = element("ul", undefined, "dashboard-models");
  const graphStatus = element("p", undefined, "sub");
  graphCard.append(controls, graphStatus, graph, scale, models);
  const queueCard = element("section", undefined, "dashboard-card");
  const queueHead = element("div", undefined, "dashboard-heading");
  const openQueue = element("button", "Открыть очередь");
  openQueue.onclick = () => $("viewQueue").onclick();
  queueHead.append(element("h2", "Очередь обработки"), openQueue);
  queueCard.append(queueHead);
  const empty = element("div", undefined, "dashboard-empty");
  const prepare = element("button", "Найти и добавить текст", "primary");
  prepare.onclick = () => $("projectPreparationEntry").onclick();
  empty.append(element("p", "Текст ещё не импортирован"), prepare);
  dashboard.append(progressCard, graphCard, queueCard, empty);
  home.prepend(dashboard);
  const sidebar = element("section", undefined, "compact-jobs");
  sidebar.id = "compactJobs";
  right.prepend(sidebar);
  let sideSignature = "";
  let stats = null;
  let statsKey = "";
  let lastRequest = 0;
  let requestVersion = 0;
  const number = (value) => value.toLocaleString("ru-RU");
  const time = (value) =>
    new Date(value * 1000).toLocaleTimeString("ru-RU", {
      hour: "2-digit",
      minute: "2-digit",
    });
  function renderStats() {
    graph.replaceChildren();
    scale.replaceChildren();
    models.replaceChildren();
    const buckets = stats?.buckets || [];
    const key = unit.value;
    const values = buckets.map((item) => item[key]);
    const total = values.reduce((sum, value) => sum + value, 0);
    const max = Math.max(1, ...values);
    graph.hidden = scale.hidden = !total;
    setText(
      graphStatus,
      stats?.error ||
        (stats
          ? total
            ? number(total) +
              (key === "words" ? " слов" : " строк") +
              " · интервал " +
              number(stats.bucket_seconds / 60) +
              " мин"
            : "За этот период нет сохранённых переводов"
          : "Загрузка статистики…"),
    );
    if (!total) return;
    for (const [i, bucket] of buckets.entries()) {
      const bar = element("div", undefined, "dashboard-bar");
      bar.style.setProperty("--height", (100 * values[i]) / max + "%");
      bar.title = time(bucket.at) + " · " + number(values[i]);
      graph.append(bar);
    }
    scale.append(
      element("span", time(buckets[0].at)),
      element("span", time(buckets.at(-1).at)),
    );
    for (const [model, count] of Object.entries(stats.models).sort(
      (a, b) => b[1][key] - a[1][key],
    )) {
      models.append(
        element(
          "li",
          model.replace(/^(local|cloud|mcp):/, "") + " · " + number(count[key]),
        ),
      );
    }
  }
  async function loadStats() {
    if (
      studioState.viewMode !== "home" ||
      !studioState.project ||
      graphCard.hidden
    )
      return;
    const project = studioState.project;
    const key = project + "/" + period.value;
    if (key === statsKey && Date.now() - lastRequest < 12000) return;
    if (key !== statsKey) {
      stats = null;
      renderStats();
    }
    statsKey = key;
    lastRequest = Date.now();
    const requestId = ++requestVersion;
    try {
      const result = await request(
        `translation-stats?project=${project}&period=${period.value}`,
      );
      if (requestId !== requestVersion || studioState.project !== project)
        return;
      stats = result;
    } catch (error) {
      if (requestId !== requestVersion || studioState.project !== project)
        return;
      stats = { error: error.message };
    }
    renderStats();
  }
  period.onchange = loadStats;
  unit.onchange = renderStats;
  function render() {
    const snapshot = studioState.snapshot;
    const project = studioState.project;
    const isHome = studioState.viewMode === "home";
    $("activeProjectJob").hidden =
      isHome || !$("activeProjectJob").dataset.project;
    $("projectBack").hidden = studioState.viewMode !== "plugins" || !project;
    if (!snapshot) return;
    const counts = snapshot.counts || {};
    const translated =
      (counts.translated || 0) + (counts.edited || 0) + (counts.verified || 0);
    const preserved = counts.preserved || 0;
    const total = translated + (counts.empty || 0) + preserved;
    progressCard.hidden = graphCard.hidden = queueCard.hidden = !total;
    empty.hidden = !project || !!total;
    setText(
      progressText,
      number(translated) + " / " + number(total - preserved) + " строк",
    );
    meter.max = Math.max(1, total - preserved);
    meter.value = translated;
    setText(
      totals,
      [
        "Без перевода: " + number(counts.empty || 0),
        "После редактуры и проверки: " +
          number((counts.edited || 0) + (counts.verified || 0)),
        preserved ? "Оставлено в оригинале: " + number(preserved) : "",
      ]
        .filter(Boolean)
        .join(" · "),
    );
    if (isHome && projectQueues.area.parentElement !== queueCard)
      queueCard.append(projectQueues.area);
    else if (!isHome && projectQueues.area.parentElement === queueCard)
      $("futureTab").parentElement.after(projectQueues.area);
    document.body.dataset.projectDashboard = isHome ? "yes" : "no";
    const jobs = projectQueues.queues();
    sidebar.hidden = !jobs.length;
    document.body.dataset.compactTasks = jobs.length || !project ? "yes" : "no";
    const signature = JSON.stringify([
      project,
      jobs.map((j) => [
        j.id,
        j.state,
        j.done,
        j.total,
        !!j.retry_at,
        j.error,
        projectQueues.title(j),
      ]),
    ]);
    if (signature !== sideSignature) {
      sideSignature = signature;
      sidebar.replaceChildren(
        ...jobs.map((job) => {
          const card = element("button", undefined, "compact-job");
          card.dataset.state = job.state;
          const status =
            job.state === "running" && job.retry_at
              ? "Ожидание API"
              : jobLabels[job.state];
          card.append(
            element("strong", projectQueues.title(job)),
            element("span", status + " · " + jobCount(job), "sub"),
          );
          if (job.error)
            card.append(element("span", job.error, "compact-job-error"));
          card.onclick = guard(async () => {
            if (studioState.dirty.size)
              throw Error("Сохраните или отмените правки");
            studioState.selectedJobs.set(project, job.id);
            await $("viewQueue").onclick();
            await refresh();
          });
          return card;
        }),
      );
    }
    loadStats();
  }
  studioLifecycle.register("refresh", "projectDashboard", render);
  studioLifecycle.register("navigate", "projectDashboard", render);
})();

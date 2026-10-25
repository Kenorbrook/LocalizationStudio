"use strict";
const pluginsUi = (() => {
  const adapters = new Map(),
    rows = new Map(),
    pending = new Set();
  let items = [],
    filter = "all",
    signature = "",
    requestId = 0,
    savedScroll = 0,
    opened = false;
  const pane = element("section");
  pane.id = "pluginsPane";
  pane.hidden = true;
  pane.setAttribute("aria-label", "Управление плагинами");
  const toolbar = element("div", undefined, "plugins-toolbar");
  const search = element("input");
  search.id = "pluginSearch";
  search.type = "search";
  search.placeholder = "Найти плагин по названию или назначению";
  search.setAttribute("aria-label", "Поиск плагинов");
  const category = element("select");
  category.id = "pluginCategory";
  category.setAttribute("aria-label", "Категория плагинов");
  toolbar.append(search, category);
  const filters = element("div", undefined, "plugin-filters");
  filters.setAttribute("role", "group");
  filters.setAttribute("aria-label", "Фильтр плагинов");
  const names = {
    all: "Все",
    installed: "Установлены",
    enabled: "Включены",
    disabled: "Отключены",
    attention: "Требуют внимания",
    favorites: "Избранное",
  };
  for (const [key, name] of Object.entries(names)) {
    const button = element("button", name);
    button.dataset.pluginFilter = key;
    button.onclick = () => {
      filter = key;
      renderList();
    };
    filters.append(button);
  }
  const count = element("p", undefined, "sub");
  count.id = "pluginResultCount";
  count.setAttribute("role", "status");
  const feedback = element("p", undefined, "plugin-feedback");
  feedback.id = "pluginFeedback";
  feedback.setAttribute("role", "alert");
  feedback.hidden = true;
  const list = element("div", undefined, "plugin-list");
  list.id = "pluginList";
  const empty = element(
    "p",
    "Плагины не найдены. Измените поиск или фильтр.",
    "empty",
  );
  empty.id = "pluginEmpty";
  pane.append(toolbar, filters, count, feedback, list, empty);
  center.append(pane);
  const entry = element("button", "Плагины", "plugin-navigation");
  entry.id = "openPlugins";
  const enabledCount = element("span", "0", "ux-nav-count");
  enabledCount.id = "enabledPluginCount";
  entry.append(enabledCount);
  appFooter.prepend(entry);
  async function operation(identity, action) {
    if (pending.has(identity)) return;
    const focused = document.activeElement;
    pending.add(identity);
    updateRows();
    feedback.hidden = true;
    // Discard polling responses started before this mutation.
    requestId++;
    try {
      const result = await action();
      const changed = result.plugins.find((item) => item.id === identity);
      if (changed)
        accept({
          plugins: items.map((item) => (item.id === identity ? changed : item)),
        });
    } catch (error) {
      setText(feedback, error.message);
      feedback.hidden = false;
    } finally {
      pending.delete(identity);
      updateRows();
      if (
        focused?.closest(".plugin-row") &&
        document.activeElement === document.body
      ) {
        (focused.isConnected ? focused : search).focus({ preventScroll: true });
      }
    }
  }
  function makeRow(item) {
    const row = element("article", undefined, "plugin-row");
    row.dataset.pluginId = item.id;
    const favorite = element("button", "☆", "plugin-favorite");
    favorite.dataset.pluginFavorite = item.id;
    favorite.onclick = () =>
      operation(item.id, () =>
        window.pywebview.api.set_plugin_favorite(
          item.id,
          !items.find((x) => x.id === item.id).favorite,
        ),
      );
    const info = element("div", undefined, "plugin-info");
    const title = element("h2", item.name);
    const description = element("p", item.description, "sub");
    const status = element("span", undefined, "plugin-state");
    status.dataset.pluginState = item.id;
    const message = element("p", undefined, "sub");
    info.append(title, description, status, message);
    const actions = element("div", undefined, "plugin-row-actions");
    const toggleLabel = element("label", undefined, "plugin-switch");
    const toggle = element("input");
    toggle.type = "checkbox";
    toggle.setAttribute("role", "switch");
    toggle.setAttribute("aria-label", "Включить или отключить " + item.name);
    toggle.dataset.pluginToggle = item.id;
    const track = element("span", undefined, "plugin-switch-track");
    track.setAttribute("aria-hidden", "true");
    const toggleText = element("span");
    toggleLabel.append(toggle, track, toggleText);
    toggle.onchange = () => {
      const enabled = toggle.checked;
      return operation(item.id, () =>
        window.pywebview.api.set_plugin_enabled(item.id, enabled),
      );
    };
    const configure = element("button");
    configure.dataset.pluginConfigure = item.id;
    configure.onclick = guard(async () => {
      const current = items.find((x) => x.id === item.id);
      const adapter = adapters.get(item.id);
      if (!adapter) throw Error("Настройки этого плагина недоступны");
      if (!current.installed && !current.busy) await adapter.install();
      else await adapter.configure();
      await refresh();
    });
    const stop = element("button", "Остановить");
    stop.dataset.pluginStop = item.id;
    stop.onclick = guard(async () => {
      await adapters.get(item.id)?.stop();
      await refresh();
    });
    actions.append(toggleLabel, configure, stop);
    row.append(favorite, info, actions);
    return {
      row,
      favorite,
      title,
      description,
      status,
      message,
      toggle,
      toggleText,
      configure,
      stop,
    };
  }
  function updateRows() {
    for (const item of items) {
      if (!rows.has(item.id)) rows.set(item.id, makeRow(item));
      const node = rows.get(item.id);
      setText(node.title, item.name);
      setText(node.description, item.description);
      setText(node.status, item.state_label);
      node.status.dataset.state = item.state;
      setText(
        node.message,
        item.message ||
          (item.installed
            ? item.category + " · v" + item.version
            : item.category),
      );
      node.favorite.setAttribute(
        "aria-label",
        (item.favorite ? "Убрать из избранного: " : "В избранное: ") +
          item.name,
      );
      node.favorite.setAttribute("aria-pressed", String(item.favorite));
      setText(node.favorite, item.favorite ? "★" : "☆");
      node.favorite.disabled = pending.has(item.id);
      node.toggle.checked = item.enabled;
      node.toggle.disabled = !item.can_toggle || pending.has(item.id);
      setText(node.toggleText, item.enabled ? "Вкл." : "Выкл.");
      node.toggle.title = item.can_toggle
        ? "Изменение сохраняется сразу"
        : "Сначала установите плагин";
      setText(
        node.configure,
        !item.installed && !item.busy ? "Установить" : "Настроить",
      );
      node.configure.disabled = pending.has(item.id) || !adapters.has(item.id);
      node.configure.classList.toggle("primary", !item.installed && !item.busy);
      node.stop.hidden = !item.busy;
    }
  }
  function renderList() {
    updateRows();
    const query = search.value.trim().toLocaleLowerCase("ru");
    const predicates = {
      all: () => true,
      installed: (x) => x.installed,
      enabled: (x) => x.enabled,
      disabled: (x) => x.installed && !x.enabled,
      attention: (x) => x.attention,
      favorites: (x) => x.favorite,
    };
    const selected = items
      .filter(
        (x) =>
          predicates[filter](x) &&
          (!category.value || x.category === category.value) &&
          `${x.name} ${x.description} ${x.category}`
            .toLocaleLowerCase("ru")
            .includes(query),
      )
      .sort((a, b) => a.name.localeCompare(b.name, "ru"));
    const next = JSON.stringify(selected.map((x) => x.id));
    if (next !== signature) {
      const wasRow = document.activeElement?.closest(".plugin-row");
      list.replaceChildren(...selected.map((x) => rows.get(x.id).row));
      signature = next;
      if (wasRow && !wasRow.isConnected) search.focus({ preventScroll: true });
    }
    for (const button of filters.children) {
      const key = button.dataset.pluginFilter;
      button.setAttribute("aria-pressed", String(key === filter));
      setText(
        button,
        `${names[key]} · ${items.filter(predicates[key]).length}`,
      );
    }
    empty.hidden = selected.length > 0;
    setText(count, `Показано ${selected.length} из ${items.length}`);
    setText(enabledCount, items.filter((x) => x.enabled).length);
  }
  function accept(value) {
    items = value.plugins;
    const categories = [...new Set(items.map((x) => x.category))].sort();
    if (category.dataset.categories !== JSON.stringify(categories)) {
      const previous = category.value;
      const option = element("option", "Все категории");
      option.value = "";
      category.replaceChildren(
        option,
        ...categories.map((name) => {
          const option = element("option", name);
          option.value = name;
          return option;
        }),
      );
      category.value = categories.includes(previous) ? previous : "";
      category.dataset.categories = JSON.stringify(categories);
    }
    for (const [identity, node] of rows)
      if (!items.some((x) => x.id === identity)) {
        node.row.remove();
        rows.delete(identity);
      }
    renderList();
    studioLifecycle.notify("pluginsChanged", { plugins: items });
  }
  async function refresh() {
    if (!window.pywebview?.api?.get_plugins || pending.size) return;
    const own = ++requestId;
    const value = await window.pywebview.api.get_plugins();
    if (own === requestId && !pending.size) accept(value);
  }
  function navigate() {
    const active = studioState.viewMode === "plugins";
    if (opened && !active) savedScroll = center.scrollTop;
    opened = active;
    pane.hidden = !active;
    document.body.dataset.appView = active ? "plugins" : "project";
    entry.setAttribute("aria-current", active ? "page" : "false");
    if (active) {
      setText("fileTitle", "Плагины");
      setText("breadcrumb", "ПРИЛОЖЕНИЕ");
      setText(
        "description",
        "Установка, быстрые переключатели и настройки расширений",
      );
    }
  }
  async function open(identity) {
    if (studioState.dirty.size)
      throw Error("Сохраните или отмените правки перед переходом");
    if (studioState.viewMode !== "plugins") {
      showView("plugins");
      center.scrollTop = savedScroll;
    }
    await refresh();
    search.focus({ preventScroll: true });
    if (identity) await adapters.get(identity)?.configure();
  }
  entry.onclick = guard(() => open());
  search.oninput = category.onchange = renderList;
  studioLifecycle.register("navigate", "pluginCatalog", navigate);
  studioLifecycle.register("refresh", "pluginCatalog", () => {
    navigate();
  });
  setInterval(() => {
    if (studioState.viewMode === "plugins")
      refresh().catch((error) => {
        setText(feedback, error.message);
        feedback.hidden = false;
      });
  }, 2500);
  window.addEventListener("pywebviewready", () => refresh().catch(() => {}));
  return {
    open,
    refresh,
    register: (id, adapter) => {
      if (adapters.has(id)) throw Error("Duplicate plugin adapter");
      adapters.set(id, adapter);
    },
  };
})();

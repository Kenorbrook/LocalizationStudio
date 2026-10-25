export function create(api) {
  const events = window.__extensionEvents ||= {ticks:0, clicks:0, disposals:0};
  const version = "__REVISION__";
  const node = document.createElement("button");
  node.id = "qaPluginNavigation";
  node.textContent = "Тест расширения " + version;
  api.mount("app.navigation", node);
  const tab = document.createElement("p");
  tab.id = "qaPluginTabContent";
  tab.textContent = "Вкладка расширения " + version;
  api.projectTab("inspector", "Расширение", tab);
  api.style(`body { --qa-plugin-version:${version}; } body > header { background:__COLOR__; }`);
  api.recordActions(row => {
    const action = document.createElement("button");
    action.dataset.qaRecordAction = row.id;
    action.textContent = "Плагин";
    return action;
  });
  api.listen(window, "qa-plugin-event", () => events.clicks++);
  api.interval(() => events.ticks++, 250);
  api.lifecycle.register("refresh", "example", () => {
    if (window.__extensionThrow) throw Error("Neutral isolated plugin error");
  });
  api.onDispose(() => events.disposals++);
  return {};
}

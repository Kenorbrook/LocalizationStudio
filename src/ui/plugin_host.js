"use strict";
const pluginHost = (() => {
  const instances = new Map(),
    catalog = new Map(),
    errors = new Map(),
    failed = new Map();
  let serial = Promise.resolve();
  const slots = {
    "app.navigation": () => document.querySelector(".ux-workspace-nav"),
    "app.footer": () => appFooter,
    "app.toolbar": () => document.querySelector("header"),
    "app.main": () => center,
    "app.dialogs": () => document.body,
    "app.settings": () => $("updatesBody"),
    "project.toolbar": () => document.querySelector(".center .toolbar"),
    "project.activity": () => document.querySelector("aside.right"),
    "project.overview": () =>
      $("projectHome").querySelector(".ux-overview-grid"),
    "project.errors.actions": () => errorsPane,
    ...Object.fromEntries(
      ["translation", "context", "files", "integrations", "advanced"].map(
        (key) => ["project.settings." + key, () => $("settingsPanel-" + key)],
      ),
    ),
  };
  function scope(item) {
    let disposed = false,
      enabled = !!item.enabled;
    const cleanup = [],
      tabs = [],
      mounts = [],
      styles = [];
    const namespace = `plugin:${item.id}:${item.generation}:`;
    function own(dispose) {
      cleanup.push(dispose);
      return dispose;
    }
    function fail(error) {
      if (disposed) return;
      errors.set(item.id, error.message || String(error));
      toast(item.name + ": " + errors.get(item.id));
    }
    function safe(callback) {
      return (...args) => {
        if (disposed) return;
        try {
          return Promise.resolve(callback(...args)).catch(fail);
        } catch (error) {
          fail(error);
        }
      };
    }
    function attach(slot, node) {
      const target = slots[slot]?.();
      if (!target) throw Error("Точка подключения недоступна: " + slot);
      target.append(node);
    }
    const api = {
      version: 1,
      get enabled() {
        return enabled;
      },
      get disposed() {
        return disposed;
      },
      ui: {
        element,
        guard,
        setText,
        toast,
        $,
        workspaceUi,
        studioState,
        pluginsUi: { refresh: () => pluginsUi.refresh() },
      },
      project: () => studioState.project,
      refreshProject: () => window.refresh(),
      call: (command, ...args) => {
        if (disposed) return Promise.reject(Error("Плагин выгружен"));
        return window.pywebview.api.plugin_call(item.id, command, args);
      },
      configure: (values) =>
        window.pywebview.api.configure_plugin(item.id, values),
      setEnabled: (enabled) =>
        window.pywebview.api.set_plugin_enabled(item.id, enabled),
      asset: (name) => {
        if (!/^[a-zA-Z0-9_./-]+$/.test(name) || name.split("/").includes(".."))
          throw Error("Некорректный ресурс");
        return `/plugins/${item.id}/${item.generation}/${name}`;
      },
      projectTab(key, title, node) {
        if (!/^[a-z][a-z0-9-]*$/.test(key))
          throw Error("Некорректное имя вкладки");
        const descriptor = {
          key: item.id + "-" + key,
          title,
          node,
          remove: null,
        };
        tabs.push(descriptor);
        if (enabled)
          descriptor.remove = workspaceUi.registerTab(
            descriptor.key,
            title,
            node,
          );
        own(() => descriptor.remove?.());
      },
      recordActions(factory) {
        const nodes = new Map();
        const draw = () => {
          if (!enabled || disposed) return;
          for (const [key, node] of nodes)
            if (!node.isConnected) nodes.delete(key);
          for (const row of studioState.rows) {
            const card = document.querySelector(`.record[data-id="${row.id}"]`);
            if (!card || nodes.has(row.id)) continue;
            const node = factory(row);
            card.querySelector(".record-actions").append(node);
            nodes.set(row.id, node);
          }
        };
        api.lifecycle.register("render", "recordActions", draw);
        if (enabled) draw();
        own(() => {
          for (const node of nodes.values()) node.remove();
        });
      },
      mount(slot, node) {
        if (!slots[slot]) throw Error("Неизвестная точка подключения: " + slot);
        mounts.push({ slot, node });
        if (enabled || slot === "app.dialogs") attach(slot, node);
        own(() => {
          if (node.open && node.close) node.close();
          node.remove();
        });
        return node;
      },
      style(css) {
        if (!item.permissions?.includes("theme"))
          throw Error("Плагин не объявил разрешение theme");
        const node = document.createElement("style");
        node.dataset.pluginStyle = item.id;
        node.textContent = css;
        styles.push(node);
        if (enabled) document.head.append(node);
        own(() => node.remove());
        return node;
      },
      listen(target, event, callback, options) {
        const listener = safe(callback);
        target.addEventListener(event, listener, options);
        return own(() => target.removeEventListener(event, listener, options));
      },
      interval(callback, ms) {
        const timer = setInterval(
          safe(() => {
            if (
              enabled ||
              mounts.some((x) => x.slot === "app.dialogs" && x.node.open)
            )
              return callback();
          }),
          Math.max(250, ms),
        );
        return own(() => clearInterval(timer));
      },
      lifecycle: {
        register: (phase, name, callback) =>
          own(
            studioLifecycle.register(phase, namespace + name, safe(callback)),
          ),
        registerPage: (mode, descriptor) =>
          own(studioLifecycle.registerPage(namespace + mode, descriptor)),
      },
      onDispose: own,
    };
    return {
      api,
      activate() {
        enabled = true;
        for (const tab of tabs)
          if (!tab.remove)
            tab.remove = workspaceUi.registerTab(tab.key, tab.title, tab.node);
        for (const { slot, node } of mounts)
          if (!node.isConnected) attach(slot, node);
        for (const node of styles)
          if (!node.isConnected) document.head.append(node);
      },
      dispose() {
        disposed = true;
        enabled = false;
        for (const dispose of cleanup.reverse()) {
          try {
            dispose();
          } catch {}
        }
      },
    };
  }
  async function ensure(item) {
    let current = instances.get(item.id);
    if (current && current.generation !== item.generation) {
      current.scope.dispose();
      instances.delete(item.id);
      current = null;
    }
    if (current) return current;
    if (!item.ui_url) throw Error("У плагина нет интерфейса");
    const owned = scope(item);
    try {
      const module = await import(item.ui_url);
      const instance = await module.create(owned.api);
      if (instance.dispose) owned.api.onDispose(() => instance.dispose());
      current = { generation: item.generation, scope: owned, instance };
      instances.set(item.id, current);
      errors.delete(item.id);
      failed.delete(item.id);
      return current;
    } catch (error) {
      owned.dispose();
      errors.set(item.id, error.message);
      failed.set(item.id, item.generation);
      throw error;
    }
  }
  async function sync(items) {
    const wanted = new Set(items.map((x) => x.id));
    for (const [key, current] of instances) {
      const next = items.find((x) => x.id === key),
        previous = catalog.get(key);
      if (
        !wanted.has(key) ||
        next.generation !== current.generation ||
        (previous?.enabled && !next.enabled)
      ) {
        current.scope.dispose();
        instances.delete(key);
      }
    }
    catalog.clear();
    for (const item of items) {
      catalog.set(item.id, item);
      if (!item.enabled) failed.delete(item.id);
      if (
        item.enabled &&
        item.ui_url &&
        failed.get(item.id) !== item.generation
      ) {
        try {
          (await ensure(item)).scope.activate();
        } catch (error) {
          console.error(item.id, error);
        }
      }
    }
  }
  studioLifecycle.register("pluginsChanged", "pluginHost", ({ plugins }) => {
    serial = serial
      .then(() => sync(plugins))
      .catch((error) => toast(error.message));
  });
  async function action(key, command) {
    await serial;
    const item = catalog.get(key);
    if (!item) throw Error("Плагин не найден");
    if (command === "stop" && !instances.has(key))
      return window.pywebview.api.plugin_call(key, "cancel", []);
    const current = await ensure(item);
    const callback = current.instance[command];
    if (!callback) {
      if (command === "configure") {
        toast("У этого плагина нет дополнительных настроек");
        return;
      }
      throw Error("Действие недоступно");
    }
    return callback();
  }
  return {
    action,
    ready: () => serial,
    instance: (key) => instances.get(key)?.instance,
    diagnostics: () => ({
      loaded: [...instances.keys()],
      errors: Object.fromEntries(errors),
    }),
  };
})();

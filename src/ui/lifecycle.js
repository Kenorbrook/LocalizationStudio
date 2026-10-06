"use strict";
const studioLifecycle = (() => {
  const hooks = new Map();
  function register(phase, name, callback) {
    const callbacks = hooks.get(phase) || new Map();
    if (callbacks.has(name))
      throw new Error(`Duplicate ${phase} hook: ${name}`);
    callbacks.set(name, callback);
    hooks.set(phase, callbacks);
  }
  async function refresh(context) {
    for (const callback of hooks.get("refresh")?.values() || [])
      await callback(context);
  }
  function render(data, quiet) {
    for (const callback of hooks.get("render")?.values() || [])
      callback(data, quiet);
  }
  function settings(values) {
    for (const callback of hooks.get("settings")?.values() || [])
      Object.assign(values, callback());
    return values;
  }
  const pages = new Map();
  function registerPage(mode, descriptor) {
    if (pages.has(mode)) throw new Error(`Duplicate record page: ${mode}`);
    pages.set(mode, descriptor);
  }
  function page(mode) {
    return pages.get(mode)?.();
  }
  return { register, refresh, render, settings, registerPage, page };
})();

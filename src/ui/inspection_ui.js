"use strict";
const studioInspection = (() => {
  const sensitive = (el) =>
    el.type === "password" ||
    /api.?key|token|secret|password/i.test(el.id + " " + el.name);
  const visible = (el) =>
    !!(
      el.getClientRects().length && getComputedStyle(el).visibility !== "hidden"
    );
  const rect = (el) => {
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, width: r.width, height: r.height };
  };
  function snapshot() {
    const secrets = [...document.querySelectorAll("input,textarea")]
      .filter(sensitive)
      .map((el) => el.value)
      .filter(Boolean);
    const redact = (text) =>
      secrets.reduce(
        (result, value) => result.split(value).join("[redacted]"),
        text,
      );
    const elements = [
      ...document.querySelectorAll("button,input,select,textarea,a,[role=tab]"),
    ].filter(visible);
    const controls = elements.map((el) => ({
      id: el.id,
      tag: el.tagName.toLowerCase(),
      type: el.type || "",
      label: redact(
        el.getAttribute("aria-label") ||
          document.querySelector(`label[for="${CSS.escape(el.id)}"]`)
            ?.innerText ||
          el.innerText ||
          el.placeholder ||
          "",
      ),
      value: sensitive(el)
        ? "[redacted]"
        : el.type === "file"
          ? "[file selection]"
          : el.value == null
            ? null
            : redact(el.value),
      checked: el.matches("input[type=checkbox],input[type=radio]")
        ? el.checked
        : undefined,
      disabled: !!el.disabled,
      options:
        el.tagName === "SELECT"
          ? [...el.options].map((o) => ({
              value: redact(o.value),
              label: redact(o.label),
              disabled: o.disabled,
            }))
          : undefined,
      bounds: rect(el),
    }));
    const text = redact(document.body.innerText);
    return {
      observed_at: new Date().toISOString(),
      title: redact(document.title),
      viewport: {
        width: innerWidth,
        height: innerHeight,
        scale: devicePixelRatio,
      },
      project: studioState.project,
      file: studioState.file,
      view: studioState.viewMode,
      process_tab: studioState.processTab,
      dirty_records: [...studioState.dirty],
      dialogs: [...document.querySelectorAll("dialog[open]")].map((el) => ({
        id: el.id,
        text: redact(el.innerText),
      })),
      focused_control: document.activeElement?.id || null,
      text: text.slice(0, 150000),
      text_truncated: text.length > 150000,
      controls,
      screenshot_available:
        !elements.some(
          (el) => sensitive(el) && el.type !== "password" && el.value,
        ) && !secrets.some((value) => document.body.innerText.includes(value)),
    };
  }
  return { snapshot };
})();

// Keep editing commands available in the Windows release WebView.
let textMenu;
function dismissTextMenu() {
  textMenu?.remove();
  textMenu = null;
}
document.addEventListener(
  "pointerdown",
  (event) => {
    if (!textMenu?.contains(event.target)) dismissTextMenu();
  },
  true,
);
document.addEventListener(
  "keydown",
  (event) => {
    if (event.key === "Escape" && textMenu) {
      event.preventDefault();
      event.stopImmediatePropagation();
      dismissTextMenu();
    }
  },
  true,
);
document.addEventListener("scroll", dismissTextMenu, true);
document.addEventListener("contextmenu", (event) => {
  dismissTextMenu();
  const field = event.target.closest(
    "textarea,input:not([type]),input[type=text],input[type=search],input[type=url],input[type=email],input[type=password]",
  );
  const selection = window.getSelection();
  const selectedText = !!selection?.toString();
  if (!field && !selectedText) return;
  if (field && typeof field.selectionStart !== "number") return;
  event.preventDefault();
  if (field) field.focus({ preventScroll: true });
  const start = field?.selectionStart ?? 0;
  const end = field?.selectionEnd ?? 0;
  const editable = field && !field.readOnly && !field.disabled;
  const visibleSelection = field
    ? end > start && field.type !== "password"
    : selectedText;
  const menu = element("div", undefined, "text-menu");
  menu.setAttribute("role", "menu");
  const add = (label, enabled, act) => {
    const button = element("button", label);
    button.type = "button";
    button.setAttribute("role", "menuitem");
    button.disabled = !enabled;
    button.onpointerdown = (e) => e.preventDefault();
    button.onclick = guard(async () => {
      dismissTextMenu();
      if (field) {
        field.focus({ preventScroll: true });
        field.setSelectionRange(start, end);
      }
      await act();
    });
    menu.append(button);
  };
  if (editable)
    add("Вырезать", visibleSelection, () => document.execCommand("cut"));
  add("Копировать", visibleSelection, () => document.execCommand("copy"));
  if (editable)
    add("Вставить", true, async () => {
      const native = window.pywebview?.api;
      const value = native?.clipboard_text
        ? await native.clipboard_text()
        : await navigator.clipboard.readText();
      if (!value || !field.isConnected || document.activeElement !== field)
        return;
      if (!document.execCommand("insertText", false, value)) {
        field.setRangeText(value, start, end, "end");
        field.dispatchEvent(new Event("input", { bubbles: true }));
      }
    });
  if (field) add("Выделить всё", !!field.value, () => field.select());
  const dialog = event.target.closest("dialog[open]");
  (dialog || document.body).append(menu);
  textMenu = menu;
  const anchor = field?.getBoundingClientRect();
  const x = event.clientX || anchor?.left || 0;
  const y = event.clientY || anchor?.bottom || 0;
  menu.style.left =
    Math.max(4, Math.min(x, innerWidth - menu.offsetWidth - 4)) + "px";
  menu.style.top =
    Math.max(4, Math.min(y, innerHeight - menu.offsetHeight - 4)) + "px";
});

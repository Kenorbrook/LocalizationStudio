export function create(api) {
  api.style(`
    :root { --bg:#f7f4ec; --panel:#fffdf7; --line:#b5b3a6; --ink:#202820; --muted:#536350; --accent:#176b54; --yellow:#775500; color-scheme:light; }
    html body, html body > header, html body main > aside, html body dialog { background:var(--bg); color:var(--ink); }
    html body :is(input, select, textarea, pre, .record, .ux-overview-card, .plugin-row,
    .ux-settings-group, .ux-workspace-nav button, .right > section) { background:var(--panel); color:var(--ink); border-color:var(--line); }
    html body button { background:#e4e8d9; color:var(--ink); border-color:var(--line); }
    html body button.primary { background:#176b54; color:white; }
    .sub, small { color:var(--muted); }
  `);
  return {};
}

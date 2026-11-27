// Connections live in application settings. Jobs retain the chosen connection and model.
(() => {
  const cloudPanel = element("section", undefined, "app-setting-row");
  cloudPanel.id = "cloudConnections";
  const cloudList = element("div", undefined, "cloud-list");
  const catalogPopup = settingsPopup(
    "cloudCatalogDialog",
    "API-ключи",
    cloudList,
  );
  const openCloudSettings = element("button", "Настроить API-ключи");
  const connectionPicker = element("select");
  connectionPicker.id = "cloudConnectionPicker";
  const modelList = element("select");
  modelList.id = "cloudModelOptions";
  modelList.setAttribute("aria-label", "Модель облачного API");
  const customModel = "__manual__";
  const cloudModel = $("cloud_model");
  cloudModel.placeholder = "Выберите модель или введите её ID";
  const connectionIdentity = element("input");
  connectionIdentity.id = "cloud_connection";
  connectionIdentity.type = "hidden";
  const connectionLabel = element("label", "Подключение");
  connectionLabel.htmlFor = connectionPicker.id;
  const refreshModels = element("button", "Обновить список моделей");
  const modelStatus = element("p", undefined, "sub");
  modelStatus.setAttribute("role", "status");
  const legacyFields = [
    $("endpoint"),
    $("endpoint").previousElementSibling,
    $("apiKey"),
    $("apiKey").previousElementSibling,
    cloudConnection.querySelector("p.sub"),
  ];
  for (const field of legacyFields) field.hidden = true;
  cloudConnection
    .querySelector("summary")
    .after(
      connectionLabel,
      connectionPicker,
      openCloudSettings,
      connectionIdentity,
    );
  const modelFields = element("div", undefined, "ux-field");
  modelFields.id = "executorModelFields";
  const modelLabel = element("label", "Модель API");
  modelLabel.htmlFor = modelList.id;
  cloudModel.previousElementSibling.hidden = true;
  modelFields.append(modelLabel, modelList, cloudModel);
  executionModel.closest(".ux-field").after(modelFields);
  connectionPicker.after(refreshModels, modelStatus);

  const connectionEditor = element("form", undefined, "cloud-editor");
  const editorPopup = settingsPopup(
    "cloudEditorDialog",
    "Добавить API-ключ",
    connectionEditor,
  );
  const connectionName = element("input");
  const connectionEndpoint = element("input");
  connectionEndpoint.type = "url";
  connectionEndpoint.placeholder = "https://provider.example/v1";
  const connectionKey = element("input");
  connectionKey.type = "password";
  connectionKey.autocomplete = "new-password";
  const allProjects = element("input");
  allProjects.type = "checkbox";
  const projectScopes = element("div", undefined, "cloud-scopes");
  const keyField = element("label", "API-ключ");
  keyField.append(connectionKey);
  const editorActions = element("div", undefined, "flex wrap");
  const saveConnection = element("button", "Сохранить подключение", "primary");
  saveConnection.type = "submit";
  const cancelConnection = element("button", "Отмена");
  cancelConnection.type = "button";
  cancelConnection.onclick = () => editorPopup.close();
  editorPopup.addEventListener("close", () => (connectionKey.value = ""));
  editorActions.append(saveConnection, cancelConnection);
  const nameField = element("label", "Название");
  nameField.append(connectionName);
  const endpointField = element("label", "Адрес API");
  endpointField.append(connectionEndpoint);
  const scopeField = element("label", undefined, "update-option");
  scopeField.append(allProjects, document.createTextNode(" Все проекты"));
  allProjects.onchange = () => (projectScopes.hidden = allProjects.checked);
  connectionEditor.append(
    nameField,
    endpointField,
    keyField,
    scopeField,
    projectScopes,
    editorActions,
  );
  const addConnection = element("button", "Добавить API-ключ");
  addConnection.id = "addCloudConnection";
  const manageConnections = element("button", "Настроить API-ключи");
  manageConnections.id = "manageCloudConnections";
  const catalogActions = element("div", undefined, "app-setting-actions");
  catalogActions.append(addConnection, manageConnections);
  cloudPanel.append(element("h3", "API-ключи"), catalogActions);
  updatesBody.prepend(cloudPanel);
  let cloudCatalog = [];
  let cloudProject = 0;
  let loadedCloudSelection = false;
  let editConnectionId = "";
  let projectCloudVersion = "";
  const apiBridge = () => window.pywebview?.api;

  function openConnectionEditor(item) {
    editConnectionId = item?.id || "";
    setText(
      $("cloudEditorDialogTitle"),
      item ? "Редактировать API-ключ" : "Добавить API-ключ",
    );
    connectionName.value = item?.name || "";
    connectionEndpoint.value = item?.endpoint || "";
    connectionEndpoint.readOnly = !!item;
    connectionKey.value = "";
    keyField.hidden = !!item;
    allProjects.checked = !item?.projects;
    projectScopes.hidden = allProjects.checked;
    const available = new Map(
      (studioState.snapshot?.projects || []).map((p) => [p.id, p.name]),
    );
    for (const id of item?.projects || []) {
      if (!available.has(id)) available.set(id, "Проект #" + id);
    }
    projectScopes.replaceChildren(
      ...[...available].map(([id, name]) => {
        const label = element("label", undefined, "update-option");
        const input = element("input");
        input.type = "checkbox";
        input.value = id;
        input.checked = item?.projects?.includes(id) === true;
        label.append(input, document.createTextNode(" " + name));
        return label;
      }),
    );
    editorPopup.showModal();
    connectionName.focus();
  }
  addConnection.onclick = () => openConnectionEditor();
  function catalogItem(identity = connectionIdentity.value) {
    return cloudCatalog.find((item) => item.id === identity);
  }
  function modelsFor(item, models) {
    item.models = models;
    if (item.id === connectionIdentity.value) {
      if (!cloudModel.value && models.length === 1)
        cloudModel.value = models[0];
      renderModelOptions();
      updateExecutionChoices();
      updateTaskActions();
    }
  }
  async function fetchCloudModels(item) {
    if (!item) return;
    const models = await apiBridge().discover_cloud_models(item.id);
    modelsFor(item, models);
    renderCloudCatalog();
    return models;
  }
  function renderCloudCatalog() {
    const names = new Map(
      (studioState.snapshot?.projects || []).map((p) => [p.id, p.name]),
    );
    cloudList.replaceChildren(
      ...cloudCatalog.map((item) => {
        const card = element("article", undefined, "cloud-entry");
        const title = element("strong", item.name);
        const scope = item.projects
          ? item.projects
              .map((id) => names.get(id) || "Проект #" + id)
              .join(", ")
          : "Все проекты";
        const actions = element("div", undefined, "flex wrap");
        const copy = element("button", "Скопировать ключ");
        copy.onclick = guard(async () => {
          await apiBridge().copy_cloud_key(item.id);
          toast("Ключ скопирован");
        });
        const models = element("button", "Обновить модели");
        models.onclick = guard(async () => {
          models.disabled = true;
          try {
            await fetchCloudModels(item);
          } finally {
            models.disabled = false;
          }
        });
        actions.append(copy, models);
        const edit = element("button", "Редактировать");
        edit.onclick = () => openConnectionEditor(item);
        if (!item.legacy) actions.append(edit);
        card.append(
          title,
          element("div", item.endpoint, "sub"),
          element("div", "•••••••• · " + scope, "sub"),
          element(
            "div",
            item.models.join(", ") || "Список моделей ещё не загружен",
            "sub",
          ),
          actions,
        );
        return card;
      }),
    );
    if (!cloudCatalog.length)
      cloudList.append(element("p", "Нет API-ключей", "sub"));
  }
  function renderModelOptions() {
    const item = catalogItem();
    const models = item?.models || [];
    const manual = modelList.value === customModel;
    modelList.replaceChildren();
    for (const [value, label] of [
      ["", "Выберите модель"],
      ...models.map((model) => [model, model]),
      [customModel, "Указать другой ID…"],
    ]) {
      const option = element("option", label);
      option.value = value;
      modelList.append(option);
    }
    modelList.hidden = !models.length;
    modelList.value =
      (!cloudModel.value && !manual) || models.includes(cloudModel.value)
        ? cloudModel.value
        : customModel;
    cloudModel.hidden = !!models.length && modelList.value !== customModel;
    refreshModels.disabled = !item;
    setText(
      modelStatus,
      models.length
        ? countLabel(models.length, "модель", "модели", "моделей")
        : "",
    );
  }
  modelList.onchange = () => {
    const manual = modelList.value === customModel;
    cloudModel.hidden = !manual;
    cloudModel.value = manual ? "" : modelList.value;
    updateExecutionChoices();
    updateTaskActions();
    if (manual) cloudModel.focus();
  };
  function renderProjectCloud() {
    const eligible = cloudCatalog.filter(
      (c) => !c.projects || c.projects.includes(studioState.project),
    );
    const signature = JSON.stringify([
      eligible,
      connectionIdentity.value,
      cloudModel.value,
    ]);
    if (signature === projectCloudVersion) return;
    projectCloudVersion = signature;
    connectionPicker.replaceChildren(
      element("option", "Выберите подключение"),
      ...eligible.map((item) => {
        const option = element("option", item.name);
        option.value = item.id;
        return option;
      }),
    );
    connectionPicker.options[0].value = "";
    connectionPicker.value = eligible.some(
      (c) => c.id === connectionIdentity.value,
    )
      ? connectionIdentity.value
      : "";
    renderModelOptions();
  }
  async function loadCloudCatalog() {
    if (!apiBridge()?.cloud_connections) return;
    cloudCatalog = await apiBridge().cloud_connections();
    if (!connectionIdentity.value && $("endpoint").value) {
      const legacyId = "legacy:" + studioState.project;
      if (catalogItem(legacyId)) connectionIdentity.value = legacyId;
    }
    renderCloudCatalog();
    renderProjectCloud();
    studioLifecycle.notify("cloudCatalog", cloudCatalog);
  }
  connectionEditor.onsubmit = guard(async (event) => {
    event.preventDefault();
    const editing = !!editConnectionId;
    saveConnection.disabled = true;
    try {
      const identity = await apiBridge().save_cloud_connection({
        id: editConnectionId || null,
        name: connectionName.value,
        endpoint: connectionEndpoint.value,
        key: connectionKey.value,
        projects: allProjects.checked
          ? null
          : [...projectScopes.querySelectorAll("input:checked")].map((input) =>
              Number(input.value),
            ),
      });
      editorPopup.close();
      await loadCloudCatalog();
      if (!editing) await fetchCloudModels(catalogItem(identity));
    } finally {
      saveConnection.disabled = false;
    }
  });
  const openCatalog = guard(async () => {
    if (!apiBridge()?.cloud_connections)
      throw Error("Откройте настройки в приложении");
    renderCloudCatalog();
    if (!catalogPopup.open) catalogPopup.showModal();
    await loadCloudCatalog();
  });
  openCloudSettings.onclick = manageConnections.onclick = openCatalog;
  const selectConnection = guard(async (identity) => {
    if (connectionIdentity.value === identity) return;
    connectionIdentity.value = identity;
    modelList.value = "";
    const item = catalogItem();
    $("endpoint").value = item?.endpoint || "";
    cloudModel.value = item?.models.length === 1 ? item.models[0] : "";
    renderProjectCloud();
    updateExecutionChoices();
    updateTaskActions();
    if (item && !cloudModel.value && !$("executorModelFields").hidden)
      (modelList.hidden ? cloudModel : modelList).focus();
    if (item && !item.models.length) await fetchCloudModels(item);
  });
  connectionPicker.onchange = () => selectConnection(connectionPicker.value);
  studioLifecycle.register(
    "selectCloudConnection",
    "cloudExecutor",
    selectConnection,
  );
  refreshModels.onclick = guard(async () => {
    refreshModels.disabled = true;
    try {
      await fetchCloudModels(catalogItem());
    } finally {
      renderModelOptions();
    }
  });
  cloudModel.addEventListener("input", () => {
    updateExecutionChoices();
    updateTaskActions();
  });
  studioLifecycle.register("settings", "cloudConnectionChoice", () => ({
    cloud_connection: connectionIdentity.value,
  }));
  studioLifecycle.register(
    "projectCloudSelection",
    "cloudSelection",
    renderProjectCloud,
  );
  studioLifecycle.register(
    "refresh",
    "projectCloudConnections",
    async ({ initial }) => {
      if (
        !initial &&
        cloudProject === studioState.project &&
        loadedCloudSelection
      )
        return;
      if (initial || cloudProject !== studioState.project) {
        cloudProject = studioState.project;
        const project = studioState.snapshot.projects.find(
          (p) => p.id === cloudProject,
        );
        connectionIdentity.value =
          JSON.parse(project?.settings || "{}").cloud_connection || "";
      }
      if (apiBridge()?.cloud_connections) {
        loadedCloudSelection = true;
        await loadCloudCatalog();
      }
    },
  );
})();

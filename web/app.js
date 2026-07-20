(() => {
  "use strict";

  const state = {
    ready: false,
    config: {},
    runtime: {},
    logs: [],
    latestLogId: 0,
    notificationTimer: null,
    uiWired: false,
    bridgeInitializing: false,
    bridgeFailed: false,
    bridgeWaitTimer: null,
  };

  const integerFields = new Set([
    "sap_port",
    "sap_language",
    "local_port",
    "request_timeout_seconds",
    "max_response_mb",
  ]);
  const integerFieldLabels = {
    sap_port: "SAP port",
    sap_language: "SAP language",
    local_port: "Local proxy port",
    request_timeout_seconds: "Request timeout",
    max_response_mb: "Maximum response size",
  };
  const booleanFields = new Set(["sap_verify_ssl", "start_tunnel"]);
  const configForms = [
    document.getElementById("connectionForm"),
    document.getElementById("tunnelForm"),
  ];
  const bridgeControlIds = [
    "startButton",
    "stopButton",
    "testConnectionButton",
    "refreshButton",
    "rotateApiKeyButton",
    "startupButton",
    "browseCertificateButton",
    "generateCodeButton",
  ];

  function api() {
    return window.pywebview && window.pywebview.api;
  }

  async function callApi(method, ...args) {
    if (!api() || typeof api()[method] !== "function") {
      throw new Error("Desktop bridge is unavailable");
    }
    const result = await api()[method](...args);
    if (!result || result.ok === false) {
      throw new Error((result && result.error) || "Desktop operation failed");
    }
    return result;
  }

  function showNotification(message, isError = false) {
    const element = document.getElementById("notification");
    window.clearTimeout(state.notificationTimer);
    element.textContent = message;
    element.classList.toggle("error", isError);
    element.classList.add("visible");
    state.notificationTimer = window.setTimeout(() => element.classList.remove("visible"), 4200);
  }

  function setSection(sectionId) {
    document.querySelectorAll(".view").forEach((view) => {
      view.classList.toggle("active", view.id === sectionId);
    });
    document.querySelectorAll(".nav-button").forEach((button) => {
      button.classList.toggle("active", button.dataset.section === sectionId);
    });
  }

  function populateConfig(config) {
    state.config = { ...config };
    document.querySelectorAll("[name]").forEach((element) => {
      const key = element.name;
      if (!(key in config)) {
        return;
      }
      if (element.type === "checkbox") {
        element.checked = Boolean(config[key]);
      } else {
        element.value = config[key] == null ? "" : config[key];
      }
    });
    document.getElementById("powerBiEntity").value = config.default_entity || "Invoices";
    document.getElementById("powerBiSelect").value = config.default_select || "";
    updateTlsText();
  }

  function collectConfig() {
    const payload = { ...state.config };
    document.querySelectorAll("[name]").forEach((element) => {
      const key = element.name;
      if (element.type === "checkbox") {
        payload[key] = element.checked;
      } else if (integerFields.has(key)) {
        const rawValue = element.value.trim();
        const label = integerFieldLabels[key] || "Value";
        if (!rawValue) {
          throw new Error(`${label} is required`);
        }
        const value = Number(rawValue);
        if (!Number.isInteger(value)) {
          throw new Error(`${label} must be an integer`);
        }
        payload[key] = value;
      } else {
        payload[key] = element.value;
      }
    });
    return payload;
  }

  async function saveConfig(showMessage = true) {
    if (!state.ready) {
      throw new Error("Application is still loading. Please wait and try again.");
    }
    if (state.runtime.busy) {
      throw new Error("Wait for the current operation to finish before saving settings.");
    }
    if (state.runtime.running) {
      throw new Error("Stop the proxy before changing settings.");
    }
    const result = await callApi("save_config", collectConfig());
    populateConfig(result.result || result.config || collectConfig());
    if (showMessage) {
      showNotification("Settings saved");
    }
    return result;
  }

  function setMetric(prefix, online, onlineText, offlineText, busy = false) {
    const value = document.getElementById(`${prefix}Metric`);
    const badge = document.getElementById(`${prefix}MetricState`);
    value.textContent = busy ? "Working..." : online ? onlineText : offlineText;
    badge.textContent = busy ? "BUSY" : online ? "ONLINE" : "OFFLINE";
    badge.className = `metric-state ${busy ? "busy" : online ? "online" : "offline"}`;
  }

  function applyRuntime(runtime) {
    state.runtime = { ...state.runtime, ...runtime };
    const current = state.runtime;
    const running = Boolean(current.running);
    const busy = Boolean(current.busy);
    const failed = Boolean(current.last_error) && !running && !busy;

    const dot = document.getElementById("globalStatusDot");
    dot.className = `status-dot ${busy ? "busy" : running ? "online" : failed ? "error" : "offline"}`;
    document.getElementById("globalStatusText").textContent =
      busy ? "Working" : running ? "Running" : failed ? "Attention required" : "Stopped";
    document.getElementById("globalStatusDetail").textContent =
      busy ? "Completing service operation" :
        running ? (current.public_url ? "Public tunnel is available" : "Local proxy is available") :
          failed ? current.last_error : "Services are offline";

    setMetric("sap", Boolean(current.sap_connected), "Connected", "Disconnected", busy && !running);
    setMetric("proxy", running, "Listening", "Stopped", busy && !running);
    setMetric("tunnel", Boolean(current.tunnel_running), "Published", "Stopped", busy && running && !current.tunnel_running);

    document.getElementById("startupMetric").textContent = current.startup_enabled ? "Enabled" : "Disabled";
    const startupState = document.getElementById("startupMetricState");
    startupState.textContent = current.startup_enabled ? "AT LOGIN" : "MANUAL";
    startupState.className = `metric-state ${current.startup_enabled ? "online" : "neutral"}`;

    document.getElementById("localUrl").value = current.local_url || "";
    document.getElementById("publicUrl").value = current.public_url || "";
    if (!document.getElementById("powerBiUrl").value && current.public_url) {
      document.getElementById("powerBiUrl").value = current.public_url;
    }
    document.getElementById("endpointState").textContent =
      current.public_url ? "Public and local" : running ? "Local only" : "Unavailable";

    document.getElementById("startButton").disabled = !state.ready || busy || running;
    document.getElementById("stopButton").disabled =
      !state.ready || busy || (!running && !current.tunnel_running);
    document.getElementById("testConnectionButton").disabled = !state.ready || busy || running;
    document.getElementById("refreshButton").disabled = !state.ready || busy;
    document.getElementById("rotateApiKeyButton").disabled = !state.ready || busy || running;
    document.getElementById("startupButton").disabled = !state.ready || busy;
    document.getElementById("browseCertificateButton").disabled =
      !state.ready || busy || running;
    document.getElementById("generateCodeButton").disabled = !state.ready;

    const startupButton = document.getElementById("startupButton");
    startupButton.textContent = current.startup_enabled ? "Disable startup" : "Enable startup";
    document.getElementById("startupDescription").textContent =
      current.startup_enabled ? "Starts after user login" : "Manual start";
    document.getElementById("ngrokBinaryStatus").textContent =
      current.tunnel_running ? "Tunnel active" : "Downloaded when first started";

    if (Array.isArray(runtime.logs) && runtime.logs.length) {
      appendLogs(runtime.logs);
    }
  }

  function appendLogs(entries) {
    const known = new Set(state.logs.map((entry) => entry.id));
    entries.forEach((entry) => {
      if (!known.has(entry.id)) {
        state.logs.push(entry);
      }
      state.latestLogId = Math.max(state.latestLogId, Number(entry.id) || 0);
    });
    if (state.logs.length > 600) {
      state.logs.splice(0, state.logs.length - 600);
    }
    renderLogs();
    const latest = state.logs[state.logs.length - 1];
    if (latest) {
      document.getElementById("recentLog").textContent =
        `${formatLogTime(latest.time)}  ${latest.level}  ${latest.message}`;
    }
  }

  function renderLogs() {
    const terminal = document.getElementById("logTerminal");
    const filter = document.getElementById("logLevel").value;
    const nearBottom = terminal.scrollHeight - terminal.scrollTop - terminal.clientHeight < 40;
    terminal.replaceChildren();
    state.logs
      .filter((entry) => filter === "ALL" || entry.level === filter)
      .forEach((entry) => {
        const row = document.createElement("div");
        row.className = `log-line ${entry.level.toLowerCase()}`;

        const time = document.createElement("span");
        time.className = "log-time";
        time.textContent = formatLogTime(entry.time);
        const level = document.createElement("span");
        level.className = "log-level";
        level.textContent = entry.level;
        const message = document.createElement("span");
        message.className = "log-message";
        message.textContent = entry.message;

        row.append(time, level, message);
        terminal.append(row);
      });
    if (nearBottom) {
      terminal.scrollTop = terminal.scrollHeight;
    }
  }

  function formatLogTime(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
  }

  async function refreshState() {
    if (!state.ready) {
      return;
    }
    try {
      const result = await callApi("get_state", state.latestLogId);
      applyRuntime(result);
    } catch (error) {
      document.getElementById("globalStatusDetail").textContent = error.message;
    }
  }

  async function startServices() {
    try {
      await saveConfig(false);
      await callApi("start_services");
      showNotification("Starting proxy services");
      await refreshState();
    } catch (error) {
      showNotification(error.message, true);
    }
  }

  async function stopServices() {
    try {
      await callApi("stop_services");
      showNotification("Stopping proxy services");
      await refreshState();
    } catch (error) {
      showNotification(error.message, true);
    }
  }

  async function testConnection() {
    try {
      await saveConfig(false);
      await callApi("test_connection");
      showNotification("SAP connection test started");
      await refreshState();
    } catch (error) {
      showNotification(error.message, true);
    }
  }

  async function generatePowerBiCode() {
    try {
      const result = await callApi(
        "generate_power_bi_code",
        document.getElementById("powerBiEntity").value,
        document.getElementById("powerBiSelect").value,
        document.getElementById("powerBiUrl").value,
      );
      document.getElementById("powerBiCode").textContent = result.code;
      showNotification("Power BI code generated");
    } catch (error) {
      showNotification(error.message, true);
    }
  }

  async function copyText(value, label) {
    if (!value) {
      showNotification(`${label} is empty`, true);
      return;
    }
    try {
      await navigator.clipboard.writeText(value);
    } catch (_) {
      const fallback = document.createElement("textarea");
      fallback.value = value;
      fallback.style.position = "fixed";
      fallback.style.opacity = "0";
      document.body.append(fallback);
      fallback.select();
      document.execCommand("copy");
      fallback.remove();
    }
    showNotification(`${label} copied`);
  }

  function updateTlsText() {
    const enabled = document.querySelector('[name="sap_verify_ssl"]').checked;
    document.getElementById("tlsModeText").textContent =
      enabled ? "Certificate verification enabled" : "Insecure TLS mode enabled";
  }

  function applyTheme(theme) {
    const resolved = theme === "light" ? "light" : "dark";
    document.documentElement.dataset.theme = resolved;
    window.localStorage.setItem("sapProxyTheme", resolved);
  }

  function wireEvents() {
    if (state.uiWired) {
      return;
    }
    state.uiWired = true;
    document.querySelectorAll(".nav-button").forEach((button) => {
      button.addEventListener("click", () => setSection(button.dataset.section));
    });
    document.querySelectorAll("[data-section-link]").forEach((button) => {
      button.addEventListener("click", () => setSection(button.dataset.sectionLink));
    });
    configForms.forEach((form) => {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
          await saveConfig(true);
        } catch (error) {
          showNotification(error.message, true);
        }
      });
    });

    document.getElementById("startButton").addEventListener("click", startServices);
    document.getElementById("stopButton").addEventListener("click", stopServices);
    document.getElementById("testConnectionButton").addEventListener("click", testConnection);
    document.getElementById("refreshButton").addEventListener("click", refreshState);
    document.getElementById("generateCodeButton").addEventListener("click", generatePowerBiCode);
    document.getElementById("copyCodeButton").addEventListener("click", () => {
      copyText(document.getElementById("powerBiCode").textContent, "Power BI code");
    });
    document.getElementById("copyApiKeyButton").addEventListener("click", () => {
      copyText(document.querySelector('[name="api_key"]').value, "API key");
    });
    document.querySelectorAll(".copy-button").forEach((button) => {
      button.addEventListener("click", () => {
        const input = document.getElementById(button.dataset.copyTarget);
        copyText(input.value, button.dataset.copyTarget === "publicUrl" ? "Public URL" : "Local URL");
      });
    });

    document.getElementById("rotateApiKeyButton").addEventListener("click", async () => {
      try {
        const result = await callApi("generate_api_key");
        state.config.api_key = result.api_key;
        document.querySelector('[name="api_key"]').value = result.api_key;
        showNotification("API key rotated");
      } catch (error) {
        showNotification(error.message, true);
      }
    });

    document.getElementById("startupButton").addEventListener("click", async () => {
      try {
        const enable = !state.runtime.startup_enabled;
        const result = await callApi("set_startup", enable);
        applyRuntime(result);
        showNotification(enable ? "Login startup enabled" : "Login startup disabled");
      } catch (error) {
        showNotification(error.message, true);
      }
    });

    document.getElementById("browseCertificateButton").addEventListener("click", async () => {
      try {
        const result = await callApi("browse_ca_bundle");
        if (result.path) {
          document.querySelector('[name="sap_ca_bundle"]').value = result.path;
        }
      } catch (error) {
        showNotification(error.message, true);
      }
    });

    document.getElementById("themeButton").addEventListener("click", () => {
      applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
    });
    document.querySelector('[name="sap_verify_ssl"]').addEventListener("change", updateTlsText);
    document.getElementById("logLevel").addEventListener("change", renderLogs);
    document.getElementById("clearLogsButton").addEventListener("click", () => {
      state.logs = [];
      renderLogs();
      document.getElementById("recentLog").textContent = "Log view cleared";
    });
  }

  async function initializeBridge() {
    if (state.ready || state.bridgeInitializing || state.bridgeFailed || !api()) {
      return;
    }
    state.bridgeInitializing = true;
    try {
      const initial = await callApi("get_initial_state");
      state.ready = true;
      populateConfig(initial.config || {});
      document.getElementById("platformLabel").textContent = initial.platform || "Desktop";
      applyRuntime(initial);
      window.setInterval(refreshState, 900);
    } catch (error) {
      state.bridgeFailed = true;
      showNotification(error.message, true);
      document.getElementById("globalStatusText").textContent = "Bridge unavailable";
      document.getElementById("globalStatusDetail").textContent = error.message;
    } finally {
      state.bridgeInitializing = false;
      if (state.bridgeWaitTimer) {
        window.clearInterval(state.bridgeWaitTimer);
        state.bridgeWaitTimer = null;
      }
    }
  }

  function bootstrapUi() {
    wireEvents();
    bridgeControlIds.forEach((id) => {
      document.getElementById(id).disabled = true;
    });
    applyTheme(window.localStorage.getItem("sapProxyTheme") || "dark");
    initializeBridge();
    state.bridgeWaitTimer = window.setInterval(initializeBridge, 250);
  }

  window.addEventListener("pywebviewready", initializeBridge);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootstrapUi, { once: true });
  } else {
    bootstrapUi();
  }
})();

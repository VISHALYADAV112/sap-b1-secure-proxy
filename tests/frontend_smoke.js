const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

class ClassList {
  constructor() {
    this.values = new Set();
  }

  toggle(name, force) {
    const shouldAdd = force === undefined ? !this.values.has(name) : force;
    if (shouldAdd) {
      this.values.add(name);
    } else {
      this.values.delete(name);
    }
    return shouldAdd;
  }

  add(name) {
    this.values.add(name);
  }

  remove(name) {
    this.values.delete(name);
  }
}

class Element {
  constructor({ id = "", classes = [], dataset = {}, type = "button", name = "" } = {}) {
    this.id = id;
    this.classList = new ClassList();
    classes.forEach((value) => this.classList.values.add(value));
    this.dataset = dataset;
    this.type = type;
    this.name = name;
    this.value = "";
    this.checked = false;
    this.listeners = {};
  }

  addEventListener(event, callback) {
    this.listeners[event] = this.listeners[event] || [];
    this.listeners[event].push(callback);
  }

  click() {
    (this.listeners.click || []).forEach((callback) => callback({ preventDefault() {} }));
  }
}

const sections = ["dashboard", "connection", "tunnel", "powerbi", "logs"].map(
  (id, index) => new Element({ id, classes: index === 0 ? ["view", "active"] : ["view"] }),
);
const navButtons = ["dashboard", "connection", "tunnel", "powerbi", "logs"].map(
  (section, index) =>
    new Element({
      classes: index === 0 ? ["nav-button", "active"] : ["nav-button"],
      dataset: { section },
    }),
);
const saveButtons = [
  new Element({ classes: ["primary", "save-settings"], type: "submit" }),
  new Element({ classes: ["primary", "save-settings"], type: "submit" }),
];

const elements = new Map();
const requiredIds = [
  "connectionForm",
  "tunnelForm",
  "startButton",
  "stopButton",
  "testConnectionButton",
  "refreshButton",
  "generateCodeButton",
  "copyCodeButton",
  "powerBiCode",
  "copyApiKeyButton",
  "rotateApiKeyButton",
  "startupButton",
  "browseCertificateButton",
  "themeButton",
  "logLevel",
  "clearLogsButton",
  "notification",
  "globalStatusText",
  "globalStatusDetail",
  "platformLabel",
  "tlsModeText",
  "recentLog",
];
requiredIds.forEach((id) => elements.set(id, new Element({ id })));
elements.set("connectionForm", new Element({ id: "connectionForm", type: "form" }));
elements.set("tunnelForm", new Element({ id: "tunnelForm", type: "form" }));
elements.set("verifySsl", new Element({ id: "verifySsl", type: "checkbox", name: "sap_verify_ssl" }));
elements.set("sapPassword", new Element({ id: "sapPassword", type: "password", name: "sap_password" }));
elements.set("ngrokAuthtoken", new Element({
  id: "ngrokAuthtoken",
  type: "password",
  name: "ngrok_authtoken",
}));
elements.set("apiKey", new Element({ id: "apiKey", type: "password", name: "api_key" }));

const verifySsl = elements.get("verifySsl");
const sapPassword = elements.get("sapPassword");
const ngrokAuthtoken = elements.get("ngrokAuthtoken");
const apiKey = elements.get("apiKey");
const intervalCallbacks = [];
const document = {
  readyState: "complete",
  documentElement: { dataset: {} },
  body: new Element(),
  addEventListener() {},
  getElementById(id) {
    if (!elements.has(id)) {
      elements.set(id, new Element({ id }));
    }
    return elements.get(id);
  },
  querySelectorAll(selector) {
    if (selector === ".nav-button") return navButtons;
    if (selector === ".view") return sections;
    if (selector === "[data-section-link]") return [];
    if (selector === "[name]") return [];
    if (selector === ".copy-button") return [];
    if (selector === ".save-settings") return saveButtons;
    return [];
  },
  querySelector(selector) {
    if (selector === '[name="sap_verify_ssl"]') return verifySsl;
    if (selector === '[name="sap_password"]') return sapPassword;
    if (selector === '[name="ngrok_authtoken"]') return ngrokAuthtoken;
    if (selector === '[name="api_key"]') return apiKey;
    return null;
  },
};

const window = {
  pywebview: undefined,
  localStorage: {
    getItem() {
      return "dark";
    },
    setItem() {},
  },
  addEventListener() {},
  setInterval(callback) {
    intervalCallbacks.push(callback);
    return intervalCallbacks.length;
  },
  clearInterval(id) {
    intervalCallbacks[id - 1] = null;
  },
  setTimeout(callback) {
    return setImmediate(callback);
  },
  clearTimeout() {},
};

global.window = window;
global.document = document;
global.navigator = {};
global.Node = { TEXT_NODE: 3 };
vm.runInThisContext(fs.readFileSync("web/app.js", "utf8"), { filename: "web/app.js" });

async function run() {
  navButtons[1].click();
  assert(sections[1].classList.values.has("active"), "Connection tab did not activate");
  assert(!sections[0].classList.values.has("active"), "Dashboard tab remained active");
  assert(elements.get("startButton").disabled, "Start button was enabled before bridge initialization");
  assert(
    elements.get("testConnectionButton").disabled,
    "Test connection button was enabled before bridge initialization",
  );
  assert(
    saveButtons.every((button) => !button.disabled),
    "Save buttons must remain clickable so validation errors can be shown",
  );

  window.pywebview = { api: {} };
  await intervalCallbacks[0]();
  assert(elements.get("startButton").disabled, "Partial bridge unexpectedly enabled controls");

  let pingCalls = 0;
  let initialStateCalls = 0;
  window.pywebview.api.ping = async () => {
    pingCalls += 1;
    return { ok: true, platform: "Windows", log_path: "C:\\SAPB1Proxy\\proxy.log" };
  };
  window.pywebview.api.get_initial_state = async () => {
    initialStateCalls += 1;
    return {
      ok: true,
      platform: "Windows",
      config: {},
      busy: false,
      running: false,
      sap_connected: false,
      tunnel_running: false,
      startup_enabled: false,
      logs: [],
    };
  };
  let apiKeyCalls = 0;
  window.pywebview.api.get_api_key = async () => {
    apiKeyCalls += 1;
    return { ok: true, api_key: "synthetic-api-key" };
  };

  await intervalCallbacks[0]();
  await new Promise((resolve) => setImmediate(resolve));

  assert.strictEqual(pingCalls, 1, "Bridge handshake was not performed");
  assert.strictEqual(initialStateCalls, 1, "Initial state was not requested");
  assert(!elements.get("startButton").disabled, "Start button remained disabled after bridge initialization");
  assert.strictEqual(elements.get("platformLabel").textContent, "Windows");
  assert.strictEqual(sapPassword.value, "", "SAP password was preloaded into the DOM");
  assert.strictEqual(ngrokAuthtoken.value, "", "ngrok token was preloaded into the DOM");
  assert.strictEqual(apiKey.value, "", "API key was preloaded into the DOM");

  elements.get("copyApiKeyButton").click();
  await new Promise((resolve) => setImmediate(resolve));
  assert.strictEqual(apiKeyCalls, 1, "Copy did not request the API key explicitly");
  console.log("frontend navigation and bridge smoke test passed");
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

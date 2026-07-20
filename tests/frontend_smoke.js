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

const verifySsl = elements.get("verifySsl");
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
  setInterval() {
    return 1;
  },
  clearInterval() {},
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
console.log("frontend navigation smoke test passed");

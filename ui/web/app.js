"use strict";

/* KOVA dashboard page, plain JS, no framework. Everything Python sends arrives
   as one event through window.kova.receive; every call back into Python goes
   through pywebview.api. Nothing is fetched from the internet. */

window.kova = (function () {
  const handlers = {
    state: applyState,
    boot: applyBoot,
    chat: applyChat,
    history: applyHistory,
    notice: applyNotice,
    trace: applyTrace,
    prompt: applyPrompt,
    stats: applyStats,
    memory: applyMemoryRows,
    "screen-result": applyScreenResult,
    "focus-input": applyFocusInput,
  };

  function receive(event) {
    const handler = handlers[event && event.t];
    if (handler) {
      handler(event);
    }
  }

  /* --- events from Python --- */

  const names = {};
  const CHAT_KEEP = 200;
  const seenMessages = new Set();
  const activeRequests = { apps: 0, files: 0, activity: 0, system: 0 };
  let fileRoots = [];
  let fileFolder = "";
  let systemTimer = null;
  let systemBusy = false;
  let screenResultTimer = null;
  let historyReady = false;
  let waitingForBridge = false;
  const chatWaitingForHistory = [];

  function applyState(event) {
    const dot = document.getElementById("status-dot");
    const text = document.getElementById("status-text");
    const home = document.getElementById("home-state");
    if (text) {
      text.textContent = event.text;
    }
    if (dot) {
      dot.className = "status-dot is-" + (event.tone || "idle");
    }
    if (home) {
      home.textContent = event.text;
      home.className = "home-state tone-" + (event.tone || "idle");
    }
    companionState(event.state || "idle");
    const line = document.getElementById("home-line");
    if (line) {
      line.textContent = HOME_LINES[event.state] || HOME_LINES.idle;
    }
  }

  const HOME_LINES = {
    idle: "I'm here. What are we working on?",
    awake: "I'm listening.",
    listening: "I'm listening.",
    thinking: "Let me think about that.",
    working: "On it.",
    approval: "I need your okay for this.",
    speaking: "Talking.",
    error: "That didn't work. Want to try again?",
    asleep: "Say my name, or type below.",
    muted: "My mic is off. You can still type.",
  };

  /* --- the companion: the Reaper loop plays only while Max is awake and the
     window is on screen; asleep or voice off shows the still frame, dimmed --- */

  let companionNow = "idle";

  function companionState(state) {
    companionNow = state;
    const frame = document.getElementById("companion");
    const video = document.getElementById("companion-video");
    if (frame) {
      frame.dataset.state = state;
    }
    if (!video) {
      return;
    }
    const still = state === "asleep" || state === "muted" || state === "error" || document.hidden;
    if (still) {
      video.pause();
    } else {
      video.playbackRate = state === "thinking" ? 0.6 : 1;
      const playing = video.play();
      if (playing && playing.catch) {
        playing.catch(function () {});   // autoplay refused: the poster stays, nothing breaks
      }
    }
  }

  function applyBoot(event) {
    const line = document.getElementById("assistant-line");
    const brain = document.getElementById("brain");
    if (line) {
      line.textContent = event.assistant + ", your desktop assistant";
    }
    if (brain) {
      brain.textContent = event.brain;
    }
    document.body.dataset.tint = event.tint || "none";
    document.body.dataset.persona = event.persona || "max";
    names.assistant = event.assistant;
    names.title = event.title;
    const input = document.getElementById("ask-input");
    if (input) {
      input.placeholder = event.wake
        ? "Message " + event.assistant + ", or say \u201c" + event.wake + "\u201d"
        : "Message " + event.assistant;
    }
    greeting();
    buildQuick(event.quick || []);
  }

  function buildQuick(actions) {
    const strip = document.getElementById("quick");
    if (!strip) {
      return;
    }
    strip.textContent = "";
    actions.forEach((action) => {
      const button = document.createElement("button");
      button.className = "quick-action";
      button.type = "button";
      button.textContent = action.label;
      button.addEventListener("click", () => {
        pywebview.api.send(action.send);
        showPage("assistant");   // the answer appears in the conversation
      });
      strip.appendChild(button);
    });
  }

  /* --- chat --- */

  function applyChat(event) {
    // Restore older messages before painting any live message; otherwise a
    // fast first request can appear above the conversation returned by Python.
    if (!historyReady) {
      chatWaitingForHistory.push(event);
      return;
    }
    if (event.id && seenMessages.has(event.id)) {
      return;
    }
    const log = document.getElementById("chat-log");
    if (!log) {
      return;
    }
    if (event.id) {
      seenMessages.add(event.id);
    }
    dropEmpty("chat-empty");
    if (event.who === "max" && !event.note && !loadingHistory) {
      beHappy();
    }
    log.appendChild(bubble(event.who, event.text, event.note));
    trim(log);
  }

  let loadingHistory = false;
  let happyTimer = null;

  function beHappy() {
    // A finished job shows for two seconds, then the frame settles
    const frame = document.getElementById("companion");
    if (!frame) {
      return;
    }
    frame.classList.add("is-happy");
    clearTimeout(happyTimer);
    happyTimer = setTimeout(function () { frame.classList.remove("is-happy"); }, 2000);
  }

  function applyHistory(event) {
    historyReady = true;
    loadingHistory = true;
    (event.messages || []).forEach(function (message) {
      applyChat({id: message.id, who: message.role, text: message.text});
    });
    loadingHistory = false;
    chatWaitingForHistory.splice(0).forEach(applyChat);
  }

  function loadHistory() {
    if (typeof pywebview !== "undefined" && pywebview.api) {
      welcome();
      pywebview.api.history().then(function (messages) {
        applyHistory({t: "history", messages: messages});
      }).catch(function () {
        // A missing or unreadable local history must not stop the Assistant.
        applyHistory({t: "history", messages: []});
      });
    } else if (!waitingForBridge) {
      // pywebview adds its api a moment after the page loads: ask again once it is there
      // (live test 8 Nov: the saved conversation never showed after a restart)
      waitingForBridge = true;
      window.addEventListener("pywebviewready", loadHistory, { once: true });
    }
  }

  function dropEmpty(id) {
    const empty = document.getElementById(id);
    if (empty) {
      empty.remove();
    }
  }

  function applyNotice(event) {
    const log = document.getElementById("chat-log");
    if (!log) {
      return;
    }
    dropEmpty("chat-empty");
    log.appendChild(bubble("max", event.text, "Heads up"));
    trim(log);
  }

  function applyScreenResult(event) {
    const status = document.getElementById("screen-result");
    if (!status) {
      return;
    }
    status.textContent = event.text || "";
    status.className = "screen-result" + (event.success ? " is-success" : " is-error");
    if (screenResultTimer) {
      clearTimeout(screenResultTimer);
    }
    screenResultTimer = setTimeout(function () {
      status.textContent = "";
      status.className = "screen-result";
    }, 5000);
    const activityPage = document.getElementById("page-activity");
    if (activityPage && activityPage.classList.contains("is-active")) loadActivity();
  }

  function trim(log) {
    while (log.children.length > CHAT_KEEP) {
      log.removeChild(log.firstChild);
    }
    log.scrollTop = log.scrollHeight;
  }

  function bubble(who, text, note) {
    const row = document.createElement("div");
    row.className = "bubble is-" + (who === "user" ? "user" : "max");
    if (note) {
      const tag = document.createElement("span");
      tag.className = "bubble-note";
      tag.textContent = note;
      row.appendChild(tag);
    }
    const body = document.createElement("span");
    body.className = "bubble-text";
    body.textContent = text;   // never innerHTML: the page is never built out of raw text
    row.appendChild(body);
    return row;
  }

  /* --- activity: every tool call, live --- */

  const TRACE_KEEP = 500;

  function applyTrace(event) {
    const text = (event.line || "").trim();
    addRecent(text);
    const box = document.getElementById("trace");
    if (!box) {
      return;
    }
    dropEmpty("trace-empty");
    const line = document.createElement("div");
    line.className = "trace-line" +
      (text.startsWith("→") ? " is-call" : text.startsWith("←") ? " is-back" : "");
    line.textContent = event.line;
    box.appendChild(line);
    while (box.children.length > TRACE_KEEP) {
      box.removeChild(box.firstChild);
    }
    box.scrollTop = box.scrollHeight;
  }

  /* --- Home's "Done this session": the last few tool calls and how they ended --- */

  const RECENT_KEEP = 5;

  function addRecent(text) {
    const list = document.getElementById("recent");
    if (!list) {
      return;
    }
    if (text.startsWith("→")) {
      list.querySelectorAll(".empty").forEach((item) => item.remove());
      const item = document.createElement("li");
      item.textContent = text.slice(1).trim();
      list.insertBefore(item, list.firstChild);
      while (list.children.length > RECENT_KEEP) {
        list.removeChild(list.lastChild);
      }
    } else if (text.startsWith("←") && list.firstChild) {
      const result = text.slice(1).trim();
      if (/^(not done|sorry|i can't|i couldn't|the user said no)/i.test(result)) {
        list.firstChild.classList.add("is-failed");
      }
      list.firstChild.title = result;
    }
  }

  /* --- the approval dialog --- */

  const dialog = { token: null, kind: null };

  function applyPrompt(event) {
    dialog.token = event.token;
    dialog.kind = event.kind;
    const overlay = document.getElementById("overlay");
    if (!overlay) {
      respond(event.token, event.kind === "confirm" ? false : null);
      return;
    }
    const question = document.getElementById("dialog-question");
    const input = document.getElementById("dialog-input");
    const actions = document.getElementById("dialog-actions");
    const kindLabel = document.getElementById("dialog-kind");
    if (question) {
      question.textContent = event.question;
    }
    if (kindLabel) {
      kindLabel.textContent = event.kind === "ask" ? "Question" : "Approval";
    }
    if (input) {
      input.hidden = event.kind !== "ask";
      input.value = "";
      if (event.kind === "ask") {
        input.focus();
      }
    }
    if (actions) {
      actions.textContent = "";
      const ok = document.createElement("button");
      ok.className = "dialog-btn is-accent";
      ok.type = "button";
      ok.textContent = event.kind === "ask" ? "OK" : "Yes, do it";
      ok.addEventListener("click", () => {
        respond(dialog.token, event.kind === "ask" ? (input ? input.value.trim() : "") : true);
      });
      const no = document.createElement("button");
      no.className = "dialog-btn";
      no.type = "button";
      no.textContent = event.kind === "ask" ? "Cancel" : "No";
      no.addEventListener("click", () => respond(dialog.token, event.kind === "ask" ? null : false));
      actions.appendChild(no);
      actions.appendChild(ok);
    }
    overlay.hidden = false;
    dialog.openedAt = Date.now();
    if (event.kind === "confirm" && document.activeElement) {
      document.activeElement.blur();   // typing in the message box must not land in the sheet
    }
  }

  function applyFocusInput() {
    // The type-now hotkey: the conversation, with the cursor ready in the box
    showPage("assistant");
    const input = document.getElementById("ask-input");
    if (input) {
      input.focus();
      input.select();
    }
  }

  function respond(token, value) {
    closeDialog();
    if (token !== null) {
      pywebview.api.respond(token, value);
    }
  }

  function closeDialog() {
    const overlay = document.getElementById("overlay");
    if (overlay) {
      overlay.hidden = true;
    }
    dialog.token = null;
    dialog.kind = null;
  }

  /* --- Home's meters: real readings, 0..1; a figure Windows doesn't give stays "–" --- */

  const HIGH = 0.85;

  function applyStats(event) {
    const values = event.rings || {};
    setMeter("cpu", values.cpu);
    setMeter("ram", values.ram);
    setMeter("disk", values.disk);
  }

  function setMeter(key, value) {
    const fill = document.getElementById("meter-" + key);
    const label = document.getElementById("meter-" + key + "-value");
    const known = value !== null && value !== undefined;
    if (fill) {
      fill.style.width = known ? Math.round(value * 100) + "%" : "0";
      fill.classList.toggle("is-high", known && value >= HIGH);
    }
    if (label) {
      label.textContent = known ? Math.round(value * 100) + "%" : "–";
    }
  }

  /* --- memory --- */

  function applyMemoryRows(event) {
    const list = document.getElementById("memory-list");
    if (!list) {
      return;
    }
    list.textContent = "";
    const rows = event.rows || [];
    if (!rows.length) {
      const empty = document.createElement("p");
      empty.className = "memory-empty";
      empty.textContent = (names.assistant || "Max") + " doesn't remember anything yet.";
      list.appendChild(empty);
      return;
    }
    rows.forEach(function (row) {
      const block = document.createElement("div");
      block.className = "memory-row";
      const number = document.createElement("span");
      number.className = "memory-id";
      number.textContent = row[0];
      block.appendChild(number);
      const fact = document.createElement("span");
      fact.className = "memory-fact";
      fact.textContent = row[1];
      const forget = document.createElement("button");
      forget.className = "memory-forget";
      forget.type = "button";
      forget.textContent = "Forget";
      forget.addEventListener("click", (function (factId) {
        return function () {
          pywebview.api.forget(factId);
        };
      })(row[0]));
      block.appendChild(fact);
      block.appendChild(forget);
      list.appendChild(block);
    });
  }

  function loadMemory() {
    if (typeof pywebview !== "undefined" && pywebview.api) {
      pywebview.api.memory().then(function (rows) {
        applyMemoryRows({ t: "memory", rows: rows });
      });
    }
  }

  /* --- settings --- */

  function loadSettings() {
    if (typeof pywebview !== "undefined" && pywebview.api) {
      pywebview.api.settings().then(buildSettings);
    }
  }

  function buildSettings(current) {
    const form = document.getElementById("settings-form");
    if (!form) {
      return;
    }
    form.textContent = "";

    const persona = field("Persona", "name, voice and manner together");
    const personaSeg = segmented((current.PERSONAS || []).map(function (p) { return p.name; }),
      ((current.PERSONAS || []).find(function (p) { return p.id === current.PERSONA; }) || {}).name);
    persona.row.appendChild(personaSeg);
    form.appendChild(persona.row);

    const voice = field("Voice", "only the sound; a persona sets it too");
    const voiceSeg = segmented(["male", "female"], current.VOICE);
    voice.row.appendChild(voiceSeg);   // was built but never placed: the row showed no control
    form.appendChild(voice.row);

    form.appendChild(switchField("Proactive alerts", "battery, RAM and disk warnings",
      current.ALERTS_ON, "alerts"));

    form.appendChild(switchField("Reaper on the desktop", "a small round Reaper that stays on screen; double-click opens KOVA",
      current.PET_ON, "pet"));

    form.appendChild(switchField("Start with Windows", "a shortcut in your own Startup folder; off removes it",
      current.START_WITH_WINDOWS, "startup"));

    form.appendChild(switchField("Close app on X",
      "closing the dashboard exits KOVA instead of hiding it to the system tray",
      current.CLOSE_ON_X, "close-on-x"));

    form.appendChild(textField("Wake words", "comma separated", current.WAKE_WORDS, "wake-words"));
    form.appendChild(textField("Listen-now key", "wakes Max without the wake word",
      current.PTT_HOTKEY, "hotkey"));

    const actions = document.createElement("div");
    actions.className = "settings-actions";
    const save = document.createElement("button");
    save.className = "dialog-btn is-accent";
    save.type = "button";
    save.textContent = "Save";
    save.addEventListener("click", function () {
      const payload = {
        PERSONA: ((current.PERSONAS || []).find(function (p) { return p.name === personaSeg.value; }) || {}).id,
        VOICE: voiceSeg.value,
        ALERTS_ON: document.getElementById("set-alerts").checked,
        PET_ON: document.getElementById("set-pet").checked,
        START_WITH_WINDOWS: document.getElementById("set-startup").checked,
        CLOSE_ON_X: document.getElementById("set-close-on-x").checked,
        WAKE_WORDS: document.getElementById("set-wake-words").value,
        PTT_HOTKEY: document.getElementById("set-hotkey").value,
      };
      pywebview.api.save(payload).then(function (answer) {
        const status = document.getElementById("settings-status");
        if (!status) {
          return;
        }
        let text = "";
        if (!answer.changed.length) {
          text = "Nothing changed.";
        } else {
          text = "Saved: " + answer.changed.join(", ") + ".";
          if (answer.refused.length) {
            text += " " + answer.refused[0] + " was not accepted, the old key still works.";
          }
        }
        if (answer.notes && answer.notes.length) {
          text += " " + answer.notes.join(" ");
        }
        status.textContent = text;
      });
    });
    actions.appendChild(save);
    form.appendChild(actions);
    form.appendChild(brainSection(current.BRAIN || {}));
  }

  /* --- the brain: which one answers, whether a Gemini key is saved; the key never comes back --- */

  function brainSection(brain) {
    const box = document.createElement("div");
    box.className = "settings-brain";
    const title = document.createElement("h2");
    title.className = "panel-title";
    title.textContent = "Brain";
    box.appendChild(title);
    const now = document.createElement("p");
    now.id = "brain-now";
    now.textContent = brain.footer || "";
    box.appendChild(now);
    box.appendChild(keyRow("Gemini API key", "gemini", brain.gemini_key,
      "free from aistudio.google.com", "set_gemini_key"));
    box.appendChild(keyRow("OpenRouter API key", "openrouter", brain.openrouter_key,
      "from openrouter.ai; answers when Gemini can't", "set_openrouter_key"));
    return box;
  }

  function keyRow(name, id, saved, where, method) {
    const key = field(name, saved
      ? "saved on this PC; paste a new one to replace it"
      : "not set; " + where + ". It stays in config.json on this PC");
    const input = document.createElement("input");
    input.className = "settings-input";
    input.id = "set-" + id + "-key";
    input.type = "password";
    input.autocomplete = "off";
    input.placeholder = saved ? "Saved" : "Paste key";
    const save = document.createElement("button");
    save.className = "dialog-btn";
    save.type = "button";
    save.textContent = "Save";
    save.addEventListener("click", function () {
      const value = input.value.trim();
      if (!value) {
        return;
      }
      pywebview.api[method](value).then(function (answer) {
        input.value = "";
        const status = document.getElementById("settings-status");
        if (status) {
          status.textContent = answer.message;
        }
        if (answer.ok) {
          loadSettings();
        }
      });
    });
    const pair = document.createElement("div");
    pair.className = "key-pair";
    pair.append(input, save);
    key.row.appendChild(pair);
    return key.row;
  }

  /* --- first start without a key: one welcome sheet, then never again --- */

  let welcomed = false;

  function welcome() {
    if (welcomed) {
      return;
    }
    welcomed = true;
    if (!window.pywebview || !pywebview.api || !pywebview.api.first_run) {
      return;
    }
    pywebview.api.first_run().then(function (answer) {
      const sheet = document.getElementById("welcome");
      if (!answer.show || !sheet) {
        return;
      }
      const seg = segmented((answer.PERSONAS || []).map(function (p) { return p.name; }), "Max");
      document.getElementById("welcome-persona").appendChild(seg);
      sheet.hidden = false;
      const finish = function (withKey) {
        const chosen = (answer.PERSONAS || []).find(function (p) { return p.name === seg.value; }) || {};
        const key = withKey ? document.getElementById("welcome-key").value.trim() : "";
        pywebview.api.finish_setup(chosen.id || "", key).then(function (result) {
          const note = document.getElementById("welcome-note");
          if (!result.ok) {
            note.textContent = result.message;
            note.classList.add("is-error");
            return;
          }
          sheet.hidden = true;
        });
      };
      document.getElementById("welcome-start").addEventListener("click", function () { finish(true); });
      document.getElementById("welcome-skip").addEventListener("click", function () { finish(false); });
    });
  }

  function field(name, note) {
    const row = document.createElement("div");
    row.className = "settings-row";
    const box = document.createElement("div");
    box.className = "settings-label";
    const title = document.createElement("span");
    title.textContent = name;
    const hint = document.createElement("span");
    hint.className = "settings-note";
    hint.textContent = note;
    box.appendChild(title);
    box.appendChild(hint);
    row.appendChild(box);
    return { row: row };
  }

  function segmented(values, active) {
    const seg = document.createElement("div");
    seg.className = "segmented";
    values.forEach(function (value) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = value;
      button.classList.toggle("is-active", value === active);
      button.addEventListener("click", function () {
        seg.querySelectorAll("button").forEach(function (other) {
          other.classList.toggle("is-active", other === button);
        });
        seg.value = value;
      });
      seg.appendChild(button);
    });
    seg.value = active;
    return seg;
  }

  function switchField(name, note, on, id) {
    const holder = field(name, note);
    const box = document.createElement("label");
    box.className = "switch";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.id = "set-" + id;
    input.checked = !!on;
    const slider = document.createElement("span");
    slider.className = "slider";
    box.appendChild(input);
    box.appendChild(slider);
    holder.row.appendChild(box);
    return holder.row;
  }

  function textField(name, note, value, id) {
    const holder = field(name, note);
    const input = document.createElement("input");
    input.className = "settings-input";
    input.id = "set-" + id;
    input.type = "text";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.value = value === null || value === undefined ? "" : value;
    holder.row.appendChild(input);
    return holder.row;
  }

  /* --- greeting: real time of day and date --- */

  function greeting() {
    const element = document.getElementById("greeting");
    if (element && names.title) {
      const hour = new Date().getHours();
      const part = hour < 5 ? "Late night" : hour < 12 ? "Good morning"
        : hour < 17 ? "Good afternoon" : hour < 21 ? "Good evening" : "Good night";
      element.textContent = part + ".";
    }
    const date = document.getElementById("home-date");
    if (date) {
      date.textContent = new Date().toLocaleString("en-GB",
        { weekday: "long", day: "numeric", month: "long" });
    }
  }

  /* --- page navigation --- */

  function showPage(name) {
    document.querySelectorAll(".nav-item").forEach((item) => {
      item.classList.toggle("is-active", item.dataset.page === name);
    });
    document.querySelectorAll(".page").forEach((page) => {
      page.classList.toggle("is-active", page.id === "page-" + name);
    });
    if (name === "assistant") {
      const input = document.getElementById("ask-input");
      if (input) {
        input.focus();
      }
    }
    if (name === "memory") {
      loadMemory();
    }
    if (name === "settings") {
      loadSettings();
    }
    if (name === "apps") {
      loadApps();
    }
    if (name === "files") {
      loadFilesRoots();
    }
    if (name === "system") {
      startSystemPolling();
    } else {
      stopSystemPolling();
    }
    if (name === "activity") {
      loadActivity();
    }
  }

  /* --- Applications: read from Start menu, launch through the Agent --- */

  function loadApps() {
    const request = ++activeRequests.apps;
    const query = document.getElementById("apps-search");
    const status = document.getElementById("apps-status");
    if (status) status.textContent = "Loading installed apps…";
    if (!window.pywebview || !pywebview.api) {
      if (status) status.textContent = "KOVA's desktop bridge is not ready.";
      return;
    }
    pywebview.api.apps(query ? query.value : "").then(function (answer) {
      if (request !== activeRequests.apps) return;
      renderApps(answer || {items: []});
    }).catch(function () {
      if (request === activeRequests.apps && status) status.textContent = "Couldn't read the installed apps.";
    });
  }

  function renderApps(answer) {
    const list = document.getElementById("apps-list");
    const status = document.getElementById("apps-status");
    const count = document.getElementById("apps-count");
    if (!list) return;
    list.textContent = "";
    const rows = answer.items || [];
    if (count) count.textContent = rows.length ? rows.length + " apps" : "";
    if (status) status.textContent = answer.error || (rows.length ? "" : "No matching installed apps.");
    rows.forEach(function (app) {
      const row = document.createElement("article");
      row.className = "data-row";
      const info = document.createElement("div");
      info.className = "data-info";
      const name = document.createElement("strong");
      name.textContent = app.name;
      const state = document.createElement("span");
      state.className = "data-meta";
      state.textContent = app.running ? "Running" : "Not running";
      info.append(name, state);
      const launch = document.createElement("button");
      launch.type = "button";
      launch.className = "dialog-btn";
      launch.textContent = app.running ? "Open" : "Launch";
      launch.addEventListener("click", function () {
        pywebview.api.launch_app(app.name).then(showQueuedResult).catch(showActionError);
      });
      row.append(info, launch);
      list.appendChild(row);
    });
  }

  /* --- Files: safe roots and bounded browsing/search; actions use Max --- */

  function loadFilesRoots() {
    if (!window.pywebview || !pywebview.api) {
      setText("files-status", "KOVA's desktop bridge is not ready.");
      return;
    }
    if (fileRoots.length) {
      loadFiles(fileFolder || fileRoots[0].path, "");
      return;
    }
    setText("files-status", "Loading allowed folders…");
    pywebview.api.files().then(function (answer) {
      fileRoots = answer.roots || [];
      const select = document.getElementById("files-folder");
      if (select) {
        select.textContent = "";
        fileRoots.forEach(function (root) {
          const option = document.createElement("option");
          option.value = root.path;
          option.textContent = root.name;
          select.appendChild(option);
        });
      }
      if (!fileRoots.length) {
        setText("files-status", "No allowed folders are available.");
        return;
      }
      fileFolder = fileRoots[0].path;
      loadFiles(fileFolder, "");
    }).catch(function () { setText("files-status", "Couldn't read the allowed folders."); });
  }

  function loadFiles(folder, query) {
    if (!window.pywebview || !pywebview.api) return;
    fileFolder = folder || "";
    const request = ++activeRequests.files;
    setText("files-status", query ? "Searching…" : "Loading folder…");
    pywebview.api.files(fileFolder, query || "").then(function (answer) {
      if (request !== activeRequests.files) return;
      fileRoots = answer.roots || fileRoots;
      renderFiles(answer);
    }).catch(function () {
      if (request === activeRequests.files) setText("files-status", "Couldn't read this folder.");
    });
  }

  function renderFiles(answer) {
    const list = document.getElementById("files-list");
    if (!list) return;
    list.textContent = "";
    setText("files-status", answer.error || (answer.truncated ? "Showing the first results; narrow your search for more." :
      answer.items.length ? "" : "This folder is empty."));
    const location = document.getElementById("files-location");
    if (location) location.textContent = answer.folder || "";
    const up = document.getElementById("files-up");
    const root = fileRoots.find(function (item) { return item.path.toLowerCase() === fileFolder.toLowerCase(); });
    if (up) up.disabled = !fileFolder || !!root;
    (answer.items || []).forEach(function (item) {
      const row = document.createElement("article");
      row.className = "data-row file-row";
      const info = document.createElement("div");
      info.className = "data-info";
      const name = document.createElement("strong");
      name.textContent = item.name;
      const meta = document.createElement("span");
      meta.className = "data-meta";
      const changed = item.modified ? ", changed " + formatActivityTime(item.modified).replace(/^(Today|Yesterday)/,
        function (word) { return word.toLowerCase(); }) : "";
      meta.textContent = (item.is_dir ? "Folder" : formatBytes(item.size)) + changed;
      if (item.is_dir) {
        name.className = "data-link";
        name.tabIndex = 0;
        name.addEventListener("click", function () { loadFiles(item.path, ""); });
        name.addEventListener("keydown", function (event) {
          if (event.key === "Enter") loadFiles(item.path, "");
        });
      }
      info.append(name, meta);
      const actions = document.createElement("div");
      actions.className = "data-actions";
      const open = makeButton(item.is_dir ? "Browse" : "Open", function () {
        pywebview.api.file_action("open", item.path).then(showQueuedResult).catch(showActionError);
      });
      actions.appendChild(open);
      if (!item.is_dir) {
        actions.appendChild(makeButton("Show", function () {
          pywebview.api.file_action("show", item.path).then(showQueuedResult).catch(showActionError);
        }));
        actions.appendChild(makeButton("Delete", function () {
          pywebview.api.file_action("delete", item.path).then(showQueuedResult).catch(showActionError);
        }));
      }
      row.append(info, actions);
      list.appendChild(row);
    });
  }

  function formatBytes(bytes) {
    if (bytes === null || bytes === undefined) return "Size unavailable";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + " MB";
    return (bytes / (1024 * 1024 * 1024)).toFixed(1) + " GB";
  }

  function parentFolder(path) {
    const normalized = String(path || "").replace(/[\\/]+$/, "");
    const slash = Math.max(normalized.lastIndexOf("/"), normalized.lastIndexOf("\\"));
    if (slash < 0) return "";
    const parent = normalized.slice(0, slash);
    return /^[A-Za-z]:$/.test(parent) ? parent + "\\" : parent;
  }

  function makeButton(label, action) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "dialog-btn";
    button.textContent = label;
    button.addEventListener("click", action);
    return button;
  }

  function showQueuedResult(answer) {
    if (answer && !answer.queued) applyScreenResult({text: answer.message || "Max couldn't take that request.", success: false});
  }

  function showActionError() {
    applyScreenResult({text: "Max couldn't complete that action.", success: false});
  }

  function setText(id, text) {
    const element = document.getElementById(id);
    if (element) element.textContent = text || "";
  }

  /* --- System: sample only while this page is active and the window is shown --- */

  function startSystemPolling() {
    if (systemTimer || document.hidden) return;
    loadSystem();
    systemTimer = setInterval(loadSystem, 2000);
  }

  function stopSystemPolling() {
    if (systemTimer) clearInterval(systemTimer);
    systemTimer = null;
  }

  function loadSystem() {
    if (systemBusy || document.hidden || !window.pywebview || !pywebview.api) return;
    systemBusy = true;
    const request = ++activeRequests.system;
    pywebview.api.system().then(function (snapshot) {
      if (request === activeRequests.system) renderSystem(snapshot || {});
    }).catch(function () {
      setText("system-updated", "Couldn't read system status.");
    }).finally(function () { systemBusy = false; });
  }

  function renderSystem(snapshot) {
    const grid = document.getElementById("system-status");
    const processes = document.getElementById("system-processes");
    if (!grid || !processes) return;
    grid.textContent = "";
    const ram = snapshot.ram;
    const battery = snapshot.battery;
    const disks = snapshot.disks || [];
    addSystemCard(grid, "CPU", snapshot.cpu_percent === null || snapshot.cpu_percent === undefined
      ? "Not available" : Math.round(snapshot.cpu_percent) + "%");
    addSystemCard(grid, "Memory", ram ? Number(ram.used_gb).toFixed(1) + " / " +
      Number(ram.total_gb).toFixed(1) + " GB (" + Math.round(ram.percent) + "%)" : "Not available");
    addSystemCard(grid, "GPU", snapshot.gpu_percent === null || snapshot.gpu_percent === undefined
      ? "Not available" : Math.round(snapshot.gpu_percent) + "%");
    addSystemCard(grid, "Battery", battery ? Math.round(battery.percent) + "%, " +
      (battery.plugged ? "charging" : "on battery") : "Not available");
    disks.forEach(function (disk) {
      addSystemCard(grid, "Disk " + disk.drive, Number(disk.free_gb).toFixed(1) + " GB free of " +
        Number(disk.total_gb).toFixed(1) + " GB (" + Math.round(disk.percent) + "% used)");
    });
    processes.textContent = "";
    const rows = snapshot.processes || [];
    if (!rows.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "Process details are not available.";
      processes.appendChild(empty);
    }
    rows.forEach(function (process) {
      const row = document.createElement("article");
      row.className = "data-row";
      const name = document.createElement("strong");
      name.textContent = process.name;
      const usage = document.createElement("span");
      usage.className = "data-meta";
      usage.textContent = Math.round(process.ram_mb) + " MB";
      row.append(name, usage);
      processes.appendChild(row);
    });
    setText("system-updated", "Updated " + new Date().toLocaleTimeString());
  }

  function addSystemCard(grid, title, value) {
    const card = document.createElement("article");
    card.className = "panel system-card";
    const label = document.createElement("span");
    label.className = "data-meta";
    label.textContent = title;
    const reading = document.createElement("strong");
    reading.textContent = value;
    card.append(label, reading);
    grid.appendChild(card);
  }

  /* --- Activity: read the real, local ActionRunner log --- */

  function loadActivity() {
    const request = ++activeRequests.activity;
    setText("activity-status", "Loading recent actions…");
    if (!window.pywebview || !pywebview.api) {
      setText("activity-status", "KOVA's desktop bridge is not ready.");
      return;
    }
    pywebview.api.activity(100).then(function (answer) {
      if (request !== activeRequests.activity) return;
      renderActivity(answer || {items: []});
    }).catch(function () {
      if (request === activeRequests.activity) setText("activity-status", "Couldn't read the activity log.");
    });
  }

  function renderActivity(answer) {
    const list = document.getElementById("activity-list");
    if (!list) return;
    list.textContent = "";
    const rows = answer.items || [];
    setText("activity-status", answer.error || (rows.length ? "" : "No actions have been logged yet."));
    rows.forEach(function (item) {
      const row = document.createElement("article");
      row.className = "activity-row" + (item.success ? " is-success" : " is-failed");
      const head = document.createElement("div");
      head.className = "activity-head";
      const title = document.createElement("strong");
      title.textContent = toolName(item.tool) + (item.source === "agent" ? " (AI)" : "");
      const time = document.createElement("time");
      time.textContent = formatActivityTime(item.time);
      head.append(title, time);
      const message = document.createElement("p");
      message.textContent = item.message || "No result details.";
      const meta = document.createElement("span");
      meta.className = "data-meta";
      const parts = [item.success ? "Done" : "Failed"];
      if (APPROVAL_WORDS[item.approval]) {
        parts.push(APPROVAL_WORDS[item.approval]);
      }
      if (item.undo_text) {
        parts.push("can be undone (" + item.undo_text + ")");
      }
      meta.textContent = parts.join(", ") + ".";
      row.append(head, message, meta);
      if (item.undo_available) {
        row.appendChild(makeButton("Undo", function () {
          pywebview.api.undo().then(showQueuedResult).catch(showActionError);
        }));
      }
      list.appendChild(row);
    });
  }

  // Plain words for the action log: tool ids and approval codes are for the code, not for people
  const APPROVAL_WORDS = { confirmed: "you approved it", approved: "you approved it", declined: "you said no",
                           "user asked": "you asked for it", denied: "you said no",
                           blocked: "not allowed" };
  const UPPER_WORDS = { cpu: "CPU", ram: "RAM", gpu: "GPU", ui: "UI", url: "URL", vs: "VS" };

  function toolName(tool) {
    if (!tool) {
      return "Undo";
    }
    const words = String(tool).split("_").map(function (word) { return UPPER_WORDS[word] || word; });
    const first = words[0];
    words[0] = first === first.toUpperCase() ? first : first.charAt(0).toUpperCase() + first.slice(1);
    return words.join(" ");
  }

  function formatActivityTime(value) {
    const date = new Date(value);
    if (!value || Number.isNaN(date.getTime())) return "Time unavailable";
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
    const clock = date.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
    if (date >= today) return "Today " + clock;
    if (date >= yesterday) return "Yesterday " + clock;
    return date.toLocaleDateString("en-GB", { day: "numeric", month: "short" }) + " " + clock;
  }

  /* --- the ask bar --- */

  function sendTyped() {
    const input = document.getElementById("ask-input");
    if (!input) {
      return;
    }
    const text = input.value.trim();
    if (!text) {
      return;
    }
    input.value = "";
    pywebview.api.send(text);
  }

  /* --- the real clock, updated once a second --- */

  function tickClock() {
    const clock = document.getElementById("clock");
    if (!clock) {
      return;
    }
    const now = new Date();
    const day = now.toLocaleString("en-GB", { weekday: "short" });
    const date = now.toLocaleString("en-GB", { day: "numeric", month: "short" });
    const time = now.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
    clock.textContent = day + " " + date + ", " + time;
  }

  function initPage() {
    document.querySelectorAll(".nav-item").forEach((item) => {
      item.addEventListener("click", () => showPage(item.dataset.page));
    });
    const input = document.getElementById("ask-input");
    if (input) {
      input.addEventListener("keydown", (keyEvent) => {
        if (keyEvent.key === "Enter") {
          sendTyped();
        }
      });
    }
    const dialogInput = document.getElementById("dialog-input");
    if (dialogInput) {
      dialogInput.addEventListener("keydown", (keyEvent) => {
        if (keyEvent.key === "Enter" && dialog.kind === "ask") {
          respond(dialog.token, dialogInput.value.trim());
        }
        if (keyEvent.key === "Escape") {
          respond(dialog.token, dialog.kind === "confirm" ? false : null);
        }
      });
    }
    const dialogX = document.getElementById("dialog-x");
    if (dialogX) {
      dialogX.addEventListener("click", () =>
        respond(dialog.token, dialog.kind === "confirm" ? false : null));
    }
    document.addEventListener("keydown", (keyEvent) => {
      if (keyEvent.key === "Escape" && dialog.token !== null) {
        respond(dialog.token, dialog.kind === "confirm" ? false : null);   // closing is a No
      }
      // Enter on an approval is Yes, so no mouse is needed (Bilal, 9 Nov). A question box
      // handles its own Enter, with what was typed.
      // Not in the first half second: an Enter meant for the message box must not approve
      if (keyEvent.key === "Enter" && dialog.token !== null && dialog.kind === "confirm"
          && Date.now() - (dialog.openedAt || 0) > 500) {
        keyEvent.preventDefault();
        respond(dialog.token, true);
      }
    });
    const mic = document.getElementById("ask-mic");
    if (mic) {
      mic.addEventListener("click", () => pywebview.api.talk());
    }
    const send = document.getElementById("ask-send");
    if (send) {
      send.addEventListener("click", sendTyped);
    }
    // The Home box sends the same way, then shows the conversation
    const homeInput = document.getElementById("home-input");
    const sendFromHome = function () {
      const text = homeInput.value.trim();
      if (!text) {
        return;
      }
      homeInput.value = "";
      pywebview.api.send(text);
      showPage("assistant");
    };
    if (homeInput) {
      homeInput.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
          sendFromHome();
        }
      });
    }
    const homeSend = document.getElementById("home-send");
    if (homeSend) {
      homeSend.addEventListener("click", sendFromHome);
    }
    const homeMic = document.getElementById("home-mic");
    if (homeMic) {
      homeMic.addEventListener("click", function () { pywebview.api.talk(); });
    }
    const appsSearch = document.getElementById("apps-search");
    if (appsSearch) {
      let searchTimer = null;
      appsSearch.addEventListener("input", function () {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(loadApps, 180);
      });
    }
    const appsRefresh = document.getElementById("apps-refresh");
    if (appsRefresh) appsRefresh.addEventListener("click", loadApps);
    const folderSelect = document.getElementById("files-folder");
    if (folderSelect) folderSelect.addEventListener("change", function () {
      fileFolder = folderSelect.value;
      loadFiles(fileFolder, "");
    });
    const filesSearch = document.getElementById("files-search");
    const searchFiles = function () { loadFiles(fileFolder, filesSearch ? filesSearch.value.trim() : ""); };
    if (filesSearch) filesSearch.addEventListener("keydown", function (event) {
      if (event.key === "Enter") searchFiles();
    });
    const filesSearchButton = document.getElementById("files-search-button");
    if (filesSearchButton) filesSearchButton.addEventListener("click", searchFiles);
    const filesRefresh = document.getElementById("files-refresh");
    if (filesRefresh) filesRefresh.addEventListener("click", function () { loadFiles(fileFolder, ""); });
    const filesUp = document.getElementById("files-up");
    if (filesUp) filesUp.addEventListener("click", function () {
      if (fileFolder) loadFiles(parentFolder(fileFolder), "");
    });
    const activityRefresh = document.getElementById("activity-refresh");
    if (activityRefresh) activityRefresh.addEventListener("click", loadActivity);
    // Hidden or minimised: the clip stops, so a window nobody sees costs nothing
    document.addEventListener("visibilitychange", function () {
      companionState(companionNow);
      const systemPage = document.getElementById("page-system");
      if (document.hidden) stopSystemPolling();
      else if (systemPage && systemPage.classList.contains("is-active")) startSystemPolling();
    });
    tickClock();
    setInterval(tickClock, 1000);
    greeting();
    loadHistory();
  }

  const loaded = document.readyState !== "loading";
  if (loaded) {
    initPage();
  } else {
    document.addEventListener("DOMContentLoaded", initPage);
  }

  return { receive: receive, showPage: showPage };
})();

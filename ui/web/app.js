"use strict";

/* KOVA dashboard page, plain JS, no framework. Everything Python sends arrives
   as one event through window.kova.receive; every call back into Python goes
   through pywebview.api. Nothing is fetched from the internet. */

window.kova = (function () {
  const handlers = {
    state: applyState,
    boot: applyBoot,
    chat: applyChat,
    notice: applyNotice,
    trace: applyTrace,
    prompt: applyPrompt,
    stats: applyStats,
    memory: applyMemoryRows,
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
  }

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
    const still = state === "asleep" || state === "muted" || document.hidden;
    if (still) {
      video.pause();
    } else {
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
      line.textContent = event.assistant + " · your desktop assistant";
    }
    if (brain) {
      brain.textContent = event.brain;
    }
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
    const log = document.getElementById("chat-log");
    if (!log) {
      return;
    }
    dropEmpty("chat-empty");
    log.appendChild(bubble(event.who, event.text, event.note));
    trim(log);
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
    const box = document.getElementById("trace");
    if (!box) {
      return;
    }
    dropEmpty("trace-empty");
    const text = event.line.trim();
    const line = document.createElement("div");
    line.className = "trace-line" +
      (text.startsWith("→") ? " is-call" : text.startsWith("←") ? " is-back" : "");
    line.textContent = event.line;
    box.appendChild(line);
    while (box.children.length > TRACE_KEEP) {
      box.removeChild(box.firstChild);
    }
    box.scrollTop = box.scrollHeight;
    addRecent(text);
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

    const voice = field("Voice", "how Max sounds");
    const voiceSeg = segmented(["male", "female"], current.VOICE);
    voice.row.appendChild(voiceSeg);   // was built but never placed: the row showed no control
    form.appendChild(voice.row);

    form.appendChild(switchField("Proactive alerts", "battery, RAM and disk warnings",
      current.ALERTS_ON, "alerts"));

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
        VOICE: voiceSeg.value,
        ALERTS_ON: document.getElementById("set-alerts").checked,
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
        status.textContent = text;
      });
    });
    actions.appendChild(save);
    form.appendChild(actions);
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
      element.textContent = part + ", " + names.title;
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
    clock.textContent = day + " " + date + " · " + time;
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
    });
    const mic = document.getElementById("ask-mic");
    if (mic) {
      mic.addEventListener("click", () => pywebview.api.talk());
    }
    const send = document.getElementById("ask-send");
    if (send) {
      send.addEventListener("click", sendTyped);
    }
    // Hidden or minimised: the clip stops, so a window nobody sees costs nothing
    document.addEventListener("visibilitychange", () => companionState(companionNow));
    tickClock();
    setInterval(tickClock, 1000);
    greeting();
  }

  const loaded = document.readyState !== "loading";
  if (loaded) {
    initPage();
  } else {
    document.addEventListener("DOMContentLoaded", initPage);
  }

  return { receive: receive, showPage: showPage };
})();

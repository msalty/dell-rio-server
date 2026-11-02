"use strict";

/* RioServer NG web interface: browse the library, play in the browser or on
 * a receiver, edit the play queue and playlists, podcasts, alarms. No build
 * step and no dependencies. */

const $ = (sel, root = document) => root.querySelector(sel);
const view = $("#view");

// ---------------------------------------------------------------- helpers

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v; // only used with static markup
    else if (k === "value" && "value" in el) el.value = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(5)) {
    if (c === undefined || c === null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const ICON = {
  play: '<svg viewBox="0 0 24 24"><path d="M7 4.5v15l13-7.5z" class="fill"/></svg>',
  shuffle: '<svg viewBox="0 0 24 24"><path d="M3 7h3c4 0 6 10 10 10h4M17 14l3 3-3 3M3 17h3c1.6 0 2.8-1.5 3.9-3.3M13.5 9.8C14.4 8.3 15.4 7 17 7h3M17 4l3 3-3 3"/></svg>',
  repeat: '<svg viewBox="0 0 24 24"><path d="M4 11V9a3 3 0 0 1 3-3h12l-3-3M20 13v2a3 3 0 0 1-3 3H5l3 3"/></svg>',
  speaker: '<svg viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9z" class="fill"/><path d="M16 9a4 4 0 0 1 0 6"/></svg>',
  prev: '<svg viewBox="0 0 24 24"><path d="M6 5v14M19 5 9 12l10 7z" class="fill"/></svg>',
  next: '<svg viewBox="0 0 24 24"><path d="M18 5v14M5 5l10 7-10 7z" class="fill"/></svg>',
  pause: '<svg viewBox="0 0 24 24"><path d="M7 4h3.5v16H7zM13.5 4H17v16h-3.5z" class="fill"/></svg>',
  stop: '<svg viewBox="0 0 24 24"><path d="M6 6h12v12H6z" class="fill"/></svg>',
  moon: '<svg viewBox="0 0 24 24"><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/></svg>',
  sun: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
  monitor: '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/></svg>',
  more: '<svg viewBox="0 0 24 24"><circle cx="5" cy="12" r="1.6" class="fill"/><circle cx="12" cy="12" r="1.6" class="fill"/><circle cx="19" cy="12" r="1.6" class="fill"/></svg>',
  playNext: '<svg viewBox="0 0 24 24"><path d="M4 6h10M4 12h10M4 18h6M15 13l6 3.5-6 3.5z"/></svg>',
  addQueue: '<svg viewBox="0 0 24 24"><path d="M4 6h12M4 12h12M4 18h7M17 15v6M14 18h6"/></svg>',
  addList: '<svg viewBox="0 0 24 24"><path d="M4 6h11M4 12h11M4 18h6M18 13v8M14 17h8"/></svg>',
  album: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.5"/></svg>',
  person: '<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></svg>',
  x: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg>',
  handle: '<svg viewBox="0 0 24 24"><path d="M5 9h14M5 15h14"/></svg>',
  down: '<svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>',
  back: '<svg viewBox="0 0 24 24"><path d="m15 6-6 6 6 6"/></svg>',
  cast: '<svg viewBox="0 0 24 24"><path d="M3 8V6a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-6"/><path d="M3 12a8 8 0 0 1 8 8M3 16a4 4 0 0 1 4 4"/><circle class="fill" cx="3.5" cy="19.5" r="1.5"/></svg>',
  edit: '<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16zM14 6l4 4"/></svg>',
  check: '<svg viewBox="0 0 24 24"><path d="m5 12 5 5 9-10"/></svg>',
  download: '<svg viewBox="0 0 24 24"><path d="M12 4v11M7 10l5 5 5-5M5 20h14"/></svg>',
  refresh: '<svg viewBox="0 0 24 24"><path d="M20 11a8 8 0 0 0-14.3-4.9L4 8M4 4v4h4M4 13a8 8 0 0 0 14.3 4.9L20 16M20 20v-4h-4"/></svg>',
  list: '<svg viewBox="0 0 24 24"><path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/></svg>',
  podcast: '<svg viewBox="0 0 24 24"><circle cx="12" cy="11" r="3"/><path d="M12 14v7M6.3 16.5a8 8 0 1 1 11.4 0M8.8 13.9a4.5 4.5 0 1 1 6.4 0"/></svg>',
  bell: '<svg viewBox="0 0 24 24"><path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4zM10 21h4"/></svg>',
  move: '<svg viewBox="0 0 24 24"><path d="M14 5h6v6M20 5l-9 9M10 5H5a1 1 0 0 0-1 1v13a1 1 0 0 0 1 1h13a1 1 0 0 0 1-1v-5"/></svg>',
  eq: '<span class="eq" aria-hidden="true"><i></i><i></i><i></i></span>',
};
const icon = (name) => h("span", { html: ICON[name], style: "display:inline-flex" });

// The firmware treats buffering as playing (pressing toggle pauses it), so the
// play/pause button should show pause for both.
const rxPlaying = (st) => !!st && (st.st === "play" || st.st === "buffer");

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: opts.body && typeof opts.body === "string" && !opts.raw ? { "Content-Type": "application/json" } : undefined,
  });
  if (!res.ok) throw new Error((await res.text()) || res.statusText);
  return res.json();
}
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });
const del = (path) => api(path, { method: "DELETE" });

function fmtTime(ms) {
  const s = Math.max(0, Math.round((ms || 0) / 1000));
  const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60;
  return hh ? `${hh}:${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}` : `${mm}:${String(ss).padStart(2, "0")}`;
}
function fmtLong(ms) {
  if (ms > 0 && ms < 60000) return `${Math.max(1, Math.round(ms / 1000))} sec`;
  const m = Math.round((ms || 0) / 60000);
  return m >= 60 ? `${Math.floor(m / 60)} hr ${m % 60} min` : `${m} min`;
}
function fmtAgo(t) {
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} hr ago`;
  if (s < 7 * 86400) return `${Math.round(s / 86400)} days ago`;
  return new Date(t * 1000).toLocaleDateString();
}
const fmtDate = (t) => t ? new Date(t * 1000).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "";
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

function hashHue(text) {
  let x = 0;
  for (const ch of text || "") x = (x * 31 + ch.charCodeAt(0)) >>> 0;
  return x % 360;
}
function initials(text) {
  const words = (text || "?").replace(/^the\s+/i, "").split(/\s+/).filter(Boolean);
  return (words.slice(0, 2).map((w) => w[0]).join("") || "?").toUpperCase();
}
function placeholder(text) {
  const hue = hashHue(text);
  return h("div", {
    class: "ph",
    style: `background: linear-gradient(135deg, hsl(${hue} 55% 42%), hsl(${(hue + 50) % 360} 60% 28%))`,
  }, initials(text));
}
// Stand-in for an artist without any artwork: a record whose label colour
// and initials come from the name.
function vinyl(text) {
  return h("div", { class: "vinyl", style: `--hue: ${hashHue(text)}` }, h("span", {}, initials(text)));
}
// The artist's photo (artist.jpg in their folder) or their newest cover.
function artistArt(a, cls = "avatar") {
  const el = h("div", { class: cls });
  const img = h("img", { src: `/api/art/artist/${a.id}`, alt: "", loading: "lazy" });
  img.addEventListener("error", () => img.replaceWith(vinyl(a.name)));
  el.append(img);
  return el;
}
const albumArtUrl = (albumId) => `/api/art/album/${albumId}`;

function cover(albumId, title, extra, src) {
  const el = h("div", { class: "cover" });
  if (albumId || src) {
    const img = h("img", { src: src || albumArtUrl(albumId), alt: "", loading: "lazy" });
    img.addEventListener("error", () => img.replaceWith(placeholder(title)));
    el.append(img);
  } else {
    el.append(placeholder(title));
  }
  if (extra) el.append(extra);
  return el;
}

let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
}
const fail = (e) => toast(e.message || String(e));

function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch { /* private mode */ }
  return null;
}

// A slider the user is dragging (or just let go of) must not be moved by
// state polled from a receiver, which lags behind what they asked for.
function trackBusy(input) {
  const touch = () => { input._busyUntil = Date.now() + 1500; };
  input.addEventListener("pointerdown", () => { input._held = true; touch(); });
  input.addEventListener("pointerup", () => { input._held = false; touch(); });
  input.addEventListener("pointercancel", () => { input._held = false; touch(); });
  input.addEventListener("input", touch);
  return input;
}
const isBusy = (input) => !!input && (input._held || Date.now() < (input._busyUntil || 0));

// Sends a receiver volume while the slider moves: at most every 150 ms, and
// always the final value. The server drops values the receiver hasn't
// collected yet, so nothing piles up.
function volumeSender(getId) {
  let last = 0, timer = null, value = null;
  const send = () => {
    timer = null;
    last = Date.now();
    post(`/api/receivers/${getId()}/command`, { cmd: "vol", value }).catch(fail);
  };
  return (v) => {
    value = v;
    const wait = 150 - (Date.now() - last);
    if (wait <= 0 && !timer) send();
    else if (!timer) timer = setTimeout(send, Math.max(0, wait));
  };
}

// No pinch-zoom on iPhone and iPad (Safari's own gesture events), so the
// installed app doesn't zoom by accident.
for (const ev of ["gesturestart", "gesturechange"]) document.addEventListener(ev, (e) => e.preventDefault(), { passive: false });

// ---------------------------------------------------------------- theme

const THEME_COLORS = { dark: "#12181c", light: "#eef2f3" };
function applyTheme(choice) {
  const root = document.documentElement;
  if (choice === "light" || choice === "dark") root.dataset.theme = choice;
  else delete root.dataset.theme;
  store("rio.theme", choice === "light" || choice === "dark" ? choice : null);
  for (const meta of document.querySelectorAll('meta[name="theme-color"]')) {
    const media = (meta.getAttribute("media") || "").includes("light") ? "light" : "dark";
    meta.setAttribute("content", THEME_COLORS[root.dataset.theme || media]);
  }
}
applyTheme(store("rio.theme") || "system");

// ---------------------------------------------------------------- colours from album art

// The main colour of a cover: small canvas, colourful pixels count more,
// near-black and near-white ones hardly at all.
const artColors = new Map();
function artColor(src) {
  if (!src) return Promise.resolve(null);
  if (artColors.has(src)) return artColors.get(src);
  const p = new Promise((resolve) => {
    const img = new Image();
    img.decoding = "async";
    img.onload = () => {
      try {
        const n = 24;
        const c = document.createElement("canvas");
        c.width = c.height = n;
        const g = c.getContext("2d", { willReadFrequently: true });
        g.drawImage(img, 0, 0, n, n);
        const d = g.getImageData(0, 0, n, n).data;
        let r = 0, gg = 0, b = 0, wsum = 0;
        for (let i = 0; i < d.length; i += 4) {
          const R = d[i], G = d[i + 1], B = d[i + 2];
          const max = Math.max(R, G, B), min = Math.min(R, G, B);
          const sat = max ? (max - min) / max : 0;
          const lum = (max + min) / 510;
          const w = 0.05 + sat * sat * (lum > 0.08 && lum < 0.92 ? 1 : 0.1);
          r += R * w; gg += G * w; b += B * w; wsum += w;
        }
        resolve(wsum ? `rgb(${Math.round(r / wsum)}, ${Math.round(gg / wsum)}, ${Math.round(b / wsum)})` : null);
      } catch {
        resolve(null);
      }
    };
    img.onerror = () => resolve(null);
    img.src = src;
  });
  artColors.set(src, p);
  return p;
}
function tintFrom(el, src) {
  artColor(src).then((c) => { if (c) el.style.setProperty("--art", c); else el.style.removeProperty("--art"); });
}

// ---------------------------------------------------------------- the browser's player

const audio = $("#audio");
const clientId = store("rio.client") || (() => {
  const id = Math.random().toString(36).slice(2, 12);
  store("rio.client", id);
  return id;
})();

const Player = {
  target: store("rio.target") || "",
  queue: [],
  orig: [],
  index: -1,
  shuffle: false,
  repeat: "off",
  receivers: [],

  get current() { return this.queue[this.index]; },
  get receiver() { return this.receivers.find((r) => r.id === this.target); },

  async playTracks(tracks, index = 0, { shuffle = false, startMs = 0 } = {}) {
    if (!tracks.length) return;
    if (this.target) {
      try {
        await post(`/api/receivers/${this.target}/play`, {
          track_ids: tracks.map((t) => t.id), index, shuffle, start_ms: startMs || 0,
        });
        toast(`Playing on ${this.receiver ? this.receiver.name : this.target}`);
        pollReceivers();
        Queue.changed();
      } catch (e) {
        fail(e);
      }
      return;
    }
    this.orig = tracks.map((t) => ({ ...t }));
    let order = this.orig.slice();
    if (shuffle || this.shuffle) {
      const first = order.splice(shuffle ? Math.floor(Math.random() * order.length) : index, 1);
      shuffleInPlace(order);
      order = first.concat(order);
      index = 0;
      this.shuffle = true;
    }
    this.queue = order;
    this.start(index, startMs);
  },

  async start(i, startMs) {
    reportProgress(audio.paused ? "pause" : "play");
    this.index = i;
    const t = this.current;
    if (!t) return;
    audio.src = `/api/stream/${t.id}`;
    // Podcast episodes carry on where any device left them.
    let at = startMs || 0;
    if (!startMs && t.podcast_id) {
      try { at = (await api(`/api/tracks/${t.id}/start?client=${clientId}`)).start_ms; } catch { at = 0; }
    }
    if (at) audio.addEventListener("loadedmetadata", () => { audio.currentTime = at / 1000; }, { once: true });
    audio.play().catch(() => {});
    renderNowPlaying();
    markPlaying();
    Queue.changed();
  },

  toggle() {
    if (this.target) return this.command("toggle");
    if (!this.current) return;
    audio.paused ? audio.play() : audio.pause();
  },
  next() {
    if (this.target) return this.command("next");
    if (this.index + 1 < this.queue.length) this.start(this.index + 1);
    else if (this.repeat === "all" && this.queue.length) this.start(0);
  },
  prev() {
    if (this.target) return this.command("prev");
    if (audio.currentTime > 3 || this.index === 0) audio.currentTime = 0;
    else this.start(this.index - 1, 0);
  },
  async command(cmd, value) {
    try {
      await post(`/api/receivers/${this.target}/command`, value === undefined ? { cmd } : { cmd, value });
      setTimeout(pollReceivers, 350); // the receiver collects commands at once
    } catch (e) {
      fail(e);
    }
  },

  // Queue changes in the browser (the same operations a receiver takes).
  edit(op, { tracks = [], pos = 0, to = 0, on = false } = {}) {
    const copies = tracks.map((t) => ({ ...t }));
    const cur = this.current;
    if ((op === "next" || op === "add") && !cur) {
      this.playTracks(copies, 0);
      return;
    }
    if (op === "next") {
      this.queue.splice(this.index + 1, 0, ...copies);
      const o = this.orig.indexOf(cur);
      this.orig.splice(o + 1, 0, ...copies);
    } else if (op === "add") {
      this.queue.push(...copies);
      this.orig.push(...copies);
    } else if (op === "move") {
      if (pos === to || pos < 0 || to < 0 || pos >= this.queue.length || to >= this.queue.length) return;
      const [e] = this.queue.splice(pos, 1);
      this.queue.splice(to, 0, e);
      if (pos === this.index) this.index = to;
      else if (pos < this.index && this.index <= to) this.index--;
      else if (to <= this.index && this.index < pos) this.index++;
      if (!this.shuffle) this.orig = this.queue.slice();
    } else if (op === "remove") {
      if (pos === this.index) throw new Error("Skip the track that is playing instead");
      const [e] = this.queue.splice(pos, 1);
      if (pos < this.index) this.index--;
      const o = this.orig.indexOf(e);
      if (o >= 0) this.orig.splice(o, 1);
    } else if (op === "clear") {
      for (const e of this.queue.splice(this.index + 1)) {
        const o = this.orig.indexOf(e);
        if (o >= 0) this.orig.splice(o, 1);
      }
    } else if (op === "jump") {
      this.start(pos, 0);
      return;
    } else if (op === "shuffle") {
      if (on === this.shuffle) return;
      this.shuffle = on;
      if (on) {
        const rest = this.queue.slice(this.index + 1);
        shuffleInPlace(rest);
        this.queue.splice(this.index + 1, rest.length, ...rest);
      } else if (cur) {
        this.queue = this.orig.slice();
        this.index = Math.max(0, this.queue.indexOf(cur));
      }
    }
    Queue.changed();
  },
};

function shuffleInPlace(a) {
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

// The play history and podcast positions hear about the browser's playing too.
let lastReport = 0;
function reportProgress(st, posMs) {
  const t = Player.current;
  if (Player.target || !t) return;
  lastReport = Date.now();
  const pos = posMs !== undefined ? posMs : Math.round((audio.currentTime || 0) * 1000);
  fetch("/api/progress", {
    method: "POST", headers: { "Content-Type": "application/json" }, keepalive: true,
    body: JSON.stringify({ track_id: t.id, pos_ms: pos, st, client: clientId }),
  }).catch(() => {});
}

audio.addEventListener("ended", () => {
  const t = Player.current;
  if (t) reportProgress("play", t.duration_ms || Math.round(audio.duration * 1000));
  if (Player.repeat === "one") { audio.currentTime = 0; audio.play(); return; }
  Player.next();
});
audio.addEventListener("play", () => { renderNowPlaying(); reportProgress("play"); });
audio.addEventListener("pause", () => { renderNowPlaying(); if (!audio.ended) reportProgress("pause"); });
audio.addEventListener("timeupdate", () => {
  renderProgress();
  if (!audio.paused && Date.now() - lastReport > 10000) reportProgress("play");
});
window.addEventListener("pagehide", () => { if (!audio.paused) reportProgress("pause"); });
audio.volume = Number(store("rio.volume") || 80) / 100;
$("#volume").value = Math.round(audio.volume * 100);

// ---------------------------------------------------------------- the play queue, wherever it is

const Queue = {
  cache: null,
  key: "",
  listeners: new Set(),

  // {known, editable, index, shuffle, tracks}
  async get(force = false) {
    if (!Player.target) {
      return { known: true, editable: true, index: Player.index, shuffle: Player.shuffle, tracks: Player.queue };
    }
    const key = this.remoteKey();
    if (!force && this.cache && this.key === key) return this.cache;
    const data = await api(`/api/receivers/${Player.target}/queue`);
    this.cache = data;
    this.key = this.remoteKey();
    return data;
  },
  remoteKey() {
    const r = Player.receiver, st = (r && r.state) || {};
    return [Player.target, st.qn, st.qi, st.id, st.shuf].join("|");
  },
  async edit(op, args = {}) {
    if (!Player.target) {
      Player.edit(op, args);
      return;
    }
    const body = { op };
    if (args.tracks) body.track_ids = args.tracks.map((t) => t.id);
    if ("pos" in args) body.pos = args.pos;
    if ("to" in args) body.to = args.to;
    if ("on" in args) body.on = args.on;
    this.cache = await post(`/api/receivers/${Player.target}/queue`, body);
    this.key = "";
    setTimeout(pollReceivers, 400);
    this.changed(true);
  },
  changed(keepCache) {
    if (!keepCache) this.cache = null;
    for (const fn of this.listeners) fn();
  },
};

async function playNext(tracks) {
  try {
    await Queue.edit("next", { tracks });
    toast(tracks.length === 1 ? `“${tracks[0].title}” plays next` : `${plural(tracks.length, "song")} play next`);
  } catch (e) { fail(e); }
}
async function addToQueue(tracks) {
  try {
    await Queue.edit("add", { tracks });
    toast(tracks.length === 1 ? `Added “${tracks[0].title}” to Up Next` : `Added ${plural(tracks.length, "song")} to Up Next`);
  } catch (e) { fail(e); }
}

// ---------------------------------------------------------------- player bar

$("#btn-play").addEventListener("click", () => Player.toggle());
$("#np-art").addEventListener("click", () => NowPlaying.open());
$("#np-text").addEventListener("click", () => NowPlaying.open());
$("#np-text").addEventListener("keydown", (e) => { if (e.key === "Enter") NowPlaying.open(); });
$("#btn-queue").addEventListener("click", () => NowPlaying.toggle(true));
$("#btn-next").addEventListener("click", () => Player.next());
$("#btn-prev").addEventListener("click", () => Player.prev());

function toggleShuffle() {
  if (Player.target) {
    const st = (Player.receiver && Player.receiver.state) || {};
    const on = st.shuf !== "1";
    Player.command("shuffle", on ? "1" : "0").then(() => Queue.changed());
    toast(on ? "Shuffle on" : "Shuffle off");
    return;
  }
  Player.edit("shuffle", { on: !Player.shuffle });
  renderModes();
  toast(Player.shuffle ? "Shuffle on" : "Shuffle off");
}
function cycleRepeat() {
  const next = { off: "all", all: "one", one: "off" };
  if (Player.target) {
    const st = (Player.receiver && Player.receiver.state) || {};
    const want = next[st.rep || "off"] || "all";
    Player.command("repeat", want);
    toast(`Repeat ${want}`);
    return;
  }
  Player.repeat = next[Player.repeat];
  renderModes();
  toast(`Repeat ${Player.repeat}`);
}
$("#btn-shuffle").addEventListener("click", toggleShuffle);
$("#btn-repeat").addEventListener("click", cycleRepeat);

function seekTo(frac) {
  if (Player.target) {
    const r = Player.receiver, t = r && r.state && r.state.track;
    if (t) Player.command("seek", Math.round(frac * t.duration_ms));
  } else if (audio.duration) {
    audio.currentTime = frac * audio.duration;
  }
}
$("#seek").addEventListener("input", (e) => seekTo(e.target.value / 1000));
trackBusy($("#volume"));
const sendTargetVolume = volumeSender(() => Player.target);
function setVolume(v) {
  if (Player.target) {
    sendTargetVolume(v);
  } else {
    audio.volume = v / 100;
    store("rio.volume", String(v));
  }
  for (const el of document.querySelectorAll(".vol-input")) if (!isBusy(el)) el.value = v;
}
$("#volume").classList.add("vol-input");
$("#volume").addEventListener("input", (e) => setVolume(Number(e.target.value)));

// Space play/pause, N/P next/previous, +/- volume, / search, Q Now Playing.
function nudgeVolume(delta) {
  const slider = $("#volume");
  slider.value = Math.max(0, Math.min(100, Number(slider.value) + delta));
  setVolume(Number(slider.value));
}
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (closeMenu() || closeDialog()) return;
    if (!$("#nowplaying").hidden) { NowPlaying.close(); return; }
    if ($("#sidebar").classList.contains("open")) { toggleSidebar(false); return; }
  }
  if (e.ctrlKey || e.metaKey || e.altKey || /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) return;
  if (!$("#menu-layer").hidden || document.querySelector(".dialog-back")) return;
  const action = {
    " ": () => Player.toggle(), n: () => Player.next(), p: () => Player.prev(),
    "+": () => nudgeVolume(5), "=": () => nudgeVolume(5), "-": () => nudgeVolume(-5),
    "/": () => $("#search").focus(), q: () => NowPlaying.toggle(),
    s: () => toggleShuffle(), r: () => cycleRepeat(),
  }[e.key.toLowerCase()];
  if (action) {
    e.preventDefault();
    action();
  }
});

// Lock-screen / media-key controls while playing in the browser.
function updateMediaSession(t) {
  if (!("mediaSession" in navigator)) return;
  if (Player.target || !t) {
    navigator.mediaSession.metadata = null;
    return;
  }
  navigator.mediaSession.metadata = new MediaMetadata({
    title: t.title, artist: t.artist, album: t.album,
    artwork: [{ src: albumArtUrl(t.album_id), sizes: "512x512" }],
  });
}
if ("mediaSession" in navigator) {
  const ms = navigator.mediaSession;
  ms.setActionHandler("play", () => Player.toggle());
  ms.setActionHandler("pause", () => Player.toggle());
  ms.setActionHandler("nexttrack", () => Player.next());
  ms.setActionHandler("previoustrack", () => Player.prev());
  try { ms.setActionHandler("seekto", (d) => { if (!Player.target) audio.currentTime = d.seekTime; }); } catch { /* older browsers */ }
}

// What is playing where Music goes, as one record.
function nowState() {
  if (Player.target) {
    const r = Player.receiver;
    const st = (r && r.state) || {};
    const t = st.track;
    return {
      // an offline receiver isn't playing, whatever it last reported
      t, playing: !!r && r.online && rxPlaying(st), remote: true, r, st,
      shuffle: st.shuf === "1", repeat: st.rep || "off",
      sub: t ? `${t.artist} — ${t.album}` : (r ? `${r.name} · ${!r.online ? "offline" : st.app ? `in use by ${st.app}` : "idle"}` : ""),
    };
  }
  const t = Player.current;
  return { t, playing: !!t && !audio.paused, remote: false, shuffle: Player.shuffle, repeat: Player.repeat,
    sub: t ? `${t.artist} — ${t.album}` : "" };
}

function renderModes() {
  const ns = nowState();
  for (const b of document.querySelectorAll(".mode-shuffle")) b.classList.toggle("on", ns.shuffle);
  for (const b of document.querySelectorAll(".mode-repeat")) {
    b.classList.toggle("on", ns.repeat !== "off");
    b.classList.toggle("one", ns.repeat === "one");
    b.title = `Repeat: ${ns.repeat}`;
  }
}
$("#btn-shuffle").classList.add("mode-shuffle");
$("#btn-repeat").classList.add("mode-repeat");

// The pill in the player saying where the music goes, when that isn't this
// browser; tapping it switches. Grey when that player can't play right now.
function renderWherePill(ns) {
  const pill = $("#where-pill");
  pill.hidden = !Player.target;
  if (!Player.target) return;
  const r = ns.r;
  const self = RX.on && Player.target === RX.id;
  const name = self ? `This device · ${RX.name}` : r ? r.name : "Receiver";
  const trouble = !r || !r.online ? "offline" : ns.st && ns.st.app ? `in use by ${ns.st.app}` : "";
  const label = trouble ? `${name} · ${trouble}` : name;
  if (pill.dataset.label === label) return;
  pill.dataset.label = label;
  pill.classList.toggle("off", !!trouble);
  pill.replaceChildren(h("span", { class: "ic", html: ICON.cast }), h("span", { class: "nm" }, label));
  pill.title = trouble ? `${label}. Tap to play somewhere else.` : `Playing on ${name}. Tap to play somewhere else.`;
  pill.setAttribute("aria-label", pill.title);
}
$("#where-pill").addEventListener("click", (e) => {
  const options = (RX.on ? [] : [{ id: "", name: "This browser", icon: "monitor" }]).concat(
    Player.receivers.filter(canTarget).map((r) => ({
      id: r.id, icon: "cast",
      name: `${r.id === RX.id ? `This device (${r.name})` : r.name}${r.online ? "" : " · offline"}`,
    })));
  showMenu(e.currentTarget, options.map((o) => ({
    label: o.name, icon: o.id === Player.target ? "check" : o.icon, run: () => chooseTarget(o.id),
  })), "Play on");
});

function renderNowPlaying() {
  const ns = nowState();
  renderWherePill(ns);
  const { t, playing } = ns;
  if (ns.remote && ns.st && ns.st.vol) {
    for (const el of document.querySelectorAll(".vol-input")) if (!isBusy(el)) el.value = ns.st.vol;
  }
  document.body.classList.toggle("is-playing", !!playing);
  for (const el of document.querySelectorAll(".play-host")) el.classList.toggle("playing", !!playing);
  $("#btn-play").setAttribute("aria-label", playing ? "Pause" : "Play");
  $("#np-title").textContent = t ? t.title : "Nothing playing";
  $("#np-sub").textContent = t ? ns.sub : (ns.sub || "Pick something from your library");
  const msKey = !Player.target && t ? String(t.id) : "";
  if (Player._msKey !== msKey) { Player._msKey = msKey; updateMediaSession(t); }
  const art = $("#np-art");
  const key = t ? `${t.album_id}` : "";
  if (art.dataset.key !== key) {
    art.dataset.key = key;
    art.replaceChildren(t ? cover(t.album_id, t.album) : "");
    tintFrom(document.documentElement, t ? albumArtUrl(t.album_id) : null);
  }
  renderModes();
  NowPlaying.render(ns);
  renderProgress();
}

function progressNow() {
  let pos = 0, dur = 0;
  if (Player.target) {
    const st = Player.receiver && Player.receiver.state;
    if (st && st.track) {
      dur = st.track.duration_ms;
      pos = Number(st.pos || 0);
      if (st.st === "play" && Player._statusAt && Player.receiver.online) pos += Date.now() - Player._statusAt;
      pos = Math.min(pos, dur);
    }
  } else if (Player.current) {
    pos = (audio.currentTime || 0) * 1000;
    dur = (audio.duration || Player.current.duration_ms / 1000) * 1000;
  }
  return { pos, dur };
}
function renderProgress() {
  const { pos, dur } = progressNow();
  for (const el of document.querySelectorAll(".t-pos")) el.textContent = fmtTime(pos);
  for (const el of document.querySelectorAll(".t-dur")) el.textContent = fmtTime(dur);
  for (const el of document.querySelectorAll(".seek-input")) {
    if (document.activeElement !== el && !isBusy(el)) el.value = dur ? Math.round((pos / dur) * 1000) : 0;
  }
}
$("#t-pos").classList.add("t-pos");
$("#t-dur").classList.add("t-dur");
$("#seek").classList.add("seek-input");
$("#player").classList.add("play-host");
setInterval(() => { if (Player.target) renderProgress(); }, 500);

function markPlaying() {
  let id = null;
  if (Player.target) {
    const st = Player.receiver && Player.receiver.state;
    id = st && st.track ? st.track.id : null;
  } else if (Player.current) {
    id = Player.current.id;
  }
  document.querySelectorAll("[data-track]").forEach((tr) => {
    tr.classList.toggle("playing", Number(tr.dataset.track) === id);
  });
}

// ---------------------------------------------------------------- menus and dialogs

let menuCleanup = null;
function closeMenu() {
  const layer = $("#menu-layer");
  if (layer.hidden) return false;
  layer.hidden = true;
  layer.replaceChildren();
  if (menuCleanup) menuCleanup();
  menuCleanup = null;
  return true;
}
// items: [{label, icon, run, danger}] or "-" for a line; a "sub" (async
// function returning items) opens in place, with a way back.
function showMenu(anchor, items, title) {
  closeMenu();
  const layer = $("#menu-layer");
  const narrow = window.matchMedia("(max-width: 860px)").matches;
  const menu = h("div", { class: `menu${narrow ? " sheet" : ""}`, role: "menu" });
  const fill = (list, head, back) => {
    menu.replaceChildren(...[
      head ? h("div", { class: "mhead" }, head) : null,
      back ? h("button", { onclick: () => fill(back.items, back.title, back.back) }, icon("back"), "Back") : null,
      ...list.filter(Boolean).map((it) => it === "-" ? h("hr") : h("button", {
        role: "menuitem", class: it.danger ? "danger" : "",
        onclick: async (e) => {
          e.stopPropagation();
          if (it.sub) {
            try { fill(await it.sub(), it.label, { items: list, title: head, back }); } catch (err) { fail(err); }
            return;
          }
          closeMenu();
          try { await it.run(); } catch (err) { fail(err); }
        },
      }, it.icon ? icon(it.icon) : null, it.label))].filter(Boolean));
    const first = menu.querySelector("button");
    if (first) first.focus({ preventScroll: true });
  };
  fill(items, title);
  layer.append(menu);
  layer.hidden = false;
  if (!narrow) {
    let x, y;
    if (anchor && anchor.getBoundingClientRect) {
      const b = anchor.getBoundingClientRect();
      x = b.right; y = b.bottom + 4;
    } else {
      ({ x, y } = anchor || { x: innerWidth / 2, y: innerHeight / 2 });
    }
    const w = menu.offsetWidth, hh = menu.offsetHeight;
    menu.style.left = `${Math.max(8, Math.min(x - (anchor && anchor.getBoundingClientRect ? w : 0), innerWidth - w - 8))}px`;
    menu.style.top = `${Math.max(8, Math.min(y, innerHeight - hh - 8))}px`;
  }
  const away = (e) => { if (!menu.contains(e.target)) { e.preventDefault(); closeMenu(); } };
  setTimeout(() => layer.addEventListener("pointerdown", away), 0);
  menuCleanup = () => layer.removeEventListener("pointerdown", away);
}

// Right-click, or a long press on a touch screen, opens a menu.
function bindContext(el, getItems, getTitle) {
  el.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    showMenu({ x: e.clientX, y: e.clientY }, getItems(), getTitle && getTitle());
  });
  let timer = null, sx = 0, sy = 0;
  el.addEventListener("pointerdown", (e) => {
    if (e.pointerType !== "touch") return;
    sx = e.clientX; sy = e.clientY;
    timer = setTimeout(() => {
      timer = null;
      el._longPressed = true;
      if (navigator.vibrate) navigator.vibrate(10);
      showMenu({ x: sx, y: sy }, getItems(), getTitle && getTitle());
    }, 520);
  });
  const cancel = () => { clearTimeout(timer); timer = null; };
  el.addEventListener("pointermove", (e) => { if (timer && Math.hypot(e.clientX - sx, e.clientY - sy) > 10) cancel(); });
  el.addEventListener("pointerup", cancel);
  el.addEventListener("pointercancel", cancel);
  // the click that ends a long press must not also play the track
  el.addEventListener("click", (e) => {
    if (el._longPressed) { el._longPressed = false; e.stopPropagation(); e.preventDefault(); }
  }, true);
}
function moreButton(getItems, getTitle) {
  return h("button", {
    class: "icon-btn", "aria-label": "More", title: "More", html: ICON.more,
    onclick: (e) => { e.stopPropagation(); showMenu(e.currentTarget, getItems(), getTitle && getTitle()); },
  });
}

let dialogResolve = null;
function closeDialog(value = null) {
  const back = document.querySelector(".dialog-back");
  if (!back) return false;
  back.remove();
  if (dialogResolve) dialogResolve(value);
  dialogResolve = null;
  return true;
}
// A dialog with a form; `collect` turns the form into the value to resolve with.
function dialog(title, body, { ok = "OK", collect = () => true, danger = false } = {}) {
  closeDialog();
  return new Promise((resolve) => {
    dialogResolve = resolve;
    const form = h("form", { class: "dialog", onsubmit: (e) => {
      e.preventDefault();
      const v = collect(form);
      if (v === undefined) return;
      dialogResolve = null;
      back.remove();
      resolve(v);
    } },
    h("h2", {}, title), body,
    h("div", { class: "btn-row" },
      h("button", { type: "button", class: "btn", onclick: () => closeDialog() }, "Cancel"),
      h("button", { type: "submit", class: `btn ${danger ? "danger" : "primary"}` }, ok)));
    const back = h("div", { class: "dialog-back", onclick: (e) => { if (e.target === back) closeDialog(); } }, form);
    document.body.append(back);
    const first = form.querySelector("input, select, textarea, button[type=submit]");
    if (first) first.focus();
  });
}
function askText(title, label, value = "", ok = "Save") {
  const input = h("input", { type: "text", value, maxlength: 80, required: true });
  return dialog(title, h("label", { class: "field" }, label, input), { ok, collect: () => input.value.trim() || undefined });
}
function confirmAction(title, text, ok = "Delete") {
  return dialog(title, h("p", { class: "subtle", style: "margin:0" }, text), { ok, danger: true });
}

// ---------------------------------------------------------------- what a track or album can do

let playlistCache = null;
async function editablePlaylists() {
  if (!playlistCache) playlistCache = (await api("/api/playlists")).filter((p) => p.editable);
  return playlistCache;
}
async function newPlaylist(tracks = []) {
  const name = await askText("New playlist", "Name", "", "Create");
  if (!name) return null;
  const p = await post("/api/playlists", { name, track_ids: tracks.map((t) => t.id) });
  playlistCache = null;
  toast(tracks.length ? `Saved ${plural(tracks.length, "song")} to “${p.name}”` : `Made “${p.name}”`);
  return p;
}
function playlistPicker(getTracks) {
  return async () => {
    const lists = await editablePlaylists();
    return [
      { label: "New playlist…", icon: "addList", run: async () => newPlaylist(await getTracks()) },
      lists.length ? "-" : null,
      ...lists.map((p) => ({ label: p.name, icon: "list", run: async () => {
        const tracks = await getTracks();
        await post(`/api/playlists/${p.id}/add`, { track_ids: tracks.map((t) => t.id) });
        playlistCache = null;
        toast(`Added ${tracks.length === 1 ? `“${tracks[0].title}”` : plural(tracks.length, "song")} to “${p.name}”`);
      } })),
    ];
  };
}

// ctx: {list, index} to play from, {playlist} to offer removing, {queuePos} in Up Next
function trackMenu(t, ctx = {}) {
  const items = [
    ctx.list ? { label: "Play", icon: "play", run: () => Player.playTracks(ctx.list, ctx.index || 0) } : null,
    { label: "Play next", icon: "playNext", run: () => playNext([t]) },
    { label: "Add to Up Next", icon: "addQueue", run: () => addToQueue([t]) },
    { label: "Add to playlist…", icon: "addList", sub: playlistPicker(async () => [t]) },
    "-",
    t.podcast_id ? { label: "Go to podcast", icon: "podcast", run: () => { location.hash = `#/podcasts/${t.podcast_id}`; } }
      : { label: "Go to album", icon: "album", run: () => { location.hash = `#/albums/${t.album_id}`; } },
    t.artist_id && !t.podcast_id ? { label: "Go to artist", icon: "person", run: () => { location.hash = `#/artists/${t.artist_id}`; } } : null,
  ];
  if (ctx.playlist) {
    items.push("-", { label: "Remove from playlist", icon: "trash", danger: true, run: () => ctx.playlist.remove(ctx.index) });
  }
  return items;
}
function albumMenu(al) {
  const tracks = async () => (await api(`/api/albums/${al.id}`)).tracks;
  return [
    { label: "Play", icon: "play", run: async () => Player.playTracks(await tracks(), 0) },
    { label: "Shuffle", icon: "shuffle", run: async () => Player.playTracks(await tracks(), 0, { shuffle: true }) },
    { label: "Play next", icon: "playNext", run: async () => playNext(await tracks()) },
    { label: "Add to Up Next", icon: "addQueue", run: async () => addToQueue(await tracks()) },
    { label: "Add to playlist…", icon: "addList", sub: playlistPicker(tracks) },
    "-",
    { label: "Go to artist", icon: "person", run: () => { location.hash = `#/artists/${al.artist_id}`; } },
  ];
}

// ---------------------------------------------------------------- moving playback between players

function moveTargets(exclude) {
  const list = (RX.on ? [] : [{ id: "", name: "This browser" }]).concat(
    Player.receivers.filter((r) => canTarget(r) && r.online).map((r) => ({ id: r.id, name: r.name })));
  return list.filter((x) => x.id !== exclude);
}
function moveMenu(from) {
  const targets = moveTargets(from);
  if (!targets.length) return [{ label: "No other players are online", run: () => {} }];
  return targets.map((x) => ({ label: x.name, icon: x.id ? "cast" : "monitor", run: () => movePlayback(from, x.id) }));
}
// Hand what `from` plays (queue, track, position) to `to`, and follow it there.
async function movePlayback(from, to) {
  if (from === to) return;
  let toName = "this browser";
  if (!from) {
    const t = Player.current;
    if (!t) { toast("Nothing is playing in this browser"); return; }
    const pos = Math.round((audio.currentTime || 0) * 1000);
    await post(`/api/receivers/${to}/play`, { track_ids: Player.queue.map((x) => x.id), index: Player.index, start_ms: pos });
    reportProgress("pause");
    audio.pause();
    toName = (Player.receivers.find((r) => r.id === to) || {}).name || to;
  } else {
    const res = await post(`/api/receivers/${from}/transfer`, { to });
    toName = res.to;
    if (!to) {
      setTarget("");
      Player.orig = res.tracks.map((t) => ({ ...t }));
      Player.queue = Player.orig.slice();
      Player.shuffle = false;
      Player.start(res.index, res.pos_ms || 1);
      if (!res.playing) audio.addEventListener("playing", () => audio.pause(), { once: true });
    }
  }
  if (to) setTarget(to);
  toast(`Moved to ${toName}`);
  setTimeout(pollReceivers, 400);
}

// ---------------------------------------------------------------- ticker for long titles

// A line too long for its box: it shows the start, then scrolls slowly to
// the end, pauses, glides back and stops. It is also an ordinary sideways
// scroller, so it can be swiped (or scrolled with a trackpad) at any time;
// touching it stops the ticker, and tapping it plays the ticker again.
const TICKER = { hold: 5000, replayHold: 800, speed: 35, endHold: 2000, backSpeed: 160 };
function ticker(el) {
  let raf = 0;
  const fades = () => {
    const max = el.scrollWidth - el.clientWidth;
    el.classList.toggle("fade-l", el.scrollLeft > 1);
    el.classList.toggle("fade-r", max - el.scrollLeft > 1);
  };
  const stop = () => { cancelAnimationFrame(raf); raf = 0; };
  const run = (hold = TICKER.hold) => {
    stop();
    el.scrollLeft = 0;
    fades();
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let phase = "hold", elapsed = 0, last = null, pos = 0, max = 0;
    const ease = (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
    const step = (now) => {
      // Frames stop while the page is hidden; the ticker carries on where it was.
      const dt = last === null ? 0 : Math.min(100, now - last);
      last = now;
      max = el.scrollWidth - el.clientWidth;
      if (max <= 1) { raf = 0; return; } // it fits
      elapsed += dt;
      if (phase === "hold") {
        if (elapsed >= hold) { phase = "out"; elapsed = 0; }
      } else if (phase === "out") {
        pos = Math.min(max, pos + (TICKER.speed * dt) / 1000);
        el.scrollLeft = pos;
        if (pos >= max) { phase = "end"; elapsed = 0; }
      } else if (phase === "end") {
        if (elapsed >= TICKER.endHold) { phase = "back"; elapsed = 0; }
      } else {
        const d = Math.min(1, elapsed / Math.max(600, (max / TICKER.backSpeed) * 1000));
        el.scrollLeft = max * (1 - ease(d));
        if (d >= 1) { raf = 0; return; }
      }
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
  };
  el.classList.add("ticker");
  el.addEventListener("scroll", fades, { passive: true });
  for (const ev of ["pointerdown", "wheel", "touchstart"]) el.addEventListener(ev, stop, { passive: true });
  el.addEventListener("click", (e) => { if (!e.target.closest("a")) run(TICKER.replayHold); });
  window.addEventListener("resize", fades);
  return { run, stop, fades };
}

// ---------------------------------------------------------------- full-screen Now Playing with Up Next

const NowPlaying = {
  el: $("#nowplaying"),
  built: false,
  qkey: "",

  build() {
    if (this.built) return;
    this.built = true;
    const seek = trackBusy(h("input", { type: "range", min: 0, max: 1000, value: 0, class: "seek-input", "aria-label": "Seek",
      oninput: (e) => seekTo(e.target.value / 1000) }));
    const vol = trackBusy(h("input", { type: "range", min: 0, max: 100, value: $("#volume").value, class: "vol-input", "aria-label": "Volume",
      oninput: (e) => setVolume(Number(e.target.value)) }));
    this.bg = h("div", { class: "bg" });
    // On a device that is a receiver, the header says so and opens its menu
    // (the display covers the top bar's pill).
    this.where = h("div", { class: "where", onclick: (e) => { if (RX.on && Player.target === RX.id) rxMenu(e.currentTarget); } });
    this.art = h("div", { class: "big-art" });
    this.title = h("h2");
    this.sub = h("div", { class: "sub" });
    this.tickers = [ticker(this.title), ticker(this.sub)];
    this.qlist = h("div", { class: "qlist" });
    this.qnote = h("div", { class: "note" });
    this.qtools = h("div", { class: "btn-row" });
    this.el.append(this.bg,
      h("div", { class: "np-head" },
        h("button", { class: "icon-btn", "aria-label": "Close", title: "Close (Esc)", html: ICON.down, onclick: () => this.close() }),
        this.where,
        h("button", { class: "icon-btn", "aria-label": "Move playback", title: "Play on another speaker", html: ICON.move,
          onclick: (e) => showMenu(e.currentTarget, moveMenu(Player.target), "Move playback to") })),
      h("div", { class: "np-body" },
        h("div", { class: "np-main play-host" },
          this.art,
          h("div", { class: "titles" }, this.title, this.sub),
          h("div", { class: "scrub" }, h("span", { class: "t-pos" }, "0:00"), seek, h("span", { class: "t-dur" }, "0:00")),
          h("div", { class: "buttons" },
            h("button", { class: "icon-btn mode-shuffle", "aria-label": "Shuffle", html: ICON.shuffle, onclick: toggleShuffle }),
            h("button", { class: "icon-btn", "aria-label": "Previous", html: ICON.prev, onclick: () => Player.prev() }),
            h("button", { class: "play-btn", "aria-label": "Play/pause", onclick: () => Player.toggle(),
              html: '<svg viewBox="0 0 24 24" class="i-play"><path d="M7 4.5v15l13-7.5z" class="fill"/></svg><svg viewBox="0 0 24 24" class="i-pause"><path d="M7 4h3.5v16H7zM13.5 4H17v16h-3.5z" class="fill"/></svg>' }),
            h("button", { class: "icon-btn", "aria-label": "Next", html: ICON.next, onclick: () => Player.next() }),
            h("button", { class: "icon-btn mode-repeat", "aria-label": "Repeat", onclick: cycleRepeat,
              html: ICON.repeat + '<span class="badge-one" aria-hidden="true">1</span>' })),
          h("div", { class: "extras" }, icon("speaker"), vol)),
        h("section", { class: "up-next", "aria-label": "Up Next" },
          h("header", {}, h("h3", {}, "Up Next"), this.qtools),
          this.qnote, this.qlist)));
    Queue.listeners.add(() => { if (!this.el.hidden) this.renderQueue(); });
  },
  open() {
    this.build();
    toggleSidebar(false); // opening it from the player closes the menu
    this.el.hidden = false;
    document.body.style.overflow = "hidden";
    renderNowPlaying();
    this.renderQueue(true);
  },
  close() {
    this.el.hidden = true;
    document.body.style.overflow = "";
    this.tkey = null; // the ticker runs again next time it opens
    for (const tk of this.tickers || []) tk.stop();
  },
  toggle(scrollToQueue) {
    if (this.el.hidden) {
      this.open();
      if (scrollToQueue) {
        setTimeout(() => {
          const body = this.el.querySelector(".np-body"), up = this.el.querySelector(".up-next");
          body.scrollTop += up.getBoundingClientRect().top - body.getBoundingClientRect().top;
        }, 50);
      }
    } else {
      this.close();
    }
  },
  render(ns) {
    if (!this.built || this.el.hidden) return;
    const t = ns.t;
    const self = RX.on && Player.target === RX.id;
    this.where.textContent = self ? `This device · ${RX.name}` : Player.target ? `Playing on ${ns.r ? ns.r.name : "a receiver"}` : "Playing in this browser";
    this.where.classList.toggle("is-self", self);
    const key = t ? String(t.album_id) : "";
    if (this.art.dataset.key !== key) {
      this.art.dataset.key = key;
      this.art.replaceChildren(t ? cover(t.album_id, t.album) : cover(null, "RioServer"));
      this.bg.style.backgroundImage = t ? `url("${albumArtUrl(t.album_id)}")` : "";
    }
    // Only when the song changes: rewriting the text would reset the ticker.
    const tkey = t ? `${t.id}|${t.title}|${t.album}|${t.artist}` : `none|${ns.sub}`;
    if (this.tkey !== tkey) {
      this.tkey = tkey;
      this.title.textContent = t ? t.title : "Nothing playing";
      this.sub.replaceChildren(...(t ? [
        t.podcast_id ? h("a", { href: `#/podcasts/${t.podcast_id}`, onclick: () => this.close() }, t.album)
          : h("a", { href: `#/albums/${t.album_id}`, onclick: () => this.close() }, t.album),
        " · ", t.artist,
      ] : [ns.sub || "Pick something from your library"]));
      this.title.title = this.title.textContent;
      for (const tk of this.tickers) tk.run();
    }
    if (Player.target && Queue.remoteKey() !== this.qkey) this.renderQueue();
  },
  async renderQueue(force) {
    if (!this.built || this.el.hidden) return;
    let q;
    try {
      q = await Queue.get(force);
    } catch (e) {
      this.qnote.textContent = e.message;
      return;
    }
    this.qkey = Queue.remoteKey();
    const tracks = q.tracks || [];
    const idx = q.index;
    this.qtools.replaceChildren(...[
      q.editable && tracks.length > idx + 1 ? h("button", { class: "btn small", onclick: () => Queue.edit("clear").catch(fail) }, "Clear") : null,
      tracks.length ? h("button", { class: "btn small", onclick: () => newPlaylist(tracks).catch(fail) }, "Save") : null].filter(Boolean));
    this.qnote.textContent = !q.known ? "This receiver is playing a list the server doesn't know. Play something from here to see what's next."
      : !q.editable ? "This receiver's firmware can't change its queue. Power-cycle it to load the current firmware."
        : !tracks.length ? "Nothing queued. Use “Play next” or “Add to Up Next” on any song." : "";
    const from = Math.max(0, idx - 3), to = Math.min(tracks.length, idx + 250);
    const rows = [];
    for (let i = from; i < to; i++) {
      const t = tracks[i];
      if (i === idx) rows.push(h("div", { class: "qhead" }, "Now playing"));
      if (i === idx + 1) rows.push(h("div", { class: "qhead" }, "Up next"));
      const editable = q.editable && i !== idx;
      const row = h("div", { class: `qitem${i === idx ? " current" : ""}${i < idx ? " past" : ""}`, "data-pos": i,
        onclick: () => { if (i !== idx) Queue.edit("jump", { pos: i }).catch(fail); } },
        editable && i > idx ? h("span", { class: "handle", "aria-label": "Drag to reorder", html: ICON.handle }) : h("span"),
        h("div", { class: "mini" }, cover(t.album_id, t.album)),
        h("div", { class: "tt" }, h("div", {}, t.title), h("div", { class: "s" }, `${t.artist} · ${fmtTime(t.duration_ms)}`)),
        editable ? h("button", { class: "icon-btn", "aria-label": "Remove", title: "Remove", html: ICON.x,
          onclick: (e) => { e.stopPropagation(); Queue.edit("remove", { pos: i }).catch(fail); } }) : h("span"));
      bindContext(row, () => [
        i !== idx ? { label: "Play now", icon: "play", run: () => Queue.edit("jump", { pos: i }) } : null,
        editable && i > idx + 1 ? { label: "Play next", icon: "playNext", run: () => Queue.edit("move", { pos: i, to: idx + 1 }) } : null,
        { label: "Add to playlist…", icon: "addList", sub: playlistPicker(async () => [t]) },
        { label: "Go to album", icon: "album", run: () => { this.close(); location.hash = t.podcast_id ? `#/podcasts/${t.podcast_id}` : `#/albums/${t.album_id}`; } },
        editable ? "-" : null,
        editable ? { label: "Remove from Up Next", icon: "trash", danger: true, run: () => Queue.edit("remove", { pos: i }) } : null,
      ], () => t.title);
      rows.push(row);
    }
    if (to < tracks.length) rows.push(h("div", { class: "qhead" }, `and ${plural(tracks.length - to, "more song")}`));
    this.qlist.replaceChildren(...rows);
    if (q.editable) {
      sortable(this.qlist, ".qitem", (a, b) => Queue.edit("move", { pos: a, to: b }).catch(fail),
        (pos) => pos > idx);
    }
    // Scroll the list itself (scrollIntoView would also shift the overlay).
    const cur = this.qlist.querySelector(".qitem.current");
    if (cur && force) {
      this.qlist.scrollTop += cur.getBoundingClientRect().top - this.qlist.getBoundingClientRect().top - 30;
    }
  },
};

// Drag rows by their handle to reorder them (mouse and touch).
// onMove(fromPos, toPos) gets the rows' data-pos values; canDrop(pos) says
// where a row may go.
function sortable(container, selector, onMove, canDrop = () => true) {
  for (const handle of container.querySelectorAll(`${selector} .handle`)) {
    handle.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      e.stopPropagation();
      handle.addEventListener("click", (ev) => ev.stopPropagation(), { once: true });
      const row = handle.closest(selector);
      const rows = [...container.querySelectorAll(selector)];
      const startY = e.clientY;
      const mids = rows.map((r) => { const b = r.getBoundingClientRect(); return b.top + b.height / 2; });
      const from = rows.indexOf(row);
      let target = from;
      row.classList.add("dragging");
      handle.setPointerCapture(e.pointerId);
      const move = (ev) => {
        const dy = ev.clientY - startY;
        row.style.transform = `translateY(${dy}px)`;
        const y = mids[from] + dy;
        target = from;
        for (let i = 0; i < rows.length; i++) {
          if (i < from && y < mids[i]) { target = i; break; }
          if (i > from && y > mids[i]) target = i;
        }
        rows.forEach((r, i) => {
          if (r === row) return;
          const shift = (from < target && i > from && i <= target) ? -row.offsetHeight
            : (from > target && i >= target && i < from) ? row.offsetHeight : 0;
          r.style.transform = shift ? `translateY(${shift}px)` : "";
          r.style.transition = "transform .12s";
        });
      };
      const up = () => {
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", up);
        handle.removeEventListener("pointercancel", up);
        rows.forEach((r) => { r.style.transform = ""; r.style.transition = ""; });
        row.classList.remove("dragging");
        const a = Number(row.dataset.pos), b = Number(rows[target].dataset.pos);
        if (target !== from && canDrop(b)) onMove(a, b);
      };
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", up);
      handle.addEventListener("pointercancel", up);
    });
  }
}

// ---------------------------------------------------------------- this device as a receiver

// "Use this device as a receiver": the browser connects to the server
// (webplayer.py) and plays what it's told to, like a Chromecast, so a tablet
// or old phone plugged into a stereo becomes a speaker the other devices can
// send music to. Its own Now Playing view becomes its display.
//
// Browsers only play sound after a tap, so starting (and resuming after a
// reload) takes one. Two audio elements take turns, so the next track is
// ready to go on to without a gap.
const RX = {
  name: "",
  id: "",          // the player id the server gave it, once connected
  on: false,
  ws: null,
  els: [new Audio(), new Audio()],
  cur: 0,
  track: null,     // what the active element plays
  pre: null,       // {track} loaded in the other element, to go on to
  retry: 0,
  timer: null,
  wake: null,
  volumeWorks: (() => { const a = new Audio(); a.volume = 0.5; return a.volume === 0.5; })(),

  get el() { return this.els[this.cur]; },
  get other() { return this.els[1 - this.cur]; },
  // Whether this device is a receiver right now ("rio.receiver"), and the
  // name it had last time, which stays when it stops being one
  // ("rio.receiverName"), so it's only set up once.
  saved() { try { return JSON.parse(store("rio.receiver") || "null"); } catch { return null; } },
  rememberedName() { return store("rio.receiverName") || (this.saved() || {}).name || ""; },
  remember(name) {
    store("rio.receiverName", name);
    if (this.on) store("rio.receiver", JSON.stringify({ name }));
  },
  defaultName() {
    const ua = navigator.userAgent;
    if (/Silk|\bKF[A-Z]{2,}\b/.test(ua)) return "Fire Tablet";
    if (/iPhone/.test(ua)) return "iPhone";
    if (/iPad/.test(ua)) return "iPad";
    if (/Android/.test(ua)) return "Android";
    return "This computer";
  },

  // Called from a tap: unlock sound, connect, and show the display.
  start(name) {
    this.name = name;
    store("rio.receiver", JSON.stringify({ name }));
    store("rio.receiverName", name);
    // Playing each audio element once during the tap lets the page play it
    // later without one. The one that has a song carries on with it.
    for (const el of this.els) {
      if (el === this.el && this.track) { this.play(el); continue; }
      if (!el.getAttribute("src") || !this.pre) el.src = silentWav();
      const p = el.play();
      if (p) p.then(() => { el.pause(); el.currentTime = 0; }).catch(() => {});
    }
    this.on = true;
    document.body.classList.add("is-receiver");
    if ($("#rx-resume")) $("#rx-resume").remove();
    if (!this.ws || this.ws.readyState > 1) this.connect();
    else this.send({ type: "hello", name: this.name, volume: this.volumeWorks });
    this.keepAwake();
    renderPill();
    NowPlaying.open();
  },
  stop() {
    this.on = false;
    store("rio.receiver", null);
    document.body.classList.remove("is-receiver");
    clearTimeout(this.timer);
    if (this.ws) { this.ws.onclose = null; this.ws.close(); }
    this.ws = null;
    for (const el of this.els) { el.pause(); el.removeAttribute("src"); el.load(); }
    this.track = this.pre = null;
    if (this.wake) { this.wake.release().catch(() => {}); this.wake = null; }
    if (Player.target === this.id) setTarget("");
    this.id = "";
    NowPlaying.close(); // the display opened when it started
    renderPill();
    pollReceivers();
    toast("This device is no longer a receiver");
  },
  connect() {
    const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/player/ws?id=${encodeURIComponent(clientId)}`;
    const ws = new WebSocket(url);
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.send({ type: "hello", name: this.name, volume: this.volumeWorks });
      this.report();
    };
    ws.onmessage = (e) => { try { this.handle(JSON.parse(e.data)); } catch (err) { console.error(err); } };
    ws.onclose = () => {
      if (!this.on) return;
      renderPill();
      // Try again, more slowly each time (the server restarting, Wi-Fi dropping).
      this.timer = setTimeout(() => { if (this.on) this.connect(); }, Math.min(10000, 1000 * 2 ** this.retry++));
    };
  },
  send(msg) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
  },
  state() {
    const el = this.el;
    if (!this.track || el.ended) return "stop";
    if (el.paused) return "pause";
    return el.readyState < 3 ? "buffer" : "play";
  },
  report() {
    this.send({ type: "status", st: this.state(), pos: Math.round((this.el.currentTime || 0) * 1000),
      track_id: this.track ? this.track.id : null, vol: Math.round(this.el.volume * 100) });
  },
  play(el) {
    const p = el.play();
    if (p) {
      p.catch((err) => {
        // Sound isn't unlocked any more (the browser reloaded the page): ask for a tap.
        if (err && err.name === "NotAllowedError") showResume(true);
        this.report();
      });
    }
  },
  handle(m) {
    const el = this.el;
    if (m.type === "welcome") {
      this.id = m.id;
      setTarget(m.id);
      renderPill();
      pollReceivers();
    } else if (m.type === "load") {
      this.pre = null;
      this.other.pause();
      this.track = m.track;
      el.src = m.url;
      if (m.start) el.addEventListener("loadedmetadata", () => { el.currentTime = m.start; }, { once: true });
      this.play(el);
      this.setMeta();
    } else if (m.type === "preload") {
      const o = this.other;
      o.src = m.url;
      o.preload = "auto";
      o.load();
      this.pre = { track: m.track };
    } else if (m.type === "pause") {
      el.pause();
    } else if (m.type === "resume") {
      this.play(el);
    } else if (m.type === "stop") {
      el.pause();
      this.track = null;
      this.pre = null;
    } else if (m.type === "seek") {
      el.currentTime = m.to;
    } else if (m.type === "volume") {
      for (const a of this.els) a.volume = Math.max(0, Math.min(1, m.level));
    }
    this.report();
  },
  ended(el) {
    if (el !== this.el) return;
    if (this.pre) {
      // Straight on to the track that's ready in the other element.
      this.cur = 1 - this.cur;
      this.track = this.pre.track;
      this.pre = null;
      this.play(this.el);
      this.setMeta();
      this.report();
    } else if (this.track) {
      this.send({ type: "ended", track_id: this.track.id });
      this.report();
    }
  },
  failed(el) {
    if (el === this.el && this.track && el.getAttribute("src") && !el.src.startsWith("blob:")) {
      this.send({ type: "ended", track_id: this.track.id, failed: true });
    } else if (el === this.other) {
      this.pre = null; // the server starts it the usual way when the time comes
    }
  },
  setMeta() {
    if (!("mediaSession" in navigator) || !this.track) return;
    const t = this.track;
    navigator.mediaSession.metadata = new MediaMetadata({
      title: t.title, artist: t.artist, album: t.album, artwork: [{ src: albumArtUrl(t.album_id), sizes: "512x512" }],
    });
  },
  // Keep the screen on while it's a receiver (iOS 16.4+, Android, Fire).
  async keepAwake() {
    if (!this.on || !("wakeLock" in navigator) || document.hidden) return;
    try {
      this.wake = await navigator.wakeLock.request("screen");
    } catch { /* not allowed now; tried again when the page is shown */ }
  },
};
for (const el of RX.els) {
  el.preload = "auto";
  el.addEventListener("ended", () => RX.ended(el));
  el.addEventListener("error", () => RX.failed(el));
  for (const ev of ["play", "pause", "playing", "waiting", "seeked"]) el.addEventListener(ev, () => { if (el === RX.el) RX.report(); });
}
setInterval(() => { if (RX.on && RX.track && !RX.el.paused) RX.report(); }, 2000);
document.addEventListener("visibilitychange", () => {
  if (document.hidden || !RX.on) return;
  RX.keepAwake();
  if (!RX.ws || RX.ws.readyState > 1) { clearTimeout(RX.timer); RX.connect(); }
});

// A short silent sound, played on the tap that unlocks audio.
function silentWav() {
  const n = 400, buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
  const w = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  w(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); w(8, "WAVE"); w(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, 8000, true); v.setUint32(28, 16000, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  w(36, "data"); v.setUint32(40, n * 2, true);
  return URL.createObjectURL(new Blob([buf], { type: "audio/wav" }));
}

// The first time, a sheet asks for a name; after that the device starts
// straight away under the name it had (rename it from its menu).
async function becomeReceiver() {
  const known = RX.rememberedName();
  if (known) {
    RX.start(known); // inside the tap on the card, so sound unlocks
    return;
  }
  const input = h("input", { type: "text", value: RX.defaultName(), maxlength: 40, required: true });
  const body = h("div", { style: "display:flex;flex-direction:column;gap:12px" },
    h("p", { class: "subtle", style: "margin:0" },
      "Other devices can then send music here, for example to a tablet or old phone plugged into a stereo. Keep this page open; the screen stays on while it plays."),
    h("label", { class: "field" }, "Name", input),
    RX.volumeWorks ? null : h("p", { class: "subtle", style: "margin:0;font-size:13px" },
      "On iPhone and iPad the volume is set with the device's buttons (or the stereo), not from other devices."));
  const name = await dialog("Use this device as a receiver", body, { ok: "Start", collect: () => input.value.trim() || undefined });
  if (name) RX.start(name); // still inside the tap on Start, so sound unlocks
}

// After a reload: one tap brings it back (browsers need it for sound).
function showResume(force) {
  const saved = RX.saved();
  if (!saved || $("#rx-resume")) return;
  if (RX.on && !force) return;
  const el = h("div", { class: "rx-resume", id: "rx-resume", role: "button", tabindex: "0",
    onclick: (e) => { if (!e.target.closest(".rx-quit")) { el.remove(); RX.start(saved.name); } } },
  h("div", { class: "brand-mark big" }),
  h("div", { class: "rx-resume-name" }, saved.name),
  h("div", { class: "rx-resume-hint" }, RX.on ? "Tap to carry on playing" : "Tap to start the receiver"),
  h("button", { class: "btn rx-quit", onclick: () => { el.remove(); if (RX.on) RX.stop(); else store("rio.receiver", null); renderPill(); } },
    "Stop using this device as a receiver"));
  document.body.append(el);
}

// The pill in the top bar: always shows that this device is a receiver.
function renderPill() {
  const pill = $("#rx-pill");
  pill.hidden = !RX.on;
  if (!RX.on) return;
  const connected = RX.ws && RX.ws.readyState === WebSocket.OPEN;
  pill.classList.toggle("off", !connected);
  pill.replaceChildren(h("span", { class: "dot on" }), h("span", { class: "rx-pill-name" }, RX.name));
  pill.title = connected ? `This device is the receiver “${RX.name}”` : "Reconnecting to the server…";
}
function rxMenu(anchor) {
  showMenu(anchor, [
  { label: "Show the display", icon: "album", run: () => { setTarget(RX.id); NowPlaying.open(); } },
  { label: "Rename…", icon: "edit", run: async () => {
    const n = await askText("Rename this receiver", "Name", RX.name);
    if (n) { RX.name = n; RX.remember(n); RX.send({ type: "hello", name: n, volume: RX.volumeWorks }); renderPill(); setTimeout(pollReceivers, 300); }
  } },
  "-",
  { label: "Stop being a receiver", icon: "x", danger: true, run: () => RX.stop() },
  ], `Receiver: ${RX.name}`);
}
$("#rx-pill").addEventListener("click", (e) => rxMenu(e.currentTarget));

// ---------------------------------------------------------------- receivers

// Rio receivers need the RioServer NG firmware to take music from here;
// Chromecasts and other speakers always can.
const canTarget = (r) => r.kind !== "rio" || r.firmware === "ng";

function setTarget(id) {
  Player.target = id;
  store("rio.target", id);
  $("#target").value = id;
  showTarget();
  if (id) audio.pause();
  else for (const el of document.querySelectorAll(".vol-input")) el.value = Math.round(audio.volume * 100);
  Queue.changed();
  renderNowPlaying();
  markPlaying();
}

async function pollReceivers() {
  try {
    Player.receivers = await api("/api/receivers");
  } catch {
    return;
  }
  Player._statusAt = Date.now();
  const sel = $("#target");
  // A device that is a receiver plays through its receiver; "This browser"
  // would be the same thing twice.
  const want = (RX.on ? [] : [["", "This browser"]]).concat(
    Player.receivers.filter(canTarget).map((r) => [r.id,
      r.id === RX.id ? `This device (${r.name})` : `${r.name}${r.online ? "" : " (offline)"}`]),
  );
  const have = [...sel.options].map((o) => [o.value, o.textContent]);
  if (JSON.stringify(want) !== JSON.stringify(have)) {
    sel.replaceChildren(...want.map(([v, label]) => h("option", { value: v }, label)));
  }
  // Receivers used to be chosen by IP address; a saved choice moves to the
  // receiver's id (its MAC), which survives a new address from the router.
  const moved = Player.target && Player.receivers.find((r) => r.id !== Player.target && r.ip === Player.target);
  if (moved) {
    Player.target = moved.id;
    store("rio.target", moved.id);
  }
  if (Player.target && !Player.receivers.some((r) => r.id === Player.target)) Player.target = RX.on ? RX.id : "";
  if (RX.on && RX.id && !Player.target) Player.target = RX.id;
  sel.value = Player.target;
  renderPill();
  showTarget();
  const online = Player.receivers.filter((r) => r.online).length;
  const pill = $("#receiver-count");
  pill.hidden = !online;
  pill.textContent = online;
  if (Player.target) { renderNowPlaying(); markPlaying(); }
  if (currentRoute === "receivers") renderReceiverCards();
}
// The narrow layout shows "Play on" as an icon: highlighted while music goes
// to a receiver, with the receiver's name as its tooltip.
function showTarget() {
  const r = Player.receiver;
  const name = Player.target ? (r ? r.name : "a receiver") : "this browser";
  const wrap = $("#target-wrap");
  wrap.classList.toggle("remote", !!Player.target);
  wrap.title = `Play on ${name}`;
}

$("#target").addEventListener("change", (e) => chooseTarget(e.target.value));
// Send music somewhere else (from "Play on" or the player's pill), and
// offer to take what's playing along.
function chooseTarget(id) {
  const was = Player.target;
  if (id === was) return;
  const playingHere = !was && Player.current && !audio.paused;
  setTarget(id);
  const name = id ? (Player.receiver ? Player.receiver.name : id) : "this browser";
  toast(`Music goes to ${name}`);
  // Offer to take what's playing along.
  if (playingHere || (was && rxPlaying(((Player.receivers.find((r) => r.id === was) || {}).state)))) {
    showMenu({ x: innerWidth / 2 - 110, y: 80 }, [
      { label: `Move what's playing to ${name}`, icon: "move", run: () => movePlayback(was, id) },
      { label: "Not now", run: () => {} },
    ]);
  }
}
// Poll briskly while a receiver is being controlled, slowly otherwise.
function schedulePoll() {
  const delay = document.hidden ? 10000 : (Player.target || currentRoute === "receivers") ? 1000 : 4000;
  setTimeout(() => pollReceivers().finally(schedulePoll), delay);
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) pollReceivers(); });

// ---------------------------------------------------------------- library status

let libVersion = null;
async function pollStatus() {
  try {
    const s = await api("/api/status");
    const st = s.stats;
    const foot = $("#library-status");
    foot.replaceChildren(
      h("div", {}, `${plural(st.tracks, "song")} · ${plural(st.albums, "album")}`),
      s.scanning ? h("div", { class: "scanning" }, "Scanning library…") : h("div", {}, `${fmtLong(st.duration_ms)} of music`),
    );
    if (libVersion !== null && s.version !== libVersion) {
      playlistCache = null;
      viewCache.clear(); // pages drawn before the change are out of date; positions stay
      if (["artists", "albums", "songs", "genres", "recent", "playlists", "podcasts"].includes(currentRoute)) {
        route({ keepScroll: true });
      }
    }
    libVersion = s.version;
  } catch { /* server restarting */ }
}
setInterval(pollStatus, 5000);

// ---------------------------------------------------------------- views

// opts: album (column), artist (under the title), numbers (track numbers,
// else positions), counts ({id: plays}), letters ({offset: letter}),
// playlist (editable: {id, remove(i), move(a, b)}), extra (td per track)
function trackTable(tracks, opts = {}) {
  const { album = false, artist = false, numbers = true, letters = null, playlist = null, extra = null } = opts;
  const multiDisc = numbers && new Set(tracks.map((t) => t.disc_no || 1)).size > 1;
  const rows = [];
  let disc = null;
  const cols = 4 + (album ? 1 : 0) + (extra ? 1 : 0);
  tracks.forEach((t, i) => {
    if (letters && letters[i]) {
      rows.push(h("tr", { class: "letter-row", id: `song-letter-${letters[i]}` }, h("td", { colspan: cols }, letters[i])));
    }
    if (multiDisc && (t.disc_no || 1) !== disc) {
      disc = t.disc_no || 1;
      rows.push(h("tr", { class: "disc-row" }, h("td", { colspan: cols }, `Disc ${disc}`)));
    }
    const showArtist = artist || (t.artist !== t.album_artist);
    const ctx = { list: tracks, index: i, playlist };
    const tr = h("tr", { "data-track": t.id, "data-pos": i, onclick: () => Player.playTracks(tracks, i) },
      h("td", { class: "n" },
        playlist ? h("span", { class: "handle", "aria-label": "Drag to reorder", html: ICON.handle })
          : [h("span", { class: "num" }, numbers ? (t.track_no || "") : i + 1), h("span", { class: "go", html: ICON.play })],
        h("span", { html: ICON.eq, style: "display:contents" })),
      h("td", { class: "title" }, t.title, showArtist ? h("small", {}, t.artist) : null),
      album ? h("td", { class: "col-album" }, h("a", { href: t.podcast_id ? `#/podcasts/${t.podcast_id}` : `#/albums/${t.album_id}`, onclick: (e) => e.stopPropagation() }, t.album)) : null,
      extra ? extra(t, i) : null,
      h("td", { class: "dur" }, fmtTime(t.duration_ms)),
      h("td", { class: "more" }, moreButton(() => trackMenu(t, ctx), () => t.title)));
    bindContext(tr, () => trackMenu(t, ctx), () => t.title);
    rows.push(tr);
  });
  const body = h("tbody", {}, rows);
  const table = h("table", { class: "tracks" }, body);
  if (playlist) sortable(body, "tr[data-pos]", (a, b) => playlist.move(a, b));
  setTimeout(markPlaying);
  return table;
}

function albumTile(al) {
  const play = h("button", {
    class: "hover-play", "aria-label": `Play ${al.title}`, html: ICON.play,
    onclick: async (e) => {
      e.preventDefault();
      e.stopPropagation();
      const full = await api(`/api/albums/${al.id}`);
      Player.playTracks(full.tracks, 0);
    },
  });
  const tile = h("a", { class: "album-tile", href: `#/albums/${al.id}` },
    cover(al.id, al.title, play),
    h("div", { class: "t" }, al.title),
    h("div", { class: "a" }, [al.artist, al.year].filter(Boolean).join(" · ")),
  );
  bindContext(tile, () => albumMenu(al), () => al.title);
  return tile;
}

function emptyLibrary() {
  return h("div", { class: "empty" },
    h("h2", {}, "Your library is empty"),
    h("p", {}, "Add MP3 files to the folder mounted at ", h("code", {}, "/music"),
      ". New music is picked up automatically within a few seconds."));
}

// The key names are sorted and filed under, as the server makes it
// (library.sort_key): no accents, no case, no leading "The"/"A"/"An" or
// punctuation. "...And Justice for All" is filed under A, "A Day Without
// Rain" under D and "72 Seasons" under #.
function sortKey(name) {
  let s = (name || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
  for (const art of ["the ", "a ", "an "]) {
    if (s.startsWith(art) && s.length > art.length) { s = s.slice(art.length); break; }
  }
  return s.replace(/^["'([{.\-_ ]+/, "") || (name || "").toLowerCase();
}
const ALPHABET = ["#", ..."ABCDEFGHIJKLMNOPQRSTUVWXYZ"];
const letterOf = (key) => { const c = (key || "").charAt(0).toUpperCase(); return c >= "A" && c <= "Z" ? c : "#"; };
const firstLetter = (name) => letterOf(sortKey(name));
// Sort by letter (# first, then A-Z, as the jump bar shows them), then by key.
const byKey = (ka, kb) => ALPHABET.indexOf(letterOf(ka)) - ALPHABET.indexOf(letterOf(kb)) || (ka < kb ? -1 : ka > kb ? 1 : 0);
// A jump bar: # A B C ... Z, with letters that have nothing under them
// greyed out. With `labels` (decades), just those.
function letterBar(present, jump, labels) {
  const have = new Set(present);
  return h("div", { class: "letters" }, (labels || ALPHABET).map((l) => have.has(l)
    ? h("a", { href: "#", role: "button", onclick: (e) => { e.preventDefault(); jump(l); } }, l)
    : h("span", { class: "off", "aria-hidden": "true" }, l)));
}

async function viewArtists() {
  const artists = await api("/api/artists");
  if (!artists.length) return [emptyLibrary()];
  const groups = new Map(ALPHABET.map((l) => [l, []]));
  for (const a of artists.slice().sort((x, y) => byKey(x.sort || sortKey(x.name), y.sort || sortKey(y.name)))) {
    groups.get(letterOf(a.sort || sortKey(a.name))).push(a);
  }
  for (const [l, list] of groups) if (!list.length) groups.delete(l);
  return [
    h("div", { class: "head-row" },
      h("div", {}, h("h1", {}, "Artists"), h("div", { class: "subtle" }, plural(artists.length, "artist")))),
    letterBar([...groups.keys()], (l) => document.getElementById(`letter-${l}`).scrollIntoView({ behavior: "smooth" })),
    [...groups].map(([letter, list]) => h("section", { class: "artist-group", id: `letter-${letter}` },
      h("h3", {}, letter),
      h("div", { class: "artist-grid" }, list.map((a) => h("a", { class: "artist-card", href: `#/artists/${a.id}` },
        artistArt(a),
        h("div", { style: "min-width:0" },
          h("div", { class: "name" }, a.name),
          h("div", { class: "meta" }, `${plural(a.album_count, "album")} · ${plural(a.track_count, "song")}`))))),
    )),
  ];
}

async function viewArtist(id) {
  const artist = await api(`/api/artists/${id}`);
  const albums = await Promise.all(artist.albums.map((al) => api(`/api/albums/${al.id}`)));
  const all = albums.flatMap((a) => a.tracks);
  return [
    h("div", { class: "head-row" },
      h("div", { class: "artist-hero" }, artistArt(artist, "avatar big"), h("div", {},
        h("div", { class: "subtle crumb" }, h("a", { href: "#/artists" }, "Artists")),
        h("h1", {}, artist.name),
        h("div", { class: "subtle" }, `${plural(albums.length, "album")} · ${plural(all.length, "song")} · ${fmtLong(all.reduce((s, t) => s + t.duration_ms, 0))}`))),
      h("div", { class: "btn-row" },
        h("button", { class: "btn primary", onclick: () => Player.playTracks(all, 0) }, icon("play"), "Play all"),
        h("button", { class: "btn", onclick: () => Player.playTracks(all, 0, { shuffle: true }) }, icon("shuffle"), "Shuffle"),
        moreButton(() => [
          { label: "Play next", icon: "playNext", run: () => playNext(all) },
          { label: "Add to Up Next", icon: "addQueue", run: () => addToQueue(all) },
          { label: "Add to playlist…", icon: "addList", sub: playlistPicker(async () => all) },
        ], () => artist.name))),
    albums.map((al) => h("section", { class: "album-section" },
      h("div", { class: "side" },
        h("a", { href: `#/albums/${al.id}` }, cover(al.id, al.title)),
        h("button", { class: "btn", onclick: () => Player.playTracks(al.tracks, 0) }, icon("play"), "Play album")),
      h("div", {},
        h("h2", {}, h("a", { href: `#/albums/${al.id}` }, al.title)),
        h("div", { class: "subtle", style: "margin-bottom:10px" },
          [al.year, al.genre, plural(al.tracks.length, "song"), fmtLong(al.duration_ms)].filter(Boolean).join(" · ")),
        trackTable(al.tracks)),
    )),
  ];
}

const albumKey = (al) => al.sort || sortKey(al.title);
const artistKey = (al) => al.artist_sort || sortKey(al.artist);
const ALBUM_SORTS = {
  name: ["Name", (a, b) => byKey(albumKey(a), albumKey(b)) || byKey(artistKey(a), artistKey(b))],
  artist: ["Artist", (a, b) => byKey(artistKey(a), artistKey(b)) || (a.year || 9999) - (b.year || 9999) || byKey(albumKey(a), albumKey(b))],
  year: ["Year", (a, b) => (b.year || 0) - (a.year || 0) || byKey(albumKey(a), albumKey(b))],
  added: ["Recently added", (a, b) => (b.added || 0) - (a.added || 0)],
};
async function viewAlbums() {
  const albums = await api("/api/albums");
  if (!albums.length) return [emptyLibrary()];
  let sort = store("rio.albumSort");
  if (!ALBUM_SORTS[sort]) sort = "name";
  const grid = h("div", { class: "album-grid" });
  const bar = h("div");
  const draw = () => {
    const list = albums.slice().sort(ALBUM_SORTS[sort][1]);
    grid.replaceChildren(...list.map((al) => {
      const tile = albumTile(al);
      // what the jump bar jumps to: a letter, or a decade when sorted by year
      tile.dataset.letter = sort === "artist" ? letterOf(artistKey(al)) : sort === "name" ? letterOf(albumKey(al))
        : sort === "year" ? (al.year ? `${Math.floor(al.year / 10) * 10}s` : "—") : "";
      return tile;
    }));
    const keys = [...new Set([...grid.children].map((t) => t.dataset.letter).filter(Boolean))];
    bar.replaceChildren(keys.length > 1 && sort !== "added" ? letterBar(keys, (l) => {
      const t = [...grid.children].find((x) => x.dataset.letter === l);
      if (t) t.scrollIntoView({ behavior: "smooth", block: "start" });
    }, sort === "year" ? keys : null) : "");
  };
  const select = h("select", { "aria-label": "Sort albums", onchange: (e) => { sort = e.target.value; store("rio.albumSort", sort); draw(); } },
    Object.entries(ALBUM_SORTS).map(([k, [label]]) => h("option", { value: k, selected: k === sort }, label)));
  draw();
  return [
    h("div", { class: "head-row" },
      h("div", {}, h("h1", {}, "Albums"), h("div", { class: "subtle" }, plural(albums.length, "album"))),
      h("label", { class: "field", style: "flex-direction:row;align-items:center;gap:8px" }, "Sort by", select)),
    bar, grid,
  ];
}

async function viewAlbum(id) {
  const al = await api(`/api/albums/${id}`);
  const hero = h("div", { class: "album-hero" },
    h("div", { class: "backdrop", style: `background-image:url("${albumArtUrl(al.id)}")` }),
    cover(al.id, al.title),
    h("div", {},
      h("div", { class: "kicker" }, "Album"),
      h("h1", {}, al.title),
      h("div", { class: "by" }, h("a", { href: `#/artists/${al.artist_id}` }, al.artist)),
      h("div", { class: "facts" }, [al.year, al.genre, plural(al.tracks.length, "song"), fmtLong(al.duration_ms)].filter(Boolean).join(" · ")),
      h("div", { class: "btn-row" },
        h("button", { class: "btn primary", onclick: () => Player.playTracks(al.tracks, 0) }, icon("play"), "Play"),
        h("button", { class: "btn", onclick: () => Player.playTracks(al.tracks, 0, { shuffle: true }) }, icon("shuffle"), "Shuffle"),
        moreButton(() => albumMenu(al).slice(2), () => al.title))));
  tintFrom(hero, albumArtUrl(al.id));
  return [hero, trackTable(al.tracks)];
}

async function viewSongs() {
  const page = 500;
  const first = await api(`/api/songs?offset=0&limit=${page}`);
  if (!first.total) return [emptyLibrary()];
  let tracks = first.tracks;
  const byOffset = {};
  for (const [l, off] of Object.entries(first.letters || {})) byOffset[off] = l;
  const holder = h("div", {});
  const more = h("button", { class: "btn", style: "margin-top:16px" }, "Load more");
  const draw = () => {
    holder.replaceChildren(trackTable(tracks, { album: true, artist: true, numbers: false, letters: byOffset }));
    more.hidden = tracks.length >= first.total;
  };
  const load = async (until) => {
    const want = Math.max(page, until - tracks.length + page);
    const next = await api(`/api/songs?offset=${tracks.length}&limit=${want}`);
    tracks = tracks.concat(next.tracks);
    draw();
  };
  more.addEventListener("click", () => load(tracks.length).catch(fail));
  draw();
  const jump = async (l) => {
    const off = first.letters[l];
    if (off >= tracks.length) await load(off);
    const row = document.getElementById(`song-letter-${l}`);
    if (row) row.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  return [
    h("div", { class: "head-row" },
      h("div", {}, h("h1", {}, "Songs"), h("div", { class: "subtle" }, plural(first.total, "song"))),
      h("button", { class: "btn", onclick: async () => {
        const all = await api(`/api/songs?offset=0&limit=100000`);
        Player.playTracks(all.tracks, 0, { shuffle: true });
      } }, icon("shuffle"), "Shuffle all")),
    letterBar(Object.keys(first.letters || {}), (l) => jump(l).catch(fail)),
    holder, more,
  ];
}

async function viewGenres() {
  const genres = await api("/api/genres");
  return [
    h("h1", {}, "Genres"),
    h("div", { class: "list", style: "margin-top:16px" }, genres.map((g) =>
      h("a", { href: `#/genres/${g.id}` }, h("span", {}, g.name), h("span", { class: "count" }, plural(g.track_count, "song"))))),
  ];
}

async function viewGenre(id) {
  const g = await api(`/api/genres/${id}`);
  return [
    h("div", { class: "subtle crumb" }, h("a", { href: "#/genres" }, "Genres")),
    h("h1", { style: "margin-bottom:18px" }, g.name),
    h("div", { class: "album-grid" }, g.albums.map(albumTile)),
  ];
}

async function viewPlaylists() {
  const lists = await api("/api/playlists");
  playlistCache = lists.filter((p) => p.editable);
  const smart = [
    ["#/history", "Top 25 Most Played", "From what's been played on every player"],
    ["#/history", "Recently Played", "Newest first"],
  ];
  return [
    h("div", { class: "head-row" },
      h("div", {}, h("h1", {}, "Playlists"), h("div", { class: "subtle" }, "Made here, or read from .m3u files in your music folder")),
      h("button", { class: "btn primary", onclick: async () => {
        const p = await newPlaylist().catch(fail);
        if (p) location.hash = `#/playlists/${p.id}`;
      } }, icon("addList"), "New playlist")),
    h("div", { class: "list", style: "margin-top:8px" },
      smart.map(([href, name, note]) => h("a", { href },
        h("span", { class: "lead" }, h("span", { class: "ico", html: ICON.refresh }), h("span", {}, name)),
        h("span", { class: "count" }, note))),
      lists.map((p) => h("a", { href: `#/playlists/${p.id}` },
        h("span", { class: "lead" }, h("span", { class: "ico", html: p.editable ? ICON.list : ICON.album }), h("span", {}, p.name)),
        h("span", { class: "count" }, plural(p.track_count, "song"))))),
    lists.length ? null : h("p", { class: "subtle" }, "No playlists yet. Make one here, or use “Add to playlist” on any song or album."),
  ];
}

async function viewPlaylist(id) {
  const p = await api(`/api/playlists/${id}`);
  let ids = p.tracks.map((t) => t.id);
  const save = async (newIds) => {
    await post(`/api/playlists/${p.id}`, { track_ids: newIds });
    playlistCache = null;
    route({ keepScroll: true });
  };
  const editor = p.editable ? {
    remove: (i) => save(ids.filter((_, j) => j !== i)).then(() => toast("Removed from the playlist")),
    move: (a, b) => {
      const copy = ids.slice();
      const [x] = copy.splice(a, 1);
      copy.splice(b, 0, x);
      return save(copy).catch(fail);
    },
  } : null;
  return [
    h("div", { class: "head-row" },
      h("div", {}, h("div", { class: "subtle crumb" }, h("a", { href: "#/playlists" }, "Playlists")), h("h1", {}, p.name),
        h("div", { class: "subtle" }, `${plural(p.tracks.length, "song")} · ${fmtLong(p.tracks.reduce((s, t) => s + t.duration_ms, 0))}${p.editable ? "" : " · from an .m3u file"}`)),
      h("div", { class: "btn-row" },
        h("button", { class: "btn primary", onclick: () => Player.playTracks(p.tracks, 0), disabled: !p.tracks.length }, icon("play"), "Play"),
        h("button", { class: "btn", onclick: () => Player.playTracks(p.tracks, 0, { shuffle: true }), disabled: !p.tracks.length }, icon("shuffle"), "Shuffle"),
        moreButton(() => [
          { label: "Play next", icon: "playNext", run: () => playNext(p.tracks) },
          { label: "Add to Up Next", icon: "addQueue", run: () => addToQueue(p.tracks) },
          p.editable ? "-" : null,
          p.editable ? { label: "Rename…", icon: "edit", run: async () => {
            const name = await askText("Rename playlist", "Name", p.name);
            if (name) { await post(`/api/playlists/${p.id}`, { name }); playlistCache = null; route({ keepScroll: true }); }
          } } : null,
          p.editable ? { label: "Delete playlist", icon: "trash", danger: true, run: async () => {
            if (await confirmAction("Delete playlist?", `“${p.name}” will be deleted. The songs stay in your library.`)) {
              await del(`/api/playlists/${p.id}`);
              playlistCache = null;
              location.hash = "#/playlists";
            }
          } } : null,
        ], () => p.name))),
    p.tracks.length ? trackTable(p.tracks, { album: true, artist: true, numbers: false, playlist: editor })
      : h("p", { class: "subtle" }, "This playlist is empty. Use “Add to playlist” on any song or album."),
  ];
}

async function viewRecent() {
  const albums = await api("/api/recent");
  return [
    h("div", { class: "head-row" }, h("div", {}, h("h1", {}, "Recently Added"), h("div", { class: "subtle" }, "Newest albums first"))),
    albums.length ? h("div", { class: "album-grid" }, albums.map(albumTile)) : emptyLibrary(),
  ];
}

async function viewHistory() {
  const [top, recent] = await Promise.all([api("/api/history/top?limit=25"), api("/api/history/recent?limit=100")]);
  if (!top.length) {
    return [h("h1", {}, "History"), h("div", { class: "empty" }, h("h2", {}, "Nothing played yet"),
      h("p", {}, "Songs count as played once half of them (or four minutes) has been heard, on any receiver, speaker or browser."))];
  }
  return [
    h("div", { class: "head-row" }, h("div", {}, h("h1", {}, "History"), h("div", { class: "subtle" }, "What's been played on every receiver, speaker and browser"))),
    h("div", { class: "head-row", style: "margin:24px 0 8px" }, h("h2", { style: "margin:0" }, "Top 25 Most Played"),
      h("div", { class: "btn-row" },
        h("button", { class: "btn small", onclick: () => Player.playTracks(top, 0) }, icon("play"), "Play"),
        h("button", { class: "btn small", onclick: () => newPlaylist(top).catch(fail) }, "Save as playlist"))),
    trackTable(top, { album: true, artist: true, numbers: false, extra: (t) => h("td", { class: "count" }, plural(t.play_count, "play")) }),
    h("div", { class: "head-row", style: "margin:30px 0 8px" }, h("h2", { style: "margin:0" }, "Recently Played"),
      h("button", { class: "btn small", onclick: () => Player.playTracks(recent, 0) }, icon("play"), "Play")),
    trackTable(recent, { album: true, artist: true, numbers: false, extra: (t) => h("td", { class: "count" }, fmtAgo(t.played_at)) }),
  ];
}

async function viewSearch(q) {
  const res = await api(`/api/search?q=${encodeURIComponent(q)}`);
  const none = !res.artists.length && !res.albums.length && !res.tracks.length;
  return [
    h("h1", {}, `Results for “${q}”`),
    none ? h("p", { class: "subtle" }, "Nothing found.") : null,
    res.artists.length ? [h("h2", {}, "Artists"), h("div", { class: "artist-grid" }, res.artists.map((a) =>
      h("a", { class: "artist-card", href: `#/artists/${a.id}` }, artistArt(a),
        h("div", {}, h("div", { class: "name" }, a.name), h("div", { class: "meta" }, plural(a.track_count, "song"))))))] : null,
    res.albums.length ? [h("h2", {}, "Albums"), h("div", { class: "album-grid" }, res.albums.map(albumTile))] : null,
    res.tracks.length ? [h("h2", {}, "Songs"), trackTable(res.tracks, { album: true, artist: true, numbers: false })] : null,
  ];
}

// ---------------------------------------------------------------- podcasts

const podcastArt = (id, title) => cover(null, title, null, `/api/art/podcast/${id}`);

async function playEpisode(e, show) {
  if (!e.track_id) return;
  const t = await api(`/api/tracks/${e.track_id}`);
  Player.playTracks([t], 0);
  if (Player.target && e.position_ms && !e.played) toast(`Carrying on from ${fmtTime(e.position_ms)}`);
  void show;
}

function episodeRow(e, show) {
  const ready = e.status === "ready" && e.track_id;
  const left = e.position_ms && e.duration_ms ? Math.max(0, e.duration_ms - e.position_ms) : 0;
  const desc = h("div", { class: "desc" }, e.description || "");
  const status = e.status === "queued" || e.status === "downloading"
    ? [h("span", { class: "spin", "aria-hidden": "true" }), h("span", { class: "subtle", style: "font-size:13px" }, e.status === "queued" ? "Waiting" : "Downloading")]
    : e.status === "unsupported" ? h("span", { class: "subtle", style: "font-size:13px" }, "Not an MP3")
      : e.status === "failed" ? h("button", { class: "btn small", title: e.error, onclick: () => post(`/api/episodes/${e.id}/download`).then(() => route({ keepScroll: true })).catch(fail) }, icon("refresh"), "Retry")
        : !ready ? h("button", { class: "btn small", onclick: () => post(`/api/episodes/${e.id}/download`).then(() => { toast("Downloading"); route({ keepScroll: true }); }).catch(fail) }, icon("download"), "Download")
          : null;
  const menu = () => [
    ready ? { label: e.position_ms && !e.played ? `Resume (${fmtTime(left)} left)` : "Play", icon: "play", run: () => playEpisode(e, show) } : null,
    ready ? { label: "Play next", icon: "playNext", run: async () => playNext([await api(`/api/tracks/${e.track_id}`)]) } : null,
    ready ? { label: "Add to Up Next", icon: "addQueue", run: async () => addToQueue([await api(`/api/tracks/${e.track_id}`)]) } : null,
    { label: e.played ? "Mark as unplayed" : "Mark as played", icon: "check", run: () => post(`/api/episodes/${e.id}`, { played: !e.played }).then(() => route({ keepScroll: true })) },
    !ready && e.status !== "unsupported" ? { label: "Download", icon: "download", run: () => post(`/api/episodes/${e.id}/download`).then(() => route({ keepScroll: true })) } : null,
    ready ? "-" : null,
    ready ? { label: "Delete download", icon: "trash", danger: true, run: () => del(`/api/episodes/${e.id}/download`).then(() => route({ keepScroll: true })) } : null,
  ];
  const row = h("div", { class: `episode${e.played ? " played" : ""}`, "data-track": e.track_id || "" },
    h("div", {},
      h("div", { class: "t", onclick: () => desc.classList.toggle("open") }, !e.played && ready && !e.position_ms ? h("span", { class: "new-dot", title: "New" }) : null, e.title),
      h("div", { class: "when" }, [fmtDate(e.published), e.duration_ms ? fmtLong(e.duration_ms) : "",
        e.played ? "Played" : left ? `${fmtLong(left)} left` : ""].filter(Boolean).join(" · ")),
      left && !e.played ? h("div", { class: "progress" }, h("i", { style: `width:${Math.round((e.position_ms / e.duration_ms) * 100)}%` })) : null),
    h("div", { class: "acts" },
      ready ? h("button", { class: "btn small primary", onclick: () => playEpisode(e, show) }, icon("play"), e.position_ms && !e.played ? "Resume" : "Play") : null,
      status,
      moreButton(menu, () => e.title)),
    desc);
  bindContext(row, menu, () => e.title);
  return row;
}

async function viewPodcasts() {
  const [shows, recent] = await Promise.all([api("/api/podcasts"), api("/api/podcasts/recent?limit=40")]);
  const url = h("input", { type: "url", placeholder: "Paste a podcast's feed address (RSS)", required: true, "aria-label": "Feed address" });
  const addBtn = h("button", { class: "btn primary", type: "submit" }, "Subscribe");
  const file = h("input", { type: "file", accept: ".opml,.xml,text/xml,application/xml", hidden: true, onchange: async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    toast("Importing…");
    try {
      const res = await api("/api/podcasts/opml", { method: "POST", body: await f.text(), raw: true });
      toast(`Subscribed to ${plural(res.added, "podcast")}${res.failed.length ? `; ${res.failed.length} couldn't be read` : ""}`);
      route();
    } catch (err) { fail(err); }
  } });
  const form = h("form", { class: "add-form", onsubmit: async (e) => {
    e.preventDefault();
    addBtn.disabled = true;
    addBtn.textContent = "Subscribing…";
    try {
      const p = await post("/api/podcasts", { url: url.value.trim() });
      toast(`Subscribed to “${p.title}”`);
      location.hash = `#/podcasts/${p.id}`;
    } catch (err) {
      fail(err);
      addBtn.disabled = false;
      addBtn.textContent = "Subscribe";
    }
  } }, url, addBtn);
  const going = recent.filter((t) => t.position_ms && !t.played);
  const fresh = recent.filter((t) => !t.position_ms && !t.played).slice(0, 8);
  const epList = (list) => h("div", { class: "list" }, list.map((t) => {
    const left = Math.max(0, t.duration_ms - t.position_ms);
    return h("div", { class: "row", "data-track": t.id },
      h("span", { class: "lead" },
        h("span", { style: "width:44px;height:44px;flex:none;border-radius:8px;overflow:hidden;display:block" }, podcastArt(t.podcast_id, t.album)),
        h("span", { style: "display:flex;flex-direction:column;min-width:0" },
          h("b", { style: "overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, t.title),
          h("span", { class: "count" }, `${t.album} · ${t.position_ms ? `${fmtLong(left)} left` : fmtDate(t.published)}`))),
      h("button", { class: "btn small primary", onclick: () => Player.playTracks([t], 0) }, icon("play"), t.position_ms ? "Resume" : "Play"));
  }));
  return [
    h("div", { class: "head-row" },
      h("div", {}, h("h1", {}, "Podcasts"), h("div", { class: "subtle" }, "Episodes are downloaded here, so every receiver can play them, and each one carries on where it was left on any device")),
      h("div", { class: "btn-row" },
        shows.length ? h("button", { class: "btn", onclick: () => post("/api/podcasts/refresh").then(() => toast("Checking for new episodes")).catch(fail) }, icon("refresh"), "Check now") : null,
        moreButton(() => [
          { label: "Import subscriptions (OPML)…", icon: "download", run: () => file.click() },
          shows.length ? { label: "Export subscriptions (OPML)", icon: "move", run: () => { location.href = "/api/podcasts/opml"; } } : null,
        ]))),
    form, file,
    going.length ? [h("h2", {}, "Continue listening"), epList(going)] : null,
    fresh.length ? [h("h2", {}, "New episodes"), epList(fresh)] : null,
    shows.length ? h("h2", {}, "Shows") : h("div", { class: "empty" }, h("h2", {}, "No podcasts yet"),
      h("p", {}, "Paste a feed address above. Most podcast websites link to it as “RSS”.")),
    h("div", { class: "album-grid" }, shows.map((p) => h("a", { class: "album-tile", href: `#/podcasts/${p.id}` },
      cover(null, p.title, p.unplayed ? h("span", { class: "badge" }, `${p.unplayed} new`) : null, `/api/art/podcast/${p.id}`),
      h("div", { class: "t" }, p.title),
      h("div", { class: "a" }, p.error ? `⚠ ${p.error}` : p.author || `${plural(p.downloaded || 0, "episode")} downloaded`)))),
  ];
}

async function viewPodcast(id) {
  const p = await api(`/api/podcasts/${id}`);
  const keep = h("select", { "aria-label": "Episodes to keep", onchange: async (e) => {
    await post(`/api/podcasts/${p.id}`, { keep: Number(e.target.value) }).catch(fail);
    toast("Saved");
    route({ keepScroll: true });
  } }, [0, 1, 2, 3, 5, 10, 20].map((n) => h("option", { value: n, selected: n === p.keep }, n ? `Keep the newest ${n}` : "Download only by hand")));
  const hero = h("div", { class: "album-hero" },
    h("div", { class: "backdrop", style: `background-image:url("/api/art/podcast/${p.id}")` }),
    podcastArt(p.id, p.title),
    h("div", {},
      h("div", { class: "kicker" }, "Podcast"),
      h("h1", {}, p.title),
      p.author ? h("div", { class: "by" }, p.link ? h("a", { href: p.link, target: "_blank", rel: "noopener" }, p.author) : p.author) : null,
      p.description ? h("p", { class: "notes" }, p.description) : null,
      h("div", { class: "facts" }, [plural(p.episodes.length, "episode"), p.checked ? `checked ${fmtAgo(p.checked)}` : "", p.error ? `⚠ ${p.error}` : ""].filter(Boolean).join(" · ")),
      h("div", { class: "btn-row" },
        h("button", { class: "btn", onclick: async (e) => {
          e.currentTarget.disabled = true;
          await post(`/api/podcasts/${p.id}/refresh`).catch(fail);
          route({ keepScroll: true });
        } }, icon("refresh"), "Check for new"),
        keep,
        moreButton(() => [
          { label: "Unsubscribe", icon: "trash", danger: true, run: async () => {
            if (await confirmAction("Unsubscribe?", `Downloaded episodes of “${p.title}” will be deleted.`, "Unsubscribe")) {
              await del(`/api/podcasts/${p.id}`);
              location.hash = "#/podcasts";
            }
          } },
        ], () => p.title))));
  tintFrom(hero, `/api/art/podcast/${p.id}`);
  return [
    hero,
    h("div", {}, p.episodes.map((e) => episodeRow(e, p))),
  ];
}

// ---------------------------------------------------------------- receivers page

const STATE_NAMES = { play: "Playing", pause: "Paused", buffer: "Buffering", stop: "Stopped", standby: "Standby" };
const FW_NAMES = { ng: "RioServer NG firmware", stock: "Original Rio firmware", unknown: "Booting" };

function receiverMeta(rx) {
  if (rx.kind === "rio") return `${rx.ip}${rx.mac ? " · " + rx.mac : ""} · ${FW_NAMES[rx.firmware] || rx.firmware}${rx.state && rx.state.ver ? ` ${rx.state.ver}` : ""}`;
  return `${rx.model} · ${rx.ip}`;
}

function receiverIdle(rx) {
  if (rx.kind === "rio" && rx.firmware === "stock") {
    return "Browsing with the original firmware. Switch to RioServer NG firmware for remote control.";
  }
  if (!rx.online) return `Last seen ${new Date(rx.last_seen * 1000).toLocaleString()}`;
  return rx.state && rx.state.app ? `In use by ${rx.state.app}` : "Idle";
}

// A receiver card is built once and then updated in place, so polling never
// swallows a click or yanks a slider out from under the pointer.
function receiverCard(id) {
  const r = () => Player.receivers.find((x) => x.id === id) || {};
  const cmd = (c, value) => post(`/api/receivers/${id}/command`, value === undefined ? { cmd: c } : { cmd: c, value })
    .then(() => setTimeout(pollReceivers, 350)).catch(fail);
  const sendVolume = volumeSender(() => id);
  const dot = h("span", { class: "dot" });
  const name = h("span", { class: "rx-name" });
  const rename = h("button", {
    class: "icon-btn rename", "aria-label": "Rename", title: "Rename", html: ICON.edit,
    onclick: async () => {
      const n = await dialog("Name this receiver", h("label", { class: "field" }, "Name (leave empty for the default)",
        h("input", { type: "text", value: r().label || "", maxlength: 40, placeholder: "Kitchen" })),
      { ok: "Save", collect: (f) => f.querySelector("input").value });
      if (n === null) return;
      post(`/api/receivers/${id}/name`, { name: n }).then(pollReceivers).catch(fail);
    },
  });
  const meta = h("div", { class: "meta" });
  const now = h("div", { class: "now" });
  const playBtn = h("button", { class: "icon-btn", "aria-label": "Play/pause", onclick: () => cmd("toggle") });
  const vol = trackBusy(h("input", { type: "range", min: 0, max: 100, value: 50, "aria-label": "Volume",
    oninput: (e) => { sendVolume(Number(e.target.value)); volLabel.textContent = e.target.value; } }));
  const volLabel = h("span", { class: "vol-label" });
  const moveBtn = h("button", { class: "icon-btn", "aria-label": "Move to another speaker", title: "Move to another speaker", html: ICON.move,
    style: "margin-left:auto", onclick: (e) => showMenu(e.currentTarget, moveMenu(id), "Move playback to") });
  const transport = h("div", { class: "ctrl" },
    h("button", { class: "icon-btn", "aria-label": "Previous", html: ICON.prev, onclick: () => cmd("prev") }),
    playBtn,
    h("button", { class: "icon-btn", "aria-label": "Next", html: ICON.next, onclick: () => cmd("next") }),
    h("button", { class: "icon-btn", "aria-label": "Stop", html: ICON.stop, onclick: () => cmd("stop") }),
    moveBtn);
  const volRow = h("div", { class: "vol-row" },
    h("span", { html: ICON.speaker, style: "display:inline-flex;color:var(--muted)" }), vol, volLabel);
  const sleepInfo = h("span", { class: "meta" });
  const sleep = h("select", { class: "sleep", "aria-label": "Sleep timer",
    onchange: (e) => post(`/api/receivers/${id}/sleep`, { minutes: Number(e.target.value) })
      .then(() => { toast(Number(e.target.value) ? `Sleeping in ${e.target.value} min` : "Sleep timer off"); pollReceivers(); })
      .catch(fail) },
    [["0", "Sleep timer"], ["15", "15 min"], ["30", "30 min"], ["45", "45 min"], ["60", "1 hour"], ["90", "90 min"]]
      .map(([v, label]) => h("option", { value: v }, label)));
  const sleepRow = h("div", { class: "sleep-row" }, h("span", { html: ICON.moon, style: "display:inline-flex;color:var(--muted)" }), sleep, sleepInfo);
  const ctrl = h("div", { class: "controls" }, transport, volRow, sleepRow);
  const send = h("button", {
    class: "btn send",
    onclick: () => { setTarget(id); toast(`Music goes to ${r().name}`); },
  }, "Send music here");
  const mine = h("span", { class: "tag" }, "This device");
  const forget = h("button", { class: "btn small", onclick: () => del(`/api/receivers/${id}`)
    .then(() => { receiverCards.delete(id); pollReceivers(); }).catch(fail) }, "Forget");
  const el = h("div", { class: "card" }, h("h3", {}, dot, name, mine, rename), meta, now, ctrl, send, forget);
  let playIcon = null, artKey = null;
  const thumb = h("div", { class: "thumb", title: "Open Now Playing", onclick: () => { setTarget(id); NowPlaying.open(); } });
  const info = h("div", { class: "info" });

  function update(rx) {
    const st = rx.state || {};
    const t = st.track;
    dot.className = `dot ${rx.online ? "on" : ""}`;
    name.textContent = rx.name;
    meta.textContent = receiverMeta(rx);
    const key = t ? String(t.album_id) : "";
    if (artKey !== key) {
      artKey = key;
      thumb.replaceChildren(t ? cover(t.album_id, t.album) : "");
      tintFrom(now, t ? albumArtUrl(t.album_id) : null);
    }
    info.replaceChildren(...(t
      ? [h("div", { class: "t" }, t.title), h("div", { class: "meta" }, `${t.artist} — ${t.album}`),
        h("div", { class: "meta" }, `${STATE_NAMES[st.st] || st.st} · ${fmtTime(st.pos)} / ${fmtTime(t.duration_ms)}${st.qn ? ` · track ${Number(st.qi) + 1} of ${st.qn}` : ""}`)]
      : [h("div", { class: "meta" }, receiverIdle(rx))]));
    if (!now.children.length) now.append(thumb, info);
    thumb.hidden = !t;
    ctrl.hidden = !rx.controllable;
    volRow.hidden = rx.volume === false; // Safari on iPhone/iPad: only the device's buttons set it
    mine.hidden = rx.id !== RX.id;
    forget.hidden = !(rx.kind === "browser" && !rx.online);
    moveBtn.hidden = !t;
    const left = rx.sleep_at ? Math.max(1, Math.round((rx.sleep_at * 1000 - Date.now()) / 60000)) : 0;
    sleepInfo.textContent = left ? `Stops in ${plural(left, "minute")}` : "";
    if (!left && document.activeElement !== sleep) sleep.value = "0";
    send.hidden = !(canTarget(rx) && rx.online) || Player.target === id;
    const want = rxPlaying(st) ? "pause" : "play";
    if (playIcon !== want) {
      playBtn.innerHTML = ICON[want];
      playBtn.setAttribute("aria-label", want === "pause" ? "Pause" : "Play");
      playIcon = want;
    }
    if (st.vol && !isBusy(vol)) { vol.value = st.vol; volLabel.textContent = st.vol; }
  }
  return { el, update };
}

let receiverHolder = null;
const receiverCards = new Map();
// The last card: this browser can be a receiver too.
const addSelfNote = h("span", { class: "meta" });
const addSelfCard = h("button", { class: "card add-self", onclick: () => becomeReceiver().catch(fail) },
  h("span", { class: "add-self-icon", html: ICON.cast }),
  h("b", {}, "Use this device as a receiver"),
  addSelfNote);
function updateAddSelfCard() {
  const known = RX.rememberedName();
  addSelfNote.textContent = known
    ? `Starts as “${known}”. Rename it from its menu once it's running.`
    : "Play music from other devices through this one, for example a tablet plugged into a stereo.";
}
function renderReceiverCards() {
  if (!receiverHolder) return;
  updateAddSelfCard();
  if (!Player.receivers.length) {
    receiverCards.clear();
    receiverHolder.replaceChildren(h("div", { class: "empty", style: "grid-column:1/-1" },
      h("h2", {}, "No receivers yet"),
      h("p", {}, "Power on a Rio Receiver or Dell Digital Audio Receiver on the same network. It finds this server automatically, boots from it and shows up here."),
      h("p", {}, "Chromecasts, Google Home and Nest speakers and speaker groups on the network show up here by themselves.")),
    ...(RX.on ? [] : [addSelfCard]));
    return;
  }
  const els = Player.receivers.map((rx) => {
    if (!receiverCards.has(rx.id)) receiverCards.set(rx.id, receiverCard(rx.id));
    const card = receiverCards.get(rx.id);
    card.update(rx);
    return card.el;
  });
  if (!RX.on) els.push(addSelfCard);
  const kids = [...receiverHolder.children];
  if (kids.length !== els.length || els.some((el, i) => kids[i] !== el)) receiverHolder.replaceChildren(...els);
}

async function viewReceivers() {
  receiverHolder = h("div", { class: "cards" });
  receiverCards.clear();
  await pollReceivers();
  renderReceiverCards();
  return [
    h("div", { class: "head-row" }, h("div", {}, h("h1", {}, "Receivers"),
      h("div", { class: "subtle" }, "Rio and Dell receivers that have contacted this server, the Chromecasts and Google speakers on your network, and phones or tablets used as receivers"))),
    receiverHolder,
  ];
}

// ---------------------------------------------------------------- alarms

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const SOURCE_NAMES = {
  shuffle: "Shuffle all songs", top25: "Top 25 Most Played", played: "Recently Played",
  playlist: "A playlist", album: "An album", artist: "An artist", podcast: "A podcast's newest episode",
};
function describeDays(days) {
  if (!days || !days.length) return "Once";
  if (days.length === 7) return "Every day";
  const key = days.join(",");
  if (key === "0,1,2,3,4") return "Weekdays";
  if (key === "5,6") return "Weekends";
  return days.map((d) => DAY_NAMES[d]).join(", ");
}

async function alarmDialog(alarm) {
  const a = alarm || { time: "07:00", days: [0, 1, 2, 3, 4], volume: 35, fade: 5, shuffle: false, enabled: true,
    source: { type: "shuffle", id: 0 }, receiver: (Player.receivers.find((r) => canTarget(r)) || {}).id || "" };
  const time = h("input", { type: "time", value: a.time, required: true });
  const days = new Set(a.days);
  const chips = h("div", { class: "chips" }, DAY_NAMES.map((d, i) => h("button", { type: "button", class: `chip${days.has(i) ? " sel" : ""}`,
    onclick: (e) => { days.has(i) ? days.delete(i) : days.add(i); e.currentTarget.classList.toggle("sel", days.has(i)); } }, d)));
  const rx = h("select", { required: true }, Player.receivers.filter(canTarget).map((r) => h("option", { value: r.id, selected: r.id === a.receiver }, r.name)));
  const kind = h("select", {}, Object.entries(SOURCE_NAMES).map(([k, label]) => h("option", { value: k, selected: k === a.source.type }, label)));
  const item = h("select", { "aria-label": "Which one" });
  const itemField = h("label", { class: "field" }, "Which one", item);
  const loadItems = async () => {
    const k = kind.value;
    itemField.hidden = !["playlist", "album", "artist", "podcast"].includes(k);
    if (itemField.hidden) return;
    const url = { playlist: "/api/playlists", album: "/api/albums", artist: "/api/artists", podcast: "/api/podcasts" }[k];
    const list = await api(url);
    item.replaceChildren(...list.map((x) => h("option", { value: x.id, selected: x.id === a.source.id },
      k === "album" ? `${x.title} — ${x.artist}` : x.name || x.title)));
  };
  kind.addEventListener("change", loadItems);
  await loadItems();
  const vol = h("input", { type: "range", min: 5, max: 100, value: a.volume });
  const volOut = h("span", {}, `${a.volume}`);
  vol.addEventListener("input", () => { volOut.textContent = vol.value; });
  const fade = h("select", {}, [0, 1, 2, 5, 10, 15, 20, 30].map((m) => h("option", { value: m, selected: m === a.fade }, m ? `${m} min` : "None")));
  const shuffle = h("input", { type: "checkbox", checked: a.shuffle });
  const label = h("input", { type: "text", value: a.label || "", maxlength: 60, placeholder: "Wake up" });
  if (!rx.options.length) {
    toast("No receivers yet: power one on first");
    return;
  }
  const body = h("div", { style: "display:flex;flex-direction:column;gap:14px" },
    h("div", { class: "field-row" }, h("label", { class: "field" }, "Time", time), h("label", { class: "field" }, "Receiver", rx)),
    h("div", { class: "field" }, "Repeat on (none: just once)", chips),
    h("div", { class: "field-row" }, h("label", { class: "field" }, "Play", kind), itemField),
    h("div", { class: "field-row" },
      h("label", { class: "field" }, h("span", {}, "Volume ", volOut), vol),
      h("label", { class: "field" }, "Fade in over", fade)),
    h("label", { class: "field check" }, shuffle, "Shuffle"),
    h("label", { class: "field" }, "Label", label));
  const value = await dialog(alarm ? "Edit alarm" : "New alarm", body, { ok: "Save", collect: () => ({
    time: time.value, days: [...days].sort(), receiver: rx.value, volume: Number(vol.value), fade: Number(fade.value),
    shuffle: shuffle.checked, label: label.value, enabled: true,
    tz: Intl.DateTimeFormat().resolvedOptions().timeZone || "",
    source: { type: kind.value, id: itemField.hidden ? 0 : Number(item.value), name: itemField.hidden ? "" : (item.selectedOptions[0] || {}).textContent || "" },
  }) });
  if (!value) return;
  await (alarm ? post(`/api/alarms/${alarm.id}`, value) : post("/api/alarms", value));
  toast("Alarm saved");
  route({ keepScroll: true });
}

async function viewAlarms() {
  const [alarms] = await Promise.all([api("/api/alarms"), pollReceivers()]);
  const rxName = (id) => (Player.receivers.find((r) => r.id === id) || {}).name || "a receiver that's gone";
  return [
    h("div", { class: "head-row" },
      h("div", {}, h("h1", {}, "Alarms"), h("div", { class: "subtle" }, "Wake up to music: a receiver starts playing and fades in")),
      h("button", { class: "btn primary", onclick: () => alarmDialog().catch(fail) }, icon("bell"), "New alarm")),
    alarms.length ? alarms.map((a) => {
      const sw = h("input", { type: "checkbox", checked: a.enabled, "aria-label": "On", onchange: (e) =>
        post(`/api/alarms/${a.id}`, { enabled: e.target.checked }).then(() => route({ keepScroll: true })).catch(fail) });
      return h("div", { class: `alarm${a.enabled ? "" : " off"}` },
        h("div", { class: "clock" }, a.time),
        h("div", { class: "info" },
          h("b", {}, a.label || describeDays(a.days)),
          `${a.label ? describeDays(a.days) + " · " : ""}${rxName(a.receiver)} · ${a.source.name || SOURCE_NAMES[a.source.type]}${a.fade ? ` · fades in over ${a.fade} min` : ""}`,
          a.next ? h("div", {}, `Next: ${new Date(a.next * 1000).toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" })}`) : null),
        h("div", { class: "acts" },
          h("label", { class: "switch" }, sw, h("span")),
          moreButton(() => [
            { label: "Edit…", icon: "edit", run: () => alarmDialog(a) },
            { label: "Try it now", icon: "play", run: () => post(`/api/alarms/${a.id}/test`).then(() => toast("Starting the alarm")) },
            "-",
            { label: "Delete", icon: "trash", danger: true, run: () => del(`/api/alarms/${a.id}`).then(() => route({ keepScroll: true })) },
          ], () => `${a.time} ${a.label || ""}`)));
    }) : h("div", { class: "empty" }, h("h2", {}, "No alarms"), h("p", {}, "Make one to have a receiver wake you with music, like a clock radio.")),
  ];
}

// ---------------------------------------------------------------- settings

async function viewSettings() {
  const [s, fw] = await Promise.all([api("/api/status"), api("/api/firmware")]);
  const st = s.stats;
  const last = s.last_scan;
  const fwInfo = {
    "ng": ["RioServer NG", "iPod-style menus (Artist › Album › Songs), remote control, Up Next and podcasts from this page"],
    "rioplay": ["RioPlay 0.29", "David Flowerday's open-source player (2002)"],
  };
  const fwList = h("div", {}, fw.available.length ? fw.available.map((f) => {
    const [title, desc] = fwInfo[f.name] || [f.file, "Custom image (e.g. the original Rio/Dell receiver.arf)"];
    return h("label", { class: `fw-option ${fw.selected === f.name || fw.selected === f.file ? "sel" : ""}` },
      h("input", { type: "radio", name: "fw", checked: fw.selected === f.name || fw.selected === f.file,
        onchange: async () => { await post("/api/firmware", { name: f.name }); toast("Firmware selected; power-cycle your receivers"); route(); } }),
      h("div", { style: "flex:1;min-width:0" }, h("b", {}, title), h("small", {}, `${desc} · ${(f.size / 1024).toFixed(0)} KB`)),
      h("a", { class: "btn small", href: `/api/firmware/${encodeURIComponent(f.name)}/download`, onclick: (e) => e.stopPropagation() }, "Download"));
  }) : h("p", { class: "subtle" }, "No firmware images found."));
  const theme = store("rio.theme") || "system";
  const seg = h("div", { class: "segmented", role: "radiogroup", "aria-label": "Theme" },
    [["system", "System", "monitor"], ["light", "Light", "sun"], ["dark", "Dark", "moon"]].map(([k, label, ic]) =>
      h("button", { type: "button", role: "radio", "aria-checked": String(k === theme), class: k === theme ? "sel" : "",
        onclick: (e) => {
          applyTheme(k);
          for (const b of seg.children) { b.classList.toggle("sel", b === e.currentTarget); b.setAttribute("aria-checked", String(b === e.currentTarget)); }
        } }, icon(ic), label)));
  return [
    h("h1", { style: "margin-bottom:22px" }, "Settings"),
    h("section", { class: "settings-section" },
      h("h2", {}, "Appearance"),
      seg,
      h("p", { class: "subtle" }, "System follows your device's light or dark setting. The choice is kept in this browser.")),
    h("section", { class: "settings-section" },
      h("div", { class: "head-row", style: "margin-bottom:6px" },
        h("div", {}, h("h2", { style: "margin:0" }, "Library"), h("div", { class: "subtle" }, `Music folder: `, h("code", {}, s.music_dir))),
        h("div", { class: "btn-row" },
          h("button", { class: "btn primary", onclick: async () => { await post("/api/rescan"); toast("Rescanning library"); setTimeout(route, 1500); } }, "Rescan now"),
          h("button", { class: "btn", onclick: async () => { await post("/api/rescan?full=1"); toast("Re-reading all tags"); setTimeout(route, 1500); } }, "Re-read all tags"))),
      h("div", { class: "stats" },
        [[st.tracks, "songs"], [st.albums, "albums"], [st.artists, "artists"], [st.genres, "genres"], [st.playlists, "playlists"],
          [fmtLong(st.duration_ms), "of music"], [`${(st.bytes / 1e9).toFixed(1)} GB`, "on disk"]].map(([v, l]) =>
          h("div", { class: "stat" }, h("b", {}, v), h("span", {}, l)))),
      h("p", { class: "subtle" }, s.scanning ? "A scan is running now." : last
        ? `Last scan ${new Date(s.last_scan_time * 1000).toLocaleString()}: ${last.added} added, ${last.updated} updated, ${last.removed} removed in ${last.seconds}s. Changes in the music folder are picked up automatically.`
        : "No scan has completed yet.")),
    h("section", { class: "settings-section" },
      h("h2", {}, "Receiver firmware"),
      h("p", { class: "subtle" }, s.boot_enabled
        ? "Receivers download their software from this server every time they power on. Choose what they boot:"
        : "Network boot is disabled (RIO_ENABLE_BOOT=0)."),
      s.boot_enabled ? h("p", { class: "subtle" }, "To boot something else, such as the receiver.arf from an original Rio or Dell installation, copy it into the ",
        h("code", {}, "firmware"), " folder of the data volume and select it here.") : null,
      s.boot_enabled ? fwList : null),
    h("section", { class: "settings-section" },
      h("h2", {}, "Keyboard shortcuts"),
      h("div", { class: "list" }, [["Space", "Play / pause"], ["N / P", "Next / previous"], ["+ / −", "Volume"], ["S / R", "Shuffle / repeat"],
        ["Q", "Now Playing and Up Next"], ["/", "Search"], ["Esc", "Close"]].map(([k, v]) =>
        h("div", { class: "row" }, h("span", {}, v), h("code", {}, k))))),
  ];
}

// ---------------------------------------------------------------- routing

let currentRoute = "";
const routes = [
  [/^artists$/, viewArtists, "artists"],
  [/^artists\/(\d+)$/, viewArtist, "artists"],
  [/^albums$/, viewAlbums, "albums"],
  [/^albums\/(\d+)$/, viewAlbum, "albums"],
  [/^songs$/, viewSongs, "songs"],
  [/^genres$/, viewGenres, "genres"],
  [/^genres\/(\d+)$/, viewGenre, "genres"],
  [/^playlists$/, viewPlaylists, "playlists"],
  [/^playlists\/(\d+)$/, viewPlaylist, "playlists"],
  [/^podcasts$/, viewPodcasts, "podcasts"],
  [/^podcasts\/(\d+)$/, viewPodcast, "podcasts"],
  [/^recent$/, viewRecent, "recent"],
  [/^history$/, viewHistory, "history"],
  [/^receivers$/, viewReceivers, "receivers"],
  [/^alarms$/, viewAlarms, "alarms"],
  [/^settings$/, viewSettings, "settings"],
  [/^search\/(.+)$/, (q) => viewSearch(decodeURIComponent(q)), "search"],
];

// Going back (the edge swipe, the back button) returns to a page as it was
// left: each history entry gets a key, and the page drawn for it and how far
// it was scrolled are kept. A new page (a link, the menu) starts at the top.
// The pages are kept for this visit; the scroll positions survive a reload.
const VIEW_CACHE = 15;
const viewCache = new Map(); // entry key -> {path, nodes}
const scrollPos = new Map((() => { try { return JSON.parse(sessionStorage.getItem("rio.scroll") || "[]"); } catch { return []; } })());
let currentKey = null, scrollSaveTimer = null;
view.addEventListener("scroll", () => {
  if (!currentKey) return;
  scrollPos.set(currentKey, view.scrollTop);
  clearTimeout(scrollSaveTimer);
  scrollSaveTimer = setTimeout(() => {
    try { sessionStorage.setItem("rio.scroll", JSON.stringify([...scrollPos].slice(-60))); } catch { /* full or private */ }
  }, 400);
}, { passive: true });
function entryKey() {
  const st = history.state;
  if (st && st.rioKey) return [st.rioKey, true];
  const key = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  history.replaceState({ ...(st || {}), rioKey: key }, "");
  return [key, false];
}

let routeSeq = 0;
async function route({ keepScroll = false } = {}) {
  let path = location.hash.replace(/^#\/?/, "") || "artists";
  if (path === "library") { location.replace("#/settings"); return; }
  const seq = ++routeSeq;
  const [key, seen] = entryKey();
  const returning = seen && !keepScroll && key !== currentKey;
  currentKey = key;
  for (const [re, fn, nav] of routes) {
    const m = path.match(re);
    if (!m) continue;
    currentRoute = nav;
    if (nav !== "receivers") receiverHolder = null;
    document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === nav));
    toggleSidebar(false);
    const top = view.scrollTop;
    const cached = returning && nav !== "receivers" && viewCache.get(key);
    if (cached && cached.path === path) {
      // Back to a page drawn before: put it back as it was, at once.
      view.replaceChildren(...cached.nodes);
      view.scrollTop = scrollPos.get(key) || 0;
      markPlaying();
      return;
    }
    try {
      const content = await fn(...m.slice(1));
      if (seq !== routeSeq) return;
      view.replaceChildren(...[content].flat(5).filter(Boolean));
    } catch (e) {
      if (seq !== routeSeq) return;
      view.replaceChildren(h("div", { class: "empty" }, h("h2", {}, "Something went wrong"), h("p", {}, e.message)));
    }
    viewCache.delete(key);
    viewCache.set(key, { path, nodes: [...view.childNodes] });
    while (viewCache.size > VIEW_CACHE) viewCache.delete(viewCache.keys().next().value);
    view.scrollTop = keepScroll ? top : returning ? (scrollPos.get(key) || 0) : 0;
    // Podcast downloads in progress: look again shortly.
    if (nav === "podcasts" && view.querySelector(".spin")) {
      setTimeout(() => { if (seq === routeSeq) route({ keepScroll: true }); }, 3000);
    }
    return;
  }
  location.hash = "#/artists";
}
window.addEventListener("hashchange", () => { closeMenu(); route(); });

let searchTimer;
$("#search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  // Typing changes the results page in place: going back leaves search
  // instead of stepping back through every letter typed.
  searchTimer = setTimeout(() => {
    const to = q ? `#/search/${encodeURIComponent(q)}` : "#/artists";
    if (currentRoute === "search") location.replace(to);
    else location.hash = to;
  }, 250);
});
function toggleSidebar(open) {
  const sb = $("#sidebar");
  const want = open === undefined ? !sb.classList.contains("open") : open;
  sb.classList.toggle("open", want);
  $("#scrim").classList.toggle("show", want);
}
$("#menu-btn").addEventListener("click", () => toggleSidebar());
// Choosing the page you're already on from the menu goes back to its top.
for (const link of document.querySelectorAll("#sidebar a[href^='#/']")) {
  link.addEventListener("click", (e) => {
    if (link.getAttribute("href") !== location.hash) return;
    e.preventDefault();
    toggleSidebar(false);
    view.scrollTo({ top: 0, behavior: "smooth" });
  });
}
$("#scrim").addEventListener("click", () => toggleSidebar(false));

showResume();
route();
pollStatus();
pollReceivers();
schedulePoll();
renderNowPlaying();

// Lets phones install the web interface as an app. Browsers only allow this
// over HTTPS (or on localhost); elsewhere navigator.serviceWorker is missing.
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

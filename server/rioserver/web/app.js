"use strict";

/* RioServer NG web interface: browse the library, play in the browser or on
 * a receiver. No build step and no dependencies. */

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
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === undefined || c === null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const ICON = {
  play: '<svg viewBox="0 0 24 24"><path d="M7 4.5v15l13-7.5z" class="fill"/></svg>',
  shuffle: '<svg viewBox="0 0 24 24"><path d="M3 7h3c4 0 6 10 10 10h4M17 14l3 3-3 3M3 17h3c1.6 0 2.8-1.5 3.9-3.3M13.5 9.8C14.4 8.3 15.4 7 17 7h3M17 4l3 3-3 3"/></svg>',
  speaker: '<svg viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9z" class="fill"/><path d="M16 9a4 4 0 0 1 0 6"/></svg>',
  prev: '<svg viewBox="0 0 24 24"><path d="M6 5v14M19 5 9 12l10 7z" class="fill"/></svg>',
  next: '<svg viewBox="0 0 24 24"><path d="M18 5v14M5 5l10 7-10 7z" class="fill"/></svg>',
  pause: '<svg viewBox="0 0 24 24"><path d="M7 4h3.5v16H7zM13.5 4H17v16h-3.5z" class="fill"/></svg>',
  stop: '<svg viewBox="0 0 24 24"><path d="M6 6h12v12H6z" class="fill"/></svg>',
  moon: '<svg viewBox="0 0 24 24"><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/></svg>',
};
// The firmware treats buffering as playing (pressing toggle pauses it), so the
// play/pause button should show pause for both.
const rxPlaying = (st) => !!st && (st.st === "play" || st.st === "buffer");

const icon = (name) => h("span", { html: ICON[name], style: "display:inline-flex" });

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: opts.body ? { "Content-Type": "application/json" } : undefined,
  });
  if (!res.ok) throw new Error((await res.text()) || res.statusText);
  return res.json();
}
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });

function fmtTime(ms) {
  const s = Math.max(0, Math.round((ms || 0) / 1000));
  const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60;
  return hh ? `${hh}:${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}` : `${mm}:${String(ss).padStart(2, "0")}`;
}
function fmtLong(ms) {
  const m = Math.round((ms || 0) / 60000);
  return m >= 60 ? `${Math.floor(m / 60)} hr ${m % 60} min` : `${m} min`;
}
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

function cover(albumId, title, extra) {
  const el = h("div", { class: "cover" });
  if (albumId) {
    const img = h("img", { src: `/api/art/album/${albumId}`, alt: "", loading: "lazy" });
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
  toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
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
function volumeSender(getIp) {
  let last = 0, timer = null, value = null;
  const send = () => {
    timer = null;
    last = Date.now();
    post(`/api/receivers/${getIp()}/command`, { cmd: "vol", value }).catch((e) => toast(e.message));
  };
  return (v) => {
    value = v;
    const wait = 150 - (Date.now() - last);
    if (wait <= 0 && !timer) send();
    else if (!timer) timer = setTimeout(send, Math.max(0, wait));
  };
}

// ---------------------------------------------------------------- player

const audio = $("#audio");
const Player = {
  target: localStorage.getItem("rio.target") || "",
  queue: [],
  index: -1,
  shuffle: false,
  repeat: "off",
  receivers: [],

  get current() { return this.queue[this.index]; },
  get receiver() { return this.receivers.find((r) => r.ip === this.target); },

  async playTracks(tracks, index = 0, { shuffle = false } = {}) {
    if (!tracks.length) return;
    if (this.target) {
      try {
        await post(`/api/receivers/${this.target}/play`, {
          track_ids: tracks.map((t) => t.id), index, shuffle,
        });
        toast(`Playing on ${this.receiver ? this.receiver.name : this.target}`);
        pollReceivers();
      } catch (e) {
        toast(e.message);
      }
      return;
    }
    let order = tracks.slice();
    if (shuffle || this.shuffle) {
      const first = order.splice(shuffle ? Math.floor(Math.random() * order.length) : index, 1);
      for (let i = order.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [order[i], order[j]] = [order[j], order[i]];
      }
      order = first.concat(order);
      index = 0;
      if (shuffle) this.shuffle = true;
    }
    this.queue = order;
    this.start(index);
  },

  start(i) {
    this.index = i;
    const t = this.current;
    if (!t) return;
    audio.src = `/api/stream/${t.id}`;
    audio.play().catch(() => {});
    renderNowPlaying();
    markPlaying();
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
    else this.start(this.index - 1);
  },
  async command(cmd, value) {
    try {
      await post(`/api/receivers/${this.target}/command`, value === undefined ? { cmd } : { cmd, value });
      setTimeout(pollReceivers, 350); // the receiver collects commands at once
    } catch (e) {
      toast(e.message);
    }
  },
};

audio.addEventListener("ended", () => {
  if (Player.repeat === "one") { audio.currentTime = 0; audio.play(); return; }
  Player.next();
});
audio.addEventListener("play", renderNowPlaying);
audio.addEventListener("pause", renderNowPlaying);
audio.addEventListener("timeupdate", renderProgress);
audio.volume = Number(localStorage.getItem("rio.volume") || 80) / 100;
$("#volume").value = Math.round(audio.volume * 100);

$("#btn-play").addEventListener("click", () => Player.toggle());
for (const el of [$("#np-title"), $("#np-art")]) {
  el.addEventListener("click", () => {
    const album = $("#np-title").dataset.album;
    if (album) location.hash = `#/albums/${album}`;
  });
}
$("#btn-next").addEventListener("click", () => Player.next());
$("#btn-prev").addEventListener("click", () => Player.prev());
$("#btn-shuffle").addEventListener("click", () => {
  Player.shuffle = !Player.shuffle;
  $("#btn-shuffle").classList.toggle("on", Player.shuffle);
  toast(Player.shuffle ? "Shuffle on" : "Shuffle off");
});
$("#btn-repeat").addEventListener("click", () => {
  Player.repeat = { off: "all", all: "one", one: "off" }[Player.repeat];
  $("#btn-repeat").classList.toggle("on", Player.repeat !== "off");
  $("#btn-repeat").title = `Repeat: ${Player.repeat}`;
  toast(`Repeat ${Player.repeat}`);
});
$("#seek").addEventListener("input", (e) => {
  const frac = e.target.value / 1000;
  if (Player.target) {
    const r = Player.receiver, t = r && r.state && r.state.track;
    if (t) Player.command("seek", Math.round(frac * t.duration_ms));
  } else if (audio.duration) {
    audio.currentTime = frac * audio.duration;
  }
});
trackBusy($("#volume"));
const sendTargetVolume = volumeSender(() => Player.target);
$("#volume").addEventListener("input", (e) => {
  const v = Number(e.target.value);
  if (Player.target) {
    sendTargetVolume(v);
  } else {
    audio.volume = v / 100;
    localStorage.setItem("rio.volume", v);
  }
});
// Space play/pause, N/P next/previous, +/- volume, / search.
function nudgeVolume(delta) {
  const slider = $("#volume");
  slider.value = Math.max(0, Math.min(100, Number(slider.value) + delta));
  slider.dispatchEvent(new Event("input"));
}
document.addEventListener("keydown", (e) => {
  if (e.ctrlKey || e.metaKey || e.altKey || /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) return;
  const action = {
    " ": () => Player.toggle(), n: () => Player.next(), p: () => Player.prev(),
    "+": () => nudgeVolume(5), "=": () => nudgeVolume(5), "-": () => nudgeVolume(-5),
    "/": () => $("#search").focus(),
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
    artwork: [{ src: `/api/art/album/${t.album_id}`, sizes: "512x512" }],
  });
}
if ("mediaSession" in navigator) {
  const ms = navigator.mediaSession;
  ms.setActionHandler("play", () => Player.toggle());
  ms.setActionHandler("pause", () => Player.toggle());
  ms.setActionHandler("nexttrack", () => Player.next());
  ms.setActionHandler("previoustrack", () => Player.prev());
}

function renderNowPlaying() {
  const bar = $("#player");
  let t, playing = false, sub = "";
  if (Player.target) {
    const r = Player.receiver;
    const st = r && r.state;
    t = st && st.track;
    playing = rxPlaying(st);
    sub = t ? `${t.artist} — ${t.album}` : (r ? `${r.name} · ${r.online ? "idle" : "offline"}` : "");
    if (st && st.vol && !isBusy($("#volume"))) $("#volume").value = st.vol;
  } else {
    t = Player.current;
    playing = t && !audio.paused;
    if (t) sub = `${t.artist} — ${t.album}`;
  }
  bar.classList.toggle("playing", !!playing);
  $("#btn-play").setAttribute("aria-label", playing ? "Pause" : "Play");
  const art = $("#np-art");
  $("#np-title").textContent = t ? t.title : "Nothing playing";
  $("#np-title").dataset.album = t ? t.album_id : "";
  const msKey = !Player.target && t ? String(t.id) : "";
  if (Player._msKey !== msKey) { Player._msKey = msKey; updateMediaSession(t); }
  $("#np-sub").textContent = t ? sub : (sub || "Pick something from your library");
  const key = t ? `${t.album_id}` : "";
  if (art.dataset.key !== key) {
    art.dataset.key = key;
    art.replaceChildren(t ? cover(t.album_id, t.album) : "");
  }
  renderProgress();
}

function renderProgress() {
  let pos = 0, dur = 0;
  if (Player.target) {
    const st = Player.receiver && Player.receiver.state;
    if (st && st.track) {
      dur = st.track.duration_ms;
      pos = Number(st.pos || 0);
      if (st.st === "play" && Player._statusAt) pos += Date.now() - Player._statusAt;
      pos = Math.min(pos, dur);
    }
  } else if (Player.current) {
    pos = (audio.currentTime || 0) * 1000;
    dur = (audio.duration || Player.current.duration_ms / 1000) * 1000;
  }
  $("#t-pos").textContent = fmtTime(pos);
  $("#t-dur").textContent = fmtTime(dur);
  if (document.activeElement !== $("#seek")) $("#seek").value = dur ? Math.round((pos / dur) * 1000) : 0;
}
setInterval(() => { if (Player.target) renderProgress(); }, 500);

function markPlaying() {
  let id = null;
  if (Player.target) {
    const st = Player.receiver && Player.receiver.state;
    id = st && st.track ? st.track.id : null;
  } else if (Player.current) {
    id = Player.current.id;
  }
  document.querySelectorAll("tr[data-track]").forEach((tr) => {
    tr.classList.toggle("playing", Number(tr.dataset.track) === id);
  });
}

// ---------------------------------------------------------------- receivers

async function pollReceivers() {
  try {
    Player.receivers = await api("/api/receivers");
  } catch {
    return;
  }
  Player._statusAt = Date.now();
  const sel = $("#target");
  const want = [["", "This browser"]].concat(
    Player.receivers.filter((r) => r.firmware === "ng").map((r) => [r.ip, `${r.name}${r.online ? "" : " (offline)"}`]),
  );
  const have = [...sel.options].map((o) => [o.value, o.textContent]);
  if (JSON.stringify(want) !== JSON.stringify(have)) {
    sel.replaceChildren(...want.map(([v, label]) => h("option", { value: v }, label)));
  }
  if (Player.target && !Player.receivers.some((r) => r.ip === Player.target)) Player.target = "";
  sel.value = Player.target;
  const online = Player.receivers.filter((r) => r.online).length;
  const pill = $("#receiver-count");
  pill.hidden = !online;
  pill.textContent = online;
  if (Player.target) { renderNowPlaying(); markPlaying(); }
  if (currentRoute === "receivers") renderReceiverCards();
}
$("#target").addEventListener("change", (e) => {
  Player.target = e.target.value;
  localStorage.setItem("rio.target", Player.target);
  if (Player.target) audio.pause();
  else $("#volume").value = Math.round(audio.volume * 100);
  renderNowPlaying();
  markPlaying();
  toast(Player.target ? `Playing on ${Player.receiver ? Player.receiver.name : Player.target}` : "Playing in this browser");
});
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
    if (libVersion !== null && s.version !== libVersion && ["artists", "albums", "songs", "genres", "recent"].includes(currentRoute)) {
      route();
    }
    libVersion = s.version;
  } catch { /* server restarting */ }
}
setInterval(pollStatus, 5000);

// ---------------------------------------------------------------- views

function trackTable(tracks, { album = false, artist = false, numbers = true } = {}) {
  const multiDisc = new Set(tracks.map((t) => t.disc_no || 1)).size > 1;
  const rows = [];
  let disc = null;
  tracks.forEach((t, i) => {
    if (multiDisc && (t.disc_no || 1) !== disc) {
      disc = t.disc_no || 1;
      rows.push(h("tr", { class: "disc-row" }, h("td", { colspan: 4 }, `Disc ${disc}`)));
    }
    const showArtist = artist || (t.artist !== t.album_artist);
    rows.push(h("tr", { "data-track": t.id, onclick: () => Player.playTracks(tracks, i) },
      h("td", { class: "n" }, h("span", { class: "num" }, numbers ? (t.track_no || "") : i + 1), h("span", { class: "go", html: ICON.play })),
      h("td", { class: "title" }, t.title, showArtist ? h("small", {}, t.artist) : null),
      album ? h("td", { class: "col-album" }, h("a", { href: `#/albums/${t.album_id}`, onclick: (e) => e.stopPropagation() }, t.album)) : null,
      h("td", { class: "dur" }, fmtTime(t.duration_ms)),
    ));
  });
  const table = h("table", { class: "tracks" }, h("tbody", {}, rows));
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
  return h("a", { class: "album-tile", href: `#/albums/${al.id}` },
    cover(al.id, al.title, play),
    h("div", { class: "t" }, al.title),
    h("div", { class: "a" }, [al.artist, al.year].filter(Boolean).join(" · ")),
  );
}

function emptyLibrary() {
  return h("div", { class: "empty" },
    h("h2", {}, "Your library is empty"),
    h("p", {}, "Add MP3 files to the folder mounted at ", h("code", {}, "/music"),
      ". New music is picked up automatically within a few seconds."));
}

async function viewArtists() {
  const artists = await api("/api/artists");
  if (!artists.length) return [emptyLibrary()];
  const groups = new Map();
  for (const a of artists) {
    const key = a.name.replace(/^the\s+/i, "").charAt(0).toUpperCase();
    const letter = /[A-Z]/.test(key) ? key : "#";
    if (!groups.has(letter)) groups.set(letter, []);
    groups.get(letter).push(a);
  }
  return [
    h("div", { class: "head-row" },
      h("div", {}, h("h1", {}, "Artists"), h("div", { class: "subtle" }, plural(artists.length, "artist")))),
    h("div", { class: "letters" }, [...groups.keys()].map((l) =>
      h("a", { href: "#", onclick: (e) => { e.preventDefault(); document.getElementById(`letter-${l}`).scrollIntoView({ behavior: "smooth" }); } }, l))),
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
        h("div", { class: "subtle" }, h("a", { href: "#/artists" }, "Artists")),
        h("h1", {}, artist.name),
        h("div", { class: "subtle" }, `${plural(albums.length, "album")} · ${plural(all.length, "song")} · ${fmtLong(all.reduce((s, t) => s + t.duration_ms, 0))}`))),
      h("div", { class: "btn-row" },
        h("button", { class: "btn primary", onclick: () => Player.playTracks(all, 0) }, icon("play"), "Play all"),
        h("button", { class: "btn", onclick: () => Player.playTracks(all, 0, { shuffle: true }) }, icon("shuffle"), "Shuffle"))),
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

async function viewAlbums() {
  const albums = await api("/api/albums");
  if (!albums.length) return [emptyLibrary()];
  return [
    h("div", { class: "head-row" }, h("div", {}, h("h1", {}, "Albums"), h("div", { class: "subtle" }, plural(albums.length, "album")))),
    h("div", { class: "album-grid" }, albums.map(albumTile)),
  ];
}

async function viewAlbum(id) {
  const al = await api(`/api/albums/${id}`);
  return [
    h("div", { class: "album-hero" },
      cover(al.id, al.title),
      h("div", {},
        h("div", { class: "kicker" }, "Album"),
        h("h1", {}, al.title),
        h("div", { class: "by" }, h("a", { href: `#/artists/${al.artist_id}` }, al.artist)),
        h("div", { class: "facts" }, [al.year, al.genre, plural(al.tracks.length, "song"), fmtLong(al.duration_ms)].filter(Boolean).join(" · ")),
        h("div", { class: "btn-row" },
          h("button", { class: "btn primary", onclick: () => Player.playTracks(al.tracks, 0) }, icon("play"), "Play"),
          h("button", { class: "btn", onclick: () => Player.playTracks(al.tracks, 0, { shuffle: true }) }, icon("shuffle"), "Shuffle")))),
    trackTable(al.tracks),
  ];
}

async function viewSongs() {
  const page = 500;
  const first = await api(`/api/songs?offset=0&limit=${page}`);
  if (!first.total) return [emptyLibrary()];
  let tracks = first.tracks;
  const holder = h("div", {});
  const more = h("button", { class: "btn", style: "margin-top:16px" }, "Load more");
  const draw = () => {
    holder.replaceChildren(trackTable(tracks, { album: true, artist: true, numbers: false }));
    more.hidden = tracks.length >= first.total;
  };
  more.addEventListener("click", async () => {
    const next = await api(`/api/songs?offset=${tracks.length}&limit=${page}`);
    tracks = tracks.concat(next.tracks);
    draw();
  });
  draw();
  return [
    h("div", { class: "head-row" },
      h("div", {}, h("h1", {}, "Songs"), h("div", { class: "subtle" }, plural(first.total, "song"))),
      h("button", { class: "btn", onclick: async () => {
        const all = await api(`/api/songs?offset=0&limit=100000`);
        Player.playTracks(all.tracks, 0, { shuffle: true });
      } }, icon("shuffle"), "Shuffle all")),
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
    h("div", { class: "subtle" }, h("a", { href: "#/genres" }, "Genres")),
    h("h1", { style: "margin-bottom:18px" }, g.name),
    h("div", { class: "album-grid" }, g.albums.map(albumTile)),
  ];
}

async function viewPlaylists() {
  const lists = await api("/api/playlists");
  return [
    h("h1", {}, "Playlists"),
    lists.length
      ? h("div", { class: "list", style: "margin-top:16px" }, lists.map((p) =>
        h("a", { href: `#/playlists/${p.id}` }, h("span", {}, p.name), h("span", { class: "count" }, plural(p.track_count, "song")))))
      : h("p", { class: "subtle" }, "Playlists are read from .m3u files in your music folder."),
  ];
}

async function viewPlaylist(id) {
  const p = await api(`/api/playlists/${id}`);
  return [
    h("div", { class: "head-row" },
      h("div", {}, h("div", { class: "subtle" }, h("a", { href: "#/playlists" }, "Playlists")), h("h1", {}, p.name),
        h("div", { class: "subtle" }, plural(p.tracks.length, "song"))),
      h("div", { class: "btn-row" },
        h("button", { class: "btn primary", onclick: () => Player.playTracks(p.tracks, 0) }, icon("play"), "Play"),
        h("button", { class: "btn", onclick: () => Player.playTracks(p.tracks, 0, { shuffle: true }) }, icon("shuffle"), "Shuffle"))),
    trackTable(p.tracks, { album: true, artist: true, numbers: false }),
  ];
}

async function viewRecent() {
  const albums = await api("/api/recent");
  return [
    h("div", { class: "head-row" }, h("div", {}, h("h1", {}, "Recently Added"), h("div", { class: "subtle" }, "Newest albums first"))),
    albums.length ? h("div", { class: "album-grid" }, albums.map(albumTile)) : emptyLibrary(),
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

const STATE_NAMES = { play: "Playing", pause: "Paused", buffer: "Buffering", stop: "Stopped", standby: "Standby" };
const FW_NAMES = { ng: "RioServer NG firmware", stock: "Original Rio firmware", unknown: "Booting" };

// A receiver card is built once and then updated in place, so polling never
// swallows a click or yanks a slider out from under the pointer.
function receiverCard(ip) {
  const r = () => Player.receivers.find((x) => x.ip === ip) || {};
  const cmd = (c, value) => post(`/api/receivers/${ip}/command`, value === undefined ? { cmd: c } : { cmd: c, value })
    .then(() => setTimeout(pollReceivers, 350)).catch((e) => toast(e.message));
  const sendVolume = volumeSender(() => ip);
  const dot = h("span", { class: "dot" });
  const name = h("span", { class: "rx-name" });
  const rename = h("button", {
    class: "icon-btn rename", "aria-label": "Rename", title: "Rename",
    html: '<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9l-4-4L4 16zM14 6l4 4"/></svg>',
    onclick: () => {
      const n = prompt("Name this receiver (for example Kitchen). Leave empty for the default name.", r().label || "");
      if (n === null) return;
      post(`/api/receivers/${ip}/name`, { name: n }).then(pollReceivers).catch((e) => toast(e.message));
    },
  });
  const meta = h("div", { class: "meta" });
  const now = h("div", { class: "now" });
  const playBtn = h("button", { class: "icon-btn", "aria-label": "Play/pause", onclick: () => cmd("toggle") });
  const vol = trackBusy(h("input", { type: "range", min: 0, max: 100, value: 50, "aria-label": "Volume",
    oninput: (e) => { sendVolume(Number(e.target.value)); volLabel.textContent = e.target.value; } }));
  const volLabel = h("span", { class: "vol-label" });
  const transport = h("div", { class: "ctrl" },
    h("button", { class: "icon-btn", "aria-label": "Previous", html: ICON.prev, onclick: () => cmd("prev") }),
    playBtn,
    h("button", { class: "icon-btn", "aria-label": "Next", html: ICON.next, onclick: () => cmd("next") }),
    h("button", { class: "icon-btn", "aria-label": "Stop", html: ICON.stop, onclick: () => cmd("stop") }));
  const volRow = h("div", { class: "vol-row" },
    h("span", { html: ICON.speaker, style: "display:inline-flex;color:var(--muted)" }), vol, volLabel);
  const sleepInfo = h("span", { class: "meta" });
  const sleep = h("select", { class: "sleep", "aria-label": "Sleep timer",
    onchange: (e) => post(`/api/receivers/${ip}/sleep`, { minutes: Number(e.target.value) })
      .then(() => { toast(Number(e.target.value) ? `Sleeping in ${e.target.value} min` : "Sleep timer off"); pollReceivers(); })
      .catch((err) => toast(err.message)) },
    [["0", "Sleep timer"], ["15", "15 min"], ["30", "30 min"], ["45", "45 min"], ["60", "1 hour"], ["90", "90 min"]]
      .map(([v, label]) => h("option", { value: v }, label)));
  const sleepRow = h("div", { class: "sleep-row" }, h("span", { html: ICON.moon, style: "display:inline-flex;color:var(--muted)" }), sleep, sleepInfo);
  const ctrl = h("div", { class: "controls" }, transport, volRow, sleepRow);
  const send = h("button", {
    class: "btn send",
    onclick: () => { Player.target = ip; localStorage.setItem("rio.target", ip); $("#target").value = ip; renderNowPlaying(); toast(`Playing on ${r().name}`); },
  }, "Send music here");
  const el = h("div", { class: "card" }, h("h3", {}, dot, name, rename), meta, now, ctrl, send);
  let playIcon = null;

  function update(rx) {
    const st = rx.state || {};
    const t = st.track;
    dot.className = `dot ${rx.online ? "on" : ""}`;
    name.textContent = rx.name;
    meta.textContent = `${rx.ip}${rx.mac ? " · " + rx.mac : ""} · ${FW_NAMES[rx.firmware] || rx.firmware}`;
    now.replaceChildren(...(t
      ? [h("div", { class: "t" }, t.title), h("div", { class: "meta" }, `${t.artist} — ${t.album}`),
        h("div", { class: "meta" }, `${STATE_NAMES[st.st] || st.st} · ${fmtTime(st.pos)} / ${fmtTime(t.duration_ms)}${st.qn ? ` · track ${Number(st.qi) + 1} of ${st.qn}` : ""}`)]
      : [h("div", { class: "meta" }, rx.firmware === "stock"
        ? "Browsing with the original firmware. Switch to RioServer NG firmware for remote control."
        : rx.online ? "Idle" : `Last seen ${new Date(rx.last_seen * 1000).toLocaleString()}`)]));
    ctrl.hidden = !rx.controllable;
    const left = rx.sleep_at ? Math.max(1, Math.round((rx.sleep_at * 1000 - Date.now()) / 60000)) : 0;
    sleepInfo.textContent = left ? `Stops in ${plural(left, "minute")}` : "";
    if (!left && document.activeElement !== sleep) sleep.value = "0";
    send.hidden = !(rx.firmware === "ng" && rx.online);
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
function renderReceiverCards() {
  if (!receiverHolder) return;
  if (!Player.receivers.length) {
    receiverCards.clear();
    receiverHolder.replaceChildren(h("div", { class: "empty", style: "grid-column:1/-1" },
      h("h2", {}, "No receivers yet"),
      h("p", {}, "Power on a Rio Receiver or Dell Digital Audio Receiver on the same network. It finds this server automatically, boots from it and shows up here.")));
    return;
  }
  const els = Player.receivers.map((rx) => {
    if (!receiverCards.has(rx.ip)) receiverCards.set(rx.ip, receiverCard(rx.ip));
    const card = receiverCards.get(rx.ip);
    card.update(rx);
    return card.el;
  });
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
      h("div", { class: "subtle" }, "Rio Receivers and Dell Digital Audio Receivers that have contacted this server"))),
    receiverHolder,
  ];
}

async function viewLibrary() {
  const [s, fw] = await Promise.all([api("/api/status"), api("/api/firmware")]);
  const st = s.stats;
  const last = s.last_scan;
  const fwInfo = {
    "ng": ["RioServer NG", "iPod-style menus (Artist › Album › Songs), remote control from this page"],
    "rioplay": ["RioPlay 0.29", "David Flowerday's open-source player (2002)"],
  };
  const fwList = h("div", {}, fw.available.length ? fw.available.map((f) => {
    const [title, desc] = fwInfo[f.name] || [f.file, "Custom image (e.g. the original Rio/Dell receiver.arf)"];
    return h("label", { class: `fw-option ${fw.selected === f.name || fw.selected === f.file ? "sel" : ""}` },
      h("input", { type: "radio", name: "fw", checked: fw.selected === f.name || fw.selected === f.file,
        onchange: async () => { await post("/api/firmware", { name: f.name }); toast("Firmware selected; power-cycle your receivers"); route(); } }),
      h("div", { style: "flex:1" }, h("b", {}, title), h("small", {}, `${desc} · ${(f.size / 1024).toFixed(0)} KB`)),
      h("a", { class: "btn", href: `/api/firmware/${encodeURIComponent(f.name)}/download`, onclick: (e) => e.stopPropagation() }, "Download"));
  }) : h("p", { class: "subtle" }, "No firmware images found."));
  return [
    h("div", { class: "head-row" },
      h("div", {}, h("h1", {}, "Library & Firmware"), h("div", { class: "subtle" }, `Music folder: `, h("code", {}, s.music_dir))),
      h("div", { class: "btn-row" },
        h("button", { class: "btn primary", onclick: async () => { await post("/api/rescan"); toast("Rescanning library"); setTimeout(route, 1500); } }, "Rescan now"),
        h("button", { class: "btn", onclick: async () => { await post("/api/rescan?full=1"); toast("Re-reading all tags"); setTimeout(route, 1500); } }, "Re-read all tags"))),
    h("div", { class: "stats" },
      [[st.tracks, "songs"], [st.albums, "albums"], [st.artists, "artists"], [st.genres, "genres"], [st.playlists, "playlists"],
        [fmtLong(st.duration_ms), "of music"], [`${(st.bytes / 1e9).toFixed(1)} GB`, "on disk"]].map(([v, l]) =>
        h("div", { class: "stat" }, h("b", {}, v), h("span", {}, l)))),
    h("p", { class: "subtle" }, s.scanning ? "A scan is running now." : last
      ? `Last scan ${new Date(s.last_scan_time * 1000).toLocaleString()}: ${last.added} added, ${last.updated} updated, ${last.removed} removed in ${last.seconds}s. Changes in the music folder are picked up automatically.`
      : "No scan has completed yet."),
    h("h2", {}, "Receiver firmware"),
    h("p", { class: "subtle" }, s.boot_enabled
      ? "Receivers download their software from this server every time they power on. Choose what they boot:"
      : "Network boot is disabled (RIO_ENABLE_BOOT=0)."),
    s.boot_enabled ? h("p", { class: "subtle" }, "To boot something else, such as the receiver.arf from an original Rio or Dell installation, copy it into the ",
      h("code", {}, "firmware"), " folder of the data volume and select it here.") : null,
    s.boot_enabled ? fwList : null,
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
  [/^recent$/, viewRecent, "recent"],
  [/^receivers$/, viewReceivers, "receivers"],
  [/^library$/, viewLibrary, "library"],
  [/^search\/(.+)$/, (q) => viewSearch(decodeURIComponent(q)), "search"],
];

let routeSeq = 0;
async function route() {
  const path = location.hash.replace(/^#\/?/, "") || "artists";
  const seq = ++routeSeq;
  for (const [re, fn, nav] of routes) {
    const m = path.match(re);
    if (!m) continue;
    currentRoute = nav;
    if (nav !== "receivers") receiverHolder = null;
    document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === nav));
    $("#sidebar").classList.remove("open");
    try {
      const content = await fn(...m.slice(1));
      if (seq !== routeSeq) return;
      view.replaceChildren(...[content].flat(3).filter(Boolean));
    } catch (e) {
      if (seq !== routeSeq) return;
      view.replaceChildren(h("div", { class: "empty" }, h("h2", {}, "Something went wrong"), h("p", {}, e.message)));
    }
    if (!history.state || history.state.path !== path) view.scrollTop = 0;
    return;
  }
  location.hash = "#/artists";
}
window.addEventListener("hashchange", route);

let searchTimer;
$("#search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  searchTimer = setTimeout(() => { location.hash = q ? `#/search/${encodeURIComponent(q)}` : "#/artists"; }, 250);
});
$("#menu-btn").addEventListener("click", () => $("#sidebar").classList.toggle("open"));

route();
pollStatus();
pollReceivers();
schedulePoll();
renderNowPlaying();

// Lets phones install the web interface as an app. Browsers only allow this
// over HTTPS (or on localhost); elsewhere navigator.serviceWorker is missing.
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

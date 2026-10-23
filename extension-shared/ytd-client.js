// Shared client used by both the Chrome and Firefox builds to talk to the
// Master Download Manager desktop app's local API. This file in
// extension-shared/ is the single source; build.py copies it into
// extension-chrome/ and extension-firefox/. Works under either extension namespace:
// Firefox exposes the promise-based `browser` global; Chrome's MV3 service
// worker also returns promises from chrome.* APIs, so we just pick whichever
// exists once and use it everywhere.
const ytdBrowserApi = (typeof browser !== "undefined") ? browser : (typeof chrome !== "undefined" ? chrome : undefined);

const YTD_DEFAULTS = { apiPort: 47821, apiToken: "" };

async function ytdGetConfig() {
  const stored = await ytdBrowserApi.storage.local.get(YTD_DEFAULTS);
  return { apiPort: stored.apiPort || YTD_DEFAULTS.apiPort, apiToken: stored.apiToken || "" };
}

async function ytdSaveConfig(config) {
  await ytdBrowserApi.storage.local.set(config);
}

function ytdApiBase(config) {
  return `http://127.0.0.1:${config.apiPort}`;
}

async function ytdApiFetch(path, { method = "GET", body = null, timeoutMs = 8000 } = {}) {
  const config = await ytdGetConfig();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await fetch(`${ytdApiBase(config)}${path}`, {
      method,
      headers: {
        "Content-Type": "application/json",
        "X-MDM-Token": config.apiToken,
      },
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
    clearTimeout(timer);
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const message = data && data.detail ? data.detail : `Master Download Manager returned ${resp.status}`;
      throw new Error(message);
    }
    return data;
  } catch (err) {
    clearTimeout(timer);
    if (err.name === "AbortError") {
      throw new Error("Master Download Manager did not respond. Is it running?");
    }
    throw err;
  }
}

async function ytdCheckConnection() {
  try {
    await ytdApiFetch("/health", { timeoutMs: 2000 });
  } catch (e) {
    return { connected: false, reason: e.message };
  }
  try {
    await ytdApiFetch("/connection", { timeoutMs: 2000 });
    return { connected: true };
  } catch (e) {
    return { connected: false, reason: e.message };
  }
}

function ytdIsSupportedUrl(url) {
  return /^https?:\/\/(www\.|m\.|music\.)?(youtube\.com\/watch|youtu\.be\/)/i.test(url || "");
}

async function ytdDetect(url) {
  return ytdApiFetch("/media/detect", { method: "POST", body: { url }, timeoutMs: 20000 });
}

async function ytdDetectPlaylist(url) {
  return ytdApiFetch("/playlist/detect", { method: "POST", body: { url }, timeoutMs: 30000 });
}

async function ytdGetAppSettings() {
  return ytdApiFetch("/settings", { timeoutMs: 4000 });
}

async function ytdCreateDownload(url, formatId, options = {}) {
  const { isPlaylist = false, filename = null, saveDir = null, category = null } = options;
  return ytdApiFetch("/downloads", {
    method: "POST",
    body: { url, format_id: formatId, is_playlist: isPlaylist, filename, save_dir: saveDir, category },
  });
}

// Which MDM queue links from this browser go to ("" = the app's Main queue).
async function ytdGetQueuePreference() {
  const stored = await ytdBrowserApi.storage.local.get({ queueId: "" });
  return stored.queueId || null;
}

async function ytdListQueues() {
  return ytdApiFetch("/queues", { timeoutMs: 3000 });
}

// Send any link (media page, file, magnet, .torrent) to the app, which picks
// the right engine and uses the user's default quality/format.
async function ytdSendLink(url, referer = null, title = null, origin = "menu") {
  const queue_id = await ytdGetQueuePreference();
  return ytdApiFetch("/downloads/auto", {
    method: "POST", body: { url, referer, title, queue_id, origin }, timeoutMs: 30000,
  });
}

// "Download selected links": several links in one request.
async function ytdSendLinks(urls, referer = null) {
  const queue_id = await ytdGetQueuePreference();
  return ytdApiFetch("/downloads/batch", { method: "POST", body: { urls, referer, queue_id }, timeoutMs: 30000 });
}

// What the user allowed in MDM → Settings → Browser Integration. Capture of
// browser downloads is OFF unless enabled there; cached for a minute.
let ytdBrowserConfigCache = { value: null, at: 0 };
async function ytdGetBrowserConfig(force = false) {
  if (!force && ytdBrowserConfigCache.value && Date.now() - ytdBrowserConfigCache.at < 60000) {
    return ytdBrowserConfigCache.value;
  }
  let value;
  try {
    value = await ytdApiFetch("/browser/config", { timeoutMs: 1500 });
  } catch (e) {
    value = { capture_enabled: false, file_types: [], min_size_kb: 0, blacklist: [], unreachable: true };
  }
  ytdBrowserConfigCache = { value, at: Date.now() };
  return value;
}

const YTD_MIME_EXT = {
  "application/zip": "zip", "application/x-zip-compressed": "zip", "application/x-rar-compressed": "rar",
  "application/vnd.rar": "rar", "application/x-7z-compressed": "7z", "application/x-msdownload": "exe",
  "application/x-msi": "msi", "application/x-iso9660-image": "iso", "application/pdf": "pdf",
  "video/mp4": "mp4", "video/x-matroska": "mkv", "video/webm": "webm", "audio/mpeg": "mp3",
  "application/vnd.android.package-archive": "apk", "application/x-bittorrent": "torrent",
};

function ytdFileExtension(name, url, mime) {
  const pick = (s) => {
    const m = /\.([a-z0-9]{1,12})$/i.exec(s || "");
    return m ? m[1].toLowerCase() : "";
  };
  const base = (name || "").split(/[\\/]/).pop();
  let ext = pick(base);
  if (!ext) {
    try { ext = pick(decodeURIComponent(new URL(url).pathname)); } catch (e) { ext = ""; }
  }
  return ext || YTD_MIME_EXT[(mime || "").split(";")[0].trim().toLowerCase()] || "";
}

// "example.com" matches example.com and its subdomains; "*.example.com" only subdomains.
function ytdHostBlocked(url, patterns) {
  const host = ytdHostnameOf(url);
  if (!host) return false;
  return (patterns || []).some((raw) => {
    const p = String(raw).toLowerCase().replace(/^https?:\/\//, "").replace(/^www\./, "").split("/")[0];
    if (!p) return false;
    if (p.startsWith("*.")) return host.endsWith(p.slice(1));
    return host === p || host.endsWith("." + p);
  });
}

// Decide whether a browser download should be offered to MDM. Pure function
// (unit-tested with node from tools/browser_extension_test.py).
function ytdShouldCapture(item, cfg) {
  if (!cfg || !cfg.capture_enabled) return { capture: false, why: "capture off" };
  const url = item.finalUrl || item.url || "";
  if (!/^https?:/i.test(url)) return { capture: false, why: "not http(s)" };
  if (item.byExtensionId) return { capture: false, why: "started by an extension" };
  if (ytdHostBlocked(url, cfg.blacklist) || (item.referrer && ytdHostBlocked(item.referrer, cfg.blacklist))) {
    return { capture: false, why: "site excluded" };
  }
  const ext = ytdFileExtension(item.filename, url, item.mime);
  if (!ext || !(cfg.file_types || []).includes(ext)) return { capture: false, why: `type .${ext || "?"} not selected` };
  const size = item.totalBytes > 0 ? item.totalBytes : (item.fileSize > 0 ? item.fileSize : 0);
  if (cfg.min_size_kb && size && size < cfg.min_size_kb * 1024) return { capture: false, why: "smaller than the minimum" };
  return { capture: true, why: `.${ext}` };
}

function ytdIsSendableUrl(url) {
  return /^(https?:\/\/|magnet:\?)/i.test(url || "");
}

async function ytdListDownloads() {
  return ytdApiFetch("/downloads");
}

// ---------------------------------------------------------------------
// Capture rules: the blocked-sites list for the in-page capture popup.
// Lives entirely in the browser (chrome.storage.local) — a different
// concern from the desktop app's own download-engine settings (folders,
// concurrency, retries, ...) stored server-side via /settings.
// ---------------------------------------------------------------------

const YTD_RULES_DEFAULTS = {
  blockedDomains: [],
};

async function ytdGetCaptureRules() {
  const stored = await ytdBrowserApi.storage.local.get({ captureRules: YTD_RULES_DEFAULTS });
  return { ...YTD_RULES_DEFAULTS, ...stored.captureRules };
}

async function ytdSaveCaptureRules(partial) {
  const current = await ytdGetCaptureRules();
  const merged = { ...current, ...partial };
  await ytdBrowserApi.storage.local.set({ captureRules: merged });
  return merged;
}

function ytdHostnameOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch (e) {
    return "";
  }
}

async function ytdIsDomainBlocked(url) {
  const rules = await ytdGetCaptureRules();
  const host = ytdHostnameOf(url);
  return rules.blockedDomains.includes(host);
}

async function ytdBlockDomain(url) {
  const rules = await ytdGetCaptureRules();
  const host = ytdHostnameOf(url);
  if (host && !rules.blockedDomains.includes(host)) {
    rules.blockedDomains.push(host);
    await ytdSaveCaptureRules({ blockedDomains: rules.blockedDomains });
  }
  return host;
}

async function ytdUnblockDomain(host) {
  const rules = await ytdGetCaptureRules();
  await ytdSaveCaptureRules({ blockedDomains: rules.blockedDomains.filter((d) => d !== host) });
}

// Lets the node-based unit test import the pure helpers (ignored by browsers).
if (typeof module !== "undefined" && module.exports) {
  module.exports = { ytdShouldCapture, ytdFileExtension, ytdHostBlocked, ytdHostnameOf, ytdIsSendableUrl };
}

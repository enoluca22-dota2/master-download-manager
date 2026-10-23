// Shared client used by both the Chrome and Firefox builds to talk to the
// Master Download Manager desktop app's local API. This file in
// extension-shared/ is the single source; build.py copies it into
// extension-chrome/ and extension-firefox/. Works under either extension namespace:
// Firefox exposes the promise-based `browser` global; Chrome's MV3 service
// worker also returns promises from chrome.* APIs, so we just pick whichever
// exists once and use it everywhere.
const ytdBrowserApi = (typeof browser !== "undefined") ? browser : chrome;

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

// Send any link (media page, file, magnet, .torrent) to the app, which picks
// the right engine and uses the user's default quality/format.
async function ytdSendLink(url, referer = null, title = null) {
  return ytdApiFetch("/downloads/auto", { method: "POST", body: { url, referer, title }, timeoutMs: 30000 });
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

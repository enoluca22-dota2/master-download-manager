// ytd-client.js is loaded before this file via manifest.json's
// background.scripts array, so no importScripts() call is needed here
// (unlike the Chrome build's service-worker background).

// ---------------------------------------------------------------------
// Session-only "hide YTD for this browser session" flag. This must live in
// a trusted context (the background page), not be read/written directly by
// content scripts: routing through messages here — rather than touching
// storage.session directly from a content script — works the same way on
// both Chrome and Firefox and avoids Chrome's TRUSTED_CONTEXTS-only default.
//
// It intentionally lives in storage.session, not storage.local: it clears
// itself when the browser fully restarts, and it is never written into the
// persistent capture-rules/settings the user configured.
// ---------------------------------------------------------------------
const YTD_SESSION_DISMISS_KEY = "ytdSessionDismissed";
let ytdDismissedFallback = false; // used only if storage.session is unavailable

async function ytdIsSessionDismissed() {
  if (!ytdBrowserApi.storage.session) return ytdDismissedFallback;
  try {
    const stored = await ytdBrowserApi.storage.session.get({ [YTD_SESSION_DISMISS_KEY]: false });
    return !!stored[YTD_SESSION_DISMISS_KEY];
  } catch (e) {
    return ytdDismissedFallback;
  }
}

async function ytdSetSessionDismissed(value) {
  ytdDismissedFallback = value;
  if (!ytdBrowserApi.storage.session) return;
  try {
    await ytdBrowserApi.storage.session.set({ [YTD_SESSION_DISMISS_KEY]: value });
  } catch (e) { /* non-fatal — falls back to the in-memory flag for this worker's lifetime */ }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    try {
      switch (message.type) {
        case "YTD_CHECK_CONNECTION":
          sendResponse({ ok: true, result: await ytdCheckConnection() });
          break;
        case "YTD_DETECT":
          sendResponse({ ok: true, result: await ytdDetect(message.url) });
          break;
        case "YTD_DETECT_PLAYLIST":
          sendResponse({ ok: true, result: await ytdDetectPlaylist(message.url) });
          break;
        case "YTD_GET_APP_SETTINGS":
          sendResponse({ ok: true, result: await ytdGetAppSettings() });
          break;
        case "YTD_CREATE_DOWNLOAD":
          sendResponse({ ok: true, result: await ytdCreateDownload(message.url, message.formatId, message.options || {}) });
          break;
        case "YTD_GET_SESSION_DISMISSED":
          sendResponse({ ok: true, result: await ytdIsSessionDismissed() });
          break;
        case "YTD_SET_SESSION_DISMISSED":
          await ytdSetSessionDismissed(!!message.value);
          sendResponse({ ok: true });
          break;
        case "YTD_OPEN_APP":
          sendResponse({ ok: true });
          break;
        case "YTD_OPEN_OPTIONS":
          chrome.runtime.openOptionsPage();
          sendResponse({ ok: true });
          break;
        default:
          sendResponse({ ok: false, error: "Unknown message type" });
      }
    } catch (err) {
      sendResponse({ ok: false, error: err.message });
    }
  })();
  return true; // keep the message channel open for the async response
});

// ---------------------------------------------------------------------
// Right-click "Download with Master Download Manager" for any link, page or
// <video>/<audio> element on any site. The desktop app picks the engine
// (media site, direct file, magnet/.torrent) and the user's default quality.
// A short badge on the toolbar icon confirms the result.
// ---------------------------------------------------------------------
const YTD_MENU_LINK = "mdm-download-link";
const YTD_MENU_PAGE = "mdm-download-page";
const YTD_MENU_MEDIA = "mdm-download-media";

function ytdCreateMenus() {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: YTD_MENU_LINK, title: "Download link with Master Download Manager", contexts: ["link"] });
    chrome.contextMenus.create({ id: YTD_MENU_MEDIA, title: "Download this video/audio with Master Download Manager", contexts: ["video", "audio"] });
    chrome.contextMenus.create({ id: YTD_MENU_PAGE, title: "Download this page with Master Download Manager", contexts: ["page"] });
    chrome.contextMenus.create({ id: YTD_MENU_SELECTION, title: "Download selected links with Master Download Manager", contexts: ["selection"] });
  });
}
chrome.runtime.onInstalled.addListener(ytdCreateMenus);
if (chrome.runtime.onStartup) chrome.runtime.onStartup.addListener(ytdCreateMenus);

async function ytdFlashBadge(text, color) {
  try {
    await chrome.action.setBadgeBackgroundColor({ color });
    await chrome.action.setBadgeText({ text });
    setTimeout(() => chrome.action.setBadgeText({ text: "" }), 4000);
  } catch (e) { /* badges are a nicety only */ }
}

async function ytdOnMenuClicked(info, tab) {
  let url = null;
  if (info.menuItemId === YTD_MENU_LINK) url = info.linkUrl;
  else if (info.menuItemId === YTD_MENU_MEDIA) {
    // blob:/MediaSource players can't be fetched directly — send the page, which the app scans for media.
    url = info.srcUrl && /^https?:/i.test(info.srcUrl) ? info.srcUrl : info.pageUrl;
  } else if (info.menuItemId === YTD_MENU_PAGE) url = info.pageUrl;
  if (!ytdIsSendableUrl(url)) {
    ytdFlashBadge("✕", "#d14a5a");
    return;
  }
  const referer = tab && tab.url && /^https?:/i.test(tab.url) ? tab.url : null;
  const title = info.menuItemId === YTD_MENU_PAGE && tab ? tab.title : null;
  try {
    await ytdSendLink(url, referer, title);
    ytdFlashBadge("✓", "#178b61");
  } catch (err) {
    console.warn("MDM:", err.message);
    ytdFlashBadge("!", "#d14a5a");
  }
}
chrome.contextMenus.onClicked.addListener(ytdOnMenuClicked);

// ---------------------------------------------------------------------
// MDM 4.0: "Download selected links with Master Download Manager".
// Runs a tiny function in the clicked tab (activeTab grants this only for
// the tab the user right-clicked) that collects the links inside the
// selection — <a href> elements and URLs/magnets typed as text — and sends
// them to the app in one batch. Nothing else is read from the page.
// ---------------------------------------------------------------------
const YTD_MENU_SELECTION = "mdm-download-selection";

function ytdCollectSelectedLinks() {
  const out = new Set();
  const sel = window.getSelection();
  if (!sel || !sel.rangeCount) return [];
  for (let i = 0; i < sel.rangeCount; i++) {
    const range = sel.getRangeAt(i);
    const frag = range.cloneContents();
    frag.querySelectorAll("a[href]").forEach((a) => {
      try { out.add(new URL(a.getAttribute("href"), document.baseURI).href); } catch (e) { /* ignore */ }
    });
    let node = range.commonAncestorContainer;
    while (node && node.nodeType !== 1) node = node.parentNode;
    const inside = node && node.closest ? node.closest("a[href]") : null;
    if (inside) out.add(inside.href);
  }
  (sel.toString().match(/(https?:\/\/[^\s"'<>]+|magnet:\?[^\s"'<>]+)/gi) || []).forEach((u) => out.add(u));
  return [...out].filter((u) => /^(https?:\/\/|magnet:\?)/i.test(u)).slice(0, 500);
}

async function ytdDownloadSelection(tab) {
  if (!tab || tab.id === undefined) return;
  let links = [];
  try {
    const results = await ytdBrowserApi.scripting.executeScript({ target: { tabId: tab.id }, func: ytdCollectSelectedLinks });
    links = (results && results[0] && results[0].result) || [];
  } catch (e) {
    console.warn("MDM: couldn't read the selection:", e.message);
  }
  if (!links.length) {
    ytdFlashBadge("0", "#d14a5a");
    return;
  }
  const referer = tab.url && /^https?:/i.test(tab.url) ? tab.url : null;
  try {
    const result = await ytdSendLinks(links, referer);
    ytdFlashBadge(String(result.added.length), result.failed.length ? "#c98a1a" : "#178b61");
  } catch (err) {
    console.warn("MDM:", err.message);
    ytdFlashBadge("!", "#d14a5a");
  }
}

ytdBrowserApi.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === YTD_MENU_SELECTION) ytdDownloadSelection(tab);
});


// ---------------------------------------------------------------------
// MDM 4.0: optional capture of browser downloads.
// Only when the user switched it on in MDM → Settings → Browser Integration
// (off by default), and only for the file types / sizes / sites chosen there.
// The browser's download is PAUSED (not cancelled) while MDM is asked; it is
// cancelled only after MDM accepted it, and resumed untouched if MDM isn't
// running or can't fetch the file on its own (e.g. it needs a signed-in session).
// ---------------------------------------------------------------------
const ytdCapturing = new Set();

async function ytdOnDownloadCreated(item) {
  if (ytdCapturing.has(item.id) || item.state !== "in_progress") return;
  const cfg = await ytdGetBrowserConfig();
  const decision = ytdShouldCapture(item, cfg);
  if (!decision.capture) return;
  ytdCapturing.add(item.id);
  try {
    try { await ytdBrowserApi.downloads.pause(item.id); } catch (e) { /* already finished/too fast */ }
    const url = item.finalUrl || item.url;
    try {
      await ytdSendLink(url, item.referrer || null, null, "capture");
    } catch (err) {
      console.info("MDM: leaving this download to the browser:", err.message);
      try { await ytdBrowserApi.downloads.resume(item.id); } catch (e) { /* nothing to resume */ }
      if (!/turned off/.test(err.message)) ytdFlashBadge("↩", "#c98a1a");
      if (/turned off/.test(err.message)) ytdGetBrowserConfig(true);
      return;
    }
    try { await ytdBrowserApi.downloads.cancel(item.id); } catch (e) { /* ignore */ }
    try { await ytdBrowserApi.downloads.erase({ id: item.id }); } catch (e) { /* ignore */ }
    ytdFlashBadge("✓", "#178b61");
  } finally {
    ytdCapturing.delete(item.id);
  }
}

if (ytdBrowserApi.downloads && ytdBrowserApi.downloads.onCreated) {
  ytdBrowserApi.downloads.onCreated.addListener((item) => { ytdOnDownloadCreated(item).catch(() => {}); });
}

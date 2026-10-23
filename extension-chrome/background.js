importScripts("ytd-client.js");

// ---------------------------------------------------------------------
// Session-only "hide YTD for this browser session" flag. This must live in
// a trusted context (the service worker), not be read/written directly by
// content scripts: chrome.storage.session defaults to TRUSTED_CONTEXTS-only
// access, so content scripts routing through messages here — rather than
// touching chrome.storage.session themselves — sidesteps that restriction
// entirely and works the same way on both Chrome and Firefox.
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

// Chrome enforces TRUSTED_CONTEXTS-only access to storage.session by
// default; this call (available since Chrome 102, our floor version) opts
// in untrusted contexts too. Firefox doesn't have/need this method, hence
// the feature check rather than assuming it exists.
if (chrome.storage.session && chrome.storage.session.setAccessLevel) {
  chrome.storage.session.setAccessLevel({ accessLevel: "TRUSTED_AND_UNTRUSTED_CONTEXTS" }).catch(() => {});
}

// Central message router: the popup and the in-page overlay both talk to
// the desktop app through here so there's exactly one place that knows
// about the local API's shape.
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
          // We can't launch a desktop process from a browser extension;
          // best we can do is tell the user, which the popup already does.
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

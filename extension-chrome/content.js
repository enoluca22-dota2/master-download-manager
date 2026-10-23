// In-video Capture UI: a small, draggable button (living inside the
// video player itself, so it behaves correctly in fullscreen) that opens a
// compact format popup — clicking any format in it queues the download
// immediately, with no separate confirmation step. YouTube is a single-page
// app, so we watch its own `yt-navigate-finish` event (plus a lightweight
// fallback) to know when the video changed, and always de-duplicate before
// inserting UI.

const YTD_BUTTON_ID = "ytd-download-button";
const YTD_FORMAT_POPUP_ID = "ytd-format-popup";
const YTD_TOAST_ID = "ytd-toast";
const YTD_CACHE_TTL_MS = 5 * 60 * 1000;
const YTD_VIDEO_ROWS_VISIBLE = 4;

// Detected-formats cache, keyed by URL — cleared whenever the page
// navigates to a different video, and expires on its own after a few
// minutes so a Retry (or reopening much later) never trusts stale data.
let ytdMediaCache = new Map();
let ytdLastUrl = "";

function ytdIsWatchPage() {
  return location.hostname.includes("youtube.com") && location.pathname === "/watch" && location.search.includes("v=");
}

// A watch URL opened from within a playlist carries this marker — mirrors
// ytd/providers/youtube.py's is_playlist_url(), which the local API uses to
// decide whether /playlist/detect has anything to return for this page.
function ytdIsPlaylistUrl(url) {
  return (url || "").includes("list=");
}

function ytdCacheGet(url) {
  const entry = ytdMediaCache.get(url);
  if (!entry) return null;
  if (Date.now() - entry.time > YTD_CACHE_TTL_MS) {
    ytdMediaCache.delete(url);
    return null;
  }
  return entry.media;
}

function ytdCacheSet(url, media) {
  ytdMediaCache.set(url, { media, time: Date.now() });
}

function ytdRemoveUi() {
  document.getElementById(YTD_BUTTON_ID)?.remove();
  document.getElementById(YTD_FORMAT_POPUP_ID)?.remove();
}

function ytdCloseAllPopups() {
  const fmt = document.getElementById(YTD_FORMAT_POPUP_ID);
  if (fmt) ytdClosePopup(fmt);
}

// ---------------------------------------------------------------------
// Session-only dismissal ("×" on the button). Routed through the
// background script's trusted-context storage.session wrapper — see
// background.js for why content scripts don't touch storage.session
// directly. Clears itself on a full browser restart; never touches the
// persistent capture rules / YTD settings.
// ---------------------------------------------------------------------
// Reloading/updating the extension invalidates the messaging channel for
// any content script already injected into an already-open tab (Chrome
// doesn't re-inject it) — chrome.runtime.sendMessage then either throws
// synchronously or calls back with chrome.runtime.lastError set, forever,
// no matter how many times the user retries within that same page load.
// The only real fix is refreshing the tab, so that's surfaced distinctly
// (staleContext) rather than looking like a generic "not connected" error.
function ytdSendMessage(message) {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(message, (resp) => {
        if (chrome.runtime.lastError) {
          resolve({ ok: false, error: chrome.runtime.lastError.message, staleContext: true });
          return;
        }
        resolve(resp);
      });
    } catch (e) {
      resolve({ ok: false, error: e.message, staleContext: true });
    }
  });
}

async function ytdIsSessionDismissed() {
  try {
    const resp = await ytdSendMessage({ type: "YTD_GET_SESSION_DISMISSED" });
    return !!(resp?.ok && resp.result);
  } catch (e) {
    return false; // fail open — never let a messaging hiccup permanently hide the button
  }
}

async function ytdDismissForSession() {
  try {
    await ytdSendMessage({ type: "YTD_SET_SESSION_DISMISSED", value: true });
  } catch (e) { /* the button is removed from this page regardless */ }
}

// ---------------------------------------------------------------------
// Button position, persisted as a fraction of the player's current
// width/height so "wherever I left it" still makes sense through theater
// mode, fullscreen, and window resizes.
// ---------------------------------------------------------------------
const YTD_DEFAULT_BUTTON_POS = { xPercent: 0.92, yPercent: 0.86 };
const YTD_BUTTON_POS_KEY = "ytdButtonPosition";

function ytdGetPlayerContainer() {
  return document.querySelector("#movie_player") || document.querySelector(".html5-video-player");
}

async function ytdGetButtonPosition() {
  try {
    const stored = await ytdBrowserApi.storage.local.get({ [YTD_BUTTON_POS_KEY]: YTD_DEFAULT_BUTTON_POS });
    return stored[YTD_BUTTON_POS_KEY] || YTD_DEFAULT_BUTTON_POS;
  } catch (e) {
    return YTD_DEFAULT_BUTTON_POS;
  }
}

async function ytdSaveButtonPosition(pos) {
  try {
    await ytdBrowserApi.storage.local.set({ [YTD_BUTTON_POS_KEY]: pos });
  } catch (e) { /* non-fatal — the button just won't remember this time */ }
}

function ytdApplyButtonPosition(button, container, pos) {
  const cw = container.clientWidth || 640;
  const ch = container.clientHeight || 360;
  const bw = button.offsetWidth || 74;
  const bh = button.offsetHeight || 34;
  const left = Math.min(Math.max(pos.xPercent * cw - bw / 2, 4), Math.max(cw - bw - 4, 4));
  const top = Math.min(Math.max(pos.yPercent * ch - bh / 2, 4), Math.max(ch - bh - 4, 4));
  button.style.left = `${left}px`;
  button.style.top = `${top}px`;
  button._ytdPos = pos;
}

function ytdMakeButtonDraggable(button, container) {
  let dragging = false;
  let moved = false;
  let startX = 0;
  let startY = 0;
  let startLeft = 0;
  let startTop = 0;

  button.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    dragging = true;
    moved = false;
    startX = e.clientX;
    startY = e.clientY;
    startLeft = parseFloat(button.style.left) || 0;
    startTop = parseFloat(button.style.top) || 0;
    button.classList.add("ytd-dragging");
    e.preventDefault();
  });

  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const dx = e.clientX - startX;
    const dy = e.clientY - startY;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) {
      if (!moved) ytdCloseAllPopups(); // a real drag starting closes any open popup
      moved = true;
    }
    if (!moved) return;

    const cw = container.clientWidth || 640;
    const ch = container.clientHeight || 360;
    const bw = button.offsetWidth || 74;
    const bh = button.offsetHeight || 34;
    const left = Math.min(Math.max(startLeft + dx, 4), Math.max(cw - bw - 4, 4));
    const top = Math.min(Math.max(startTop + dy, 4), Math.max(ch - bh - 4, 4));
    button.style.left = `${left}px`;
    button.style.top = `${top}px`;
  });

  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    button.classList.remove("ytd-dragging");
    if (moved) {
      const cw = container.clientWidth || 640;
      const ch = container.clientHeight || 360;
      const bw = button.offsetWidth || 74;
      const bh = button.offsetHeight || 34;
      const left = parseFloat(button.style.left) || 0;
      const top = parseFloat(button.style.top) || 0;
      const pos = {
        xPercent: (left + bw / 2) / cw,
        yPercent: (top + bh / 2) / ch,
      };
      button._ytdPos = pos;
      ytdSaveButtonPosition(pos);
    }
  });

  // A drag that actually moved the button shouldn't also fire the click
  // that opens the format popup.
  button.addEventListener("click", (e) => {
    if (moved) {
      e.stopImmediatePropagation();
      e.preventDefault();
      moved = false;
    }
  }, true);

  button.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      ytdToggleFormatPopup();
    }
  });
}

// Identity of what the page is showing: the video + playlist ids. YouTube
// rewrites other URL parts after load (e.g. it corrects `index=`), which must
// not count as navigating to a different video and close an open popup.
function ytdVideoKey(url) {
  try {
    const u = new URL(url);
    return `${u.pathname}|${u.searchParams.get("v") || ""}|${u.searchParams.get("list") || ""}`;
  } catch (e) {
    return url || "";
  }
}

async function ytdEnsureButton() {
  if (ytdVideoKey(location.href) !== ytdVideoKey(ytdLastUrl)) {
    document.getElementById(YTD_FORMAT_POPUP_ID)?.remove();
  }
  ytdLastUrl = location.href;

  if (!ytdIsWatchPage()) {
    ytdRemoveUi();
    return;
  }

  try {
    if (await ytdIsSessionDismissed()) {
      ytdRemoveUi();
      return;
    }
    if (await ytdIsDomainBlocked(location.href)) {
      ytdRemoveUi();
      return;
    }
  } catch (e) {
    // Storage/messaging hiccup — fail open (still offer the button) rather
    // than silently disappearing with no way to know why.
    console.warn("MDM: could not check dismissal/blocklist state, showing button anyway:", e);
  }

  const container = ytdGetPlayerContainer();
  const existing = document.getElementById(YTD_BUTTON_ID);
  if (existing) {
    // The player container can get swapped out by YouTube's SPA router; if
    // our button ended up orphaned in a detached/old container, re-home it
    // instead of leaving a dead duplicate around.
    if (container && existing.parentElement !== container) {
      container.appendChild(existing);
    }
    return;
  }

  const button = document.createElement("div");
  button.id = YTD_BUTTON_ID;
  button.setAttribute("role", "button");
  button.tabIndex = 0;
  button.setAttribute("aria-label", "Download this video with Master Download Manager. Drag to reposition.");
  button.title = "Download with Master Download Manager (drag to move)";
  button.innerHTML = `
    <span class="ytd-btn-icon">${ytdSvgIcon("download")}</span>
    <span class="ytd-btn-label">Download video</span>
    <button type="button" class="ytd-btn-x" tabindex="0"
            title="Hide Master Download Manager for this browser session" aria-label="Hide Master Download Manager for this browser session">${ytdSvgIcon("close")}</button>
  `;
  button.addEventListener("click", ytdToggleFormatPopup);

  const closeBtn = button.querySelector(".ytd-btn-x");
  closeBtn.addEventListener("mousedown", (e) => e.stopPropagation());
  closeBtn.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") e.stopPropagation();
  });
  closeBtn.addEventListener("click", async (e) => {
    e.stopPropagation();
    e.preventDefault();
    ytdRemoveUi();
    await ytdDismissForSession();
  });

  const host = container || document.body;
  if (container && getComputedStyle(container).position === "static") {
    container.style.position = "relative";
  }
  button.classList.toggle("ytd-in-player", !!container);
  host.appendChild(button);

  const pos = await ytdGetButtonPosition();
  ytdApplyButtonPosition(button, host, pos);
  ytdMakeButtonDraggable(button, host);

  if (container && "ResizeObserver" in window) {
    const observer = new ResizeObserver(() => {
      if (!button.isConnected) { observer.disconnect(); return; }
      ytdApplyButtonPosition(button, host, button._ytdPos || pos);
    });
    observer.observe(host);
  }
}

// ---------------------------------------------------------------------
// Popup positioning: unlike the button (which stays inside the player so it
// survives fullscreen), the format popup is a free-floating window — it's
// hosted on document.body with position:fixed so it can be dragged anywhere
// on the page, including outside the video player, and it remembers exactly
// where it was left (viewport pixel coordinates) across opens. The
// trade-off is it won't render during native fullscreen playback, since
// only descendants of the Fullscreen API's fullscreen element are visible
// there — an accepted trade for free dragging.
// ---------------------------------------------------------------------
const YTD_POPUP_POS_KEY = "ytdFormatPopupPosition";

async function ytdGetPopupPosition() {
  try {
    const stored = await ytdBrowserApi.storage.local.get({ [YTD_POPUP_POS_KEY]: null });
    return stored[YTD_POPUP_POS_KEY];
  } catch (e) {
    return null;
  }
}

async function ytdSavePopupPosition(pos) {
  try {
    await ytdBrowserApi.storage.local.set({ [YTD_POPUP_POS_KEY]: pos });
  } catch (e) { /* non-fatal — the popup just won't remember this time */ }
}

function ytdClampPopupToViewport(popup) {
  if (!popup || !popup.isConnected) return;
  const margin = 8;
  const rect = popup.getBoundingClientRect();
  const maxLeft = Math.max(margin, window.innerWidth - rect.width - margin);
  const maxTop = Math.max(margin, window.innerHeight - rect.height - margin);
  const left = Math.min(Math.max(rect.left, margin), maxLeft);
  const top = Math.min(Math.max(rect.top, margin), maxTop);
  popup.style.left = `${Math.round(left)}px`;
  popup.style.top = `${Math.round(top)}px`;
}

// Keeps the popup fully on-screen after its content changes size (loading
// spinner -> format list, "More qualities" expanding, ...) — it does NOT
// re-anchor to the button, so a dragged popup never jumps back.
function ytdReposition(popup) {
  ytdClampPopupToViewport(popup);
}

async function ytdInitPopupPosition(popup) {
  const saved = await ytdGetPopupPosition();
  if (!popup.isConnected) return;
  if (saved) {
    popup.style.left = `${saved.left}px`;
    popup.style.top = `${saved.top}px`;
    ytdClampPopupToViewport(popup);
  } else {
    const anchor = document.getElementById(YTD_BUTTON_ID);
    const margin = 10;
    const popupRect = popup.getBoundingClientRect();
    let left, top;
    if (anchor) {
      const anchorRect = anchor.getBoundingClientRect();
      left = anchorRect.right - popupRect.width;
      top = anchorRect.top - popupRect.height - margin;
      if (top < margin) top = anchorRect.bottom + margin;
    } else {
      left = window.innerWidth - popupRect.width - 24;
      top = 96;
    }
    popup.style.left = `${Math.round(left)}px`;
    popup.style.top = `${Math.round(top)}px`;
    ytdClampPopupToViewport(popup);
  }

  const handle = popup.querySelector(".ytd-popup-header");
  if (handle) ytdMakePopupDraggable(popup, handle);
}

// Generic free-drag for any position:fixed, document.body-hosted element —
// used by both the format popup and the progress toast, so both can be
// dragged anywhere on the page, not just within the video player. Returns a
// cleanup function the caller should invoke when the element is removed.
function ytdMakeElementDraggable(el, handle, { excludeSelector = null, onDragEnd = null } = {}) {
  let dragging = false;
  let moved = false;
  let startX = 0, startY = 0, startLeft = 0, startTop = 0;

  const onMouseDown = (e) => {
    if (e.button !== 0) return;
    if (excludeSelector && e.target.closest(excludeSelector)) return;
    dragging = true;
    moved = false;
    startX = e.clientX;
    startY = e.clientY;
    const rect = el.getBoundingClientRect();
    startLeft = rect.left;
    startTop = rect.top;
    e.preventDefault();
  };

  const onMouseMove = (e) => {
    if (!dragging) return;
    const dx = e.clientX - startX;
    const dy = e.clientY - startY;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) moved = true;
    if (!moved) return;
    const margin = 4;
    const rect = el.getBoundingClientRect();
    const left = Math.min(Math.max(startLeft + dx, margin), window.innerWidth - rect.width - margin);
    const top = Math.min(Math.max(startTop + dy, margin), window.innerHeight - rect.height - margin);
    el.style.left = `${Math.round(left)}px`;
    el.style.top = `${Math.round(top)}px`;
  };

  const onMouseUp = () => {
    if (!dragging) return;
    dragging = false;
    if (moved && onDragEnd) {
      const rect = el.getBoundingClientRect();
      onDragEnd({ left: Math.round(rect.left), top: Math.round(rect.top) });
    }
  };

  handle.addEventListener("mousedown", onMouseDown);
  window.addEventListener("mousemove", onMouseMove);
  window.addEventListener("mouseup", onMouseUp);

  return () => {
    handle.removeEventListener("mousedown", onMouseDown);
    window.removeEventListener("mousemove", onMouseMove);
    window.removeEventListener("mouseup", onMouseUp);
  };
}

function ytdMakePopupDraggable(popup, handle) {
  const dragCleanup = ytdMakeElementDraggable(popup, handle, {
    excludeSelector: ".ytd-popup-close",
    onDragEnd: (pos) => ytdSavePopupPosition(pos),
  });
  const prevCleanup = popup._ytdCleanup;
  popup._ytdCleanup = () => {
    prevCleanup?.();
    dragCleanup();
  };
}

function ytdMakeFloatingPopup(id) {
  document.getElementById(id)?.remove();

  const popup = document.createElement("div");
  popup.id = id;
  popup.className = "ytd-floating-popup";
  popup.tabIndex = -1;
  document.body.appendChild(popup);

  const onOutsideClick = (e) => {
    if (!popup.contains(e.target) && !e.target.closest?.(`#${YTD_BUTTON_ID}`)) {
      ytdClosePopup(popup);
    }
  };
  const onKeyDown = (e) => {
    if (e.key === "Escape") ytdClosePopup(popup);
  };
  // Defer attaching so the click that opened the popup doesn't immediately close it.
  setTimeout(() => {
    document.addEventListener("mousedown", onOutsideClick, true);
    document.addEventListener("keydown", onKeyDown, true);
  }, 0);

  const onWindowResize = () => ytdClampPopupToViewport(popup);
  window.addEventListener("resize", onWindowResize);

  popup._ytdCleanup = () => {
    document.removeEventListener("mousedown", onOutsideClick, true);
    document.removeEventListener("keydown", onKeyDown, true);
    window.removeEventListener("resize", onWindowResize);
  };
  return popup;
}

function ytdClosePopup(popup) {
  popup._ytdCleanup?.();
  popup.remove();
}

function ytdMakeRowClickable(el, handler) {
  el.setAttribute("role", "button");
  el.tabIndex = 0;
  el.addEventListener("click", handler);
  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      handler(e);
    }
  });
}

// ---------------------------------------------------------------------
// Format popup
// ---------------------------------------------------------------------
function ytdToggleFormatPopup() {
  const existing = document.getElementById(YTD_FORMAT_POPUP_ID);
  if (existing) {
    ytdClosePopup(existing);
    return;
  }
  ytdOpenFormatPopup();
}

function ytdOpenFormatPopup() {
  const popup = ytdMakeFloatingPopup(YTD_FORMAT_POPUP_ID);
  popup.classList.add("ytd-format");
  const showTabs = ytdIsPlaylistUrl(location.href);
  popup.innerHTML = `
    <div class="ytd-popup-header">
      <span class="ytd-popup-title">Download video</span>
      <button class="ytd-popup-close" type="button" aria-label="Close">${ytdSvgIcon("close")}</button>
    </div>
    ${showTabs ? `
    <div class="ytd-tab-row">
      <button type="button" class="ytd-tab ytd-tab-active" data-tab="video">Video</button>
      <button type="button" class="ytd-tab" data-tab="playlist">Playlist</button>
    </div>` : ""}
    <div class="ytd-popup-body">${ytdSpinnerHtml("Checking connection…")}</div>
  `;
  popup.dataset.activeTab = "video";
  popup.querySelector(".ytd-popup-close").addEventListener("click", () => ytdClosePopup(popup));
  ytdInitPopupPosition(popup);

  if (showTabs) {
    popup.querySelectorAll(".ytd-tab").forEach((tab) => {
      tab.addEventListener("click", () => ytdSwitchTab(popup, tab.dataset.tab));
    });
  }

  ytdSendMessage({ type: "YTD_CHECK_CONNECTION" }).then((resp) => {
    if (!popup.isConnected) return;
    if (resp?.staleContext) {
      ytdRenderStaleExtensionError(popup);
      return;
    }
    if (!resp?.ok || !resp.result.connected) {
      ytdRenderConnectionError(popup, resp?.result?.reason);
      return;
    }
    if (popup.dataset.activeTab !== "video") return; // user already switched to the Playlist tab
    ytdLoadAndRenderFormats(popup, false);
  });
}

// Switches between the "Video" (single-format list) and "Playlist" (batch
// select + download) tabs, only present when the current page is a playlist
// watch page. Each tab loads its own content independently — switching to
// Playlist doesn't wait on (or re-trigger) the Video tab's own connection
// check, and vice versa.
function ytdSwitchTab(popup, tab) {
  popup.dataset.activeTab = tab;
  popup.querySelectorAll(".ytd-tab").forEach((el) => {
    el.classList.toggle("ytd-tab-active", el.dataset.tab === tab);
  });
  if (tab === "playlist") {
    ytdLoadAndRenderPlaylist(popup, false);
  } else {
    ytdLoadAndRenderFormats(popup, false);
  }
}

function ytdRenderStaleExtensionError(popup) {
  const body = popup.querySelector(".ytd-popup-body");
  body.innerHTML = `
    <div class="ytd-error">Master Download Manager was updated since this page loaded. Refresh the page to reconnect.</div>
    <div class="ytd-retry-row">
      <button type="button" class="ytd-btn ytd-btn-primary" data-action="reload">Refresh Page</button>
    </div>
  `;
  body.querySelector('[data-action="reload"]').addEventListener("click", () => location.reload());
  ytdReposition(popup);
}

function ytdSpinnerHtml(label) {
  return `<div class="ytd-loading"><span class="ytd-spinner"></span>${ytdEscape(label)}</div>`;
}

// The desktop app not running and a wrong/missing security token both fail
// the same connection check, but they have completely different fixes — so
// this reads the actual failure reason (from the local API's own error
// message, e.g. "Missing or invalid X-Master Download Manager-Token header.") rather than
// always showing a generic "not running" message that would be wrong and
// misleading for a token mismatch.
function ytdRenderConnectionError(popup, reason) {
  const isTokenIssue = /token/i.test(reason || "");
  const body = popup.querySelector(".ytd-popup-body");
  body.innerHTML = isTokenIssue ? `
    <div class="ytd-error">Security token doesn't match Master Download Manager. Open this extension's Settings and paste the token shown in the app's Settings → Browser Integration.</div>
    <div class="ytd-retry-row">
      <button type="button" class="ytd-btn ytd-btn-primary" data-action="retry">Retry</button>
      <button type="button" class="ytd-btn" data-action="open-settings">Open Settings</button>
    </div>
  ` : `
    <div class="ytd-error">Master Download Manager not detected. Make sure the Master Download Manager desktop app is running.${reason ? ` (${ytdEscape(reason)})` : ""}</div>
    <div class="ytd-retry-row">
      <button type="button" class="ytd-btn ytd-btn-primary" data-action="retry">Retry</button>
      <button type="button" class="ytd-btn" data-action="open-app">Open Master Download Manager</button>
    </div>
  `;
  body.querySelector('[data-action="retry"]').addEventListener("click", () => ytdOpenFormatPopup());
  const settingsBtn = body.querySelector('[data-action="open-settings"]');
  if (settingsBtn) {
    settingsBtn.addEventListener("click", () => chrome.runtime.sendMessage({ type: "YTD_OPEN_OPTIONS" }));
  }
  const openAppBtn = body.querySelector('[data-action="open-app"]');
  if (openAppBtn) {
    openAppBtn.addEventListener("click", (e) => {
      e.target.textContent = "Open Master Download Manager on your PC";
      e.target.disabled = true;
    });
  }
  ytdReposition(popup);
}

function ytdLoadAndRenderFormats(popup, forceRefresh) {
  const body = popup.querySelector(".ytd-popup-body");
  const url = location.href;

  if (forceRefresh) ytdMediaCache.delete(url);
  const cached = ytdCacheGet(url);
  if (cached) {
    ytdRenderFormatList(popup, url, cached);
    return;
  }

  body.innerHTML = ytdSpinnerHtml("Detecting available formats…");
  ytdReposition(popup);

  ytdSendMessage({ type: "YTD_DETECT", url }).then((resp) => {
    if (!popup.isConnected) return;
    if (popup.dataset.activeTab && popup.dataset.activeTab !== "video") {
      if (resp?.ok) ytdCacheSet(url, resp.result); // keep it for when the user switches back
      return;
    }
    if (resp?.staleContext) {
      ytdRenderStaleExtensionError(popup);
      return;
    }
    if (!resp?.ok) {
      body.innerHTML = `
        <div class="ytd-error-block">
          <div class="ytd-error-title">Unable to detect this video</div>
          <div class="ytd-error-detail">${ytdEscape(resp?.error || "Something prevented Master Download Manager from retrieving the available formats.")}</div>
          <button type="button" class="ytd-btn ytd-btn-primary" data-action="retry">Retry</button>
        </div>
      `;
      body.querySelector('[data-action="retry"]').addEventListener("click", () => ytdLoadAndRenderFormats(popup, true));
      ytdReposition(popup);
      return;
    }
    const media = resp.result;
    ytdCacheSet(url, media);
    ytdRenderFormatList(popup, url, media);
  });
}

function ytdRenderFormatList(popup, url, media) {
  const body = popup.querySelector(".ytd-popup-body");
  // The compact popup lists the exact resolution tiers; "Best/Smallest
  // available" duplicate the top/bottom tier and live in the desktop dialog.
  const videoFormats = media.formats.filter((f) => f.kind === "video" && /^video-\d+$/.test(f.format_id));
  const audioFormats = media.formats.filter((f) => f.kind === "audio");

  if (!videoFormats.length && !audioFormats.length) {
    body.innerHTML = `<div class="ytd-error">This media cannot be downloaded by Master Download Manager.</div>`;
    ytdReposition(popup);
    return;
  }

  body.innerHTML = "";

  // Just the format list — icon + one line of specs per row, click to
  // download immediately. No title/thumbnail header and no extra rows.
  if (audioFormats.length) {
    body.appendChild(ytdBuildAudioSection(popup, url, media, audioFormats));
  }
  if (videoFormats.length) {
    body.appendChild(ytdBuildVideoSection(popup, url, media, videoFormats));
  }
  ytdReposition(popup);
}

function ytdLineRow(iconName, lineHtml, onClick) {
  const row = document.createElement("div");
  row.className = "ytd-row";
  row.innerHTML = `
    <span class="ytd-row-icon ytd-row-icon-${iconName}">${ytdSvgIcon(iconName)}</span>
    <span class="ytd-row-line">${lineHtml}</span>
  `;
  if (onClick) ytdMakeRowClickable(row, onClick);
  return row;
}

function ytdBuildAudioSection(popup, url, media, audioFormats) {
  const wrap = document.createElement("div");

  const mp3s = audioFormats.filter((f) => f.format_id.startsWith("mp3-"));
  const source = audioFormats.find((f) => f.format_id === "source-audio");

  if (mp3s.length) {
    const row = document.createElement("div");
    row.className = "ytd-row ytd-audio-row";
    row.innerHTML = `
      <span class="ytd-row-icon ytd-row-icon-audio">${ytdSvgIcon("audio")}</span>
      <div class="ytd-row-main">
        <span class="ytd-row-line">MP3 Audio</span>
        <div class="ytd-pill-group"></div>
      </div>
    `;
    const pillGroup = row.querySelector(".ytd-pill-group");
    mp3s.forEach((f) => {
      const pill = document.createElement("button");
      pill.type = "button";
      pill.className = "ytd-pill";
      pill.textContent = f.label.replace("MP3 — ", "");
      pill.addEventListener("click", (e) => {
        e.stopPropagation();
        ytdQuickDownload(popup, url, media, f);
      });
      pillGroup.appendChild(pill);
    });
    wrap.appendChild(row);
  }

  if (source) {
    const bits = [ytdEscape(source.label)];
    const sizeText = ytdFormatSize(source);
    if (source.abr_kbps) bits.push(ytdFormatBitrate(source.abr_kbps));
    if (sizeText) bits.push(sizeText);
    if (source.acodec) bits.push(ytdEscape(source.acodec.toUpperCase()));
    wrap.appendChild(ytdLineRow("audio", bits.join(" "), () => ytdQuickDownload(popup, url, media, source)));
  }

  return wrap;
}

function ytdFormatBitrate(kbps) {
  if (!kbps) return "";
  return kbps >= 1000 ? `${(kbps / 1000).toFixed(1)} Mbps` : `${Math.round(kbps)} kbps`;
}

function ytdQualityWord(format) {
  return format.quality_tier ? format.quality_tier[0].toUpperCase() + format.quality_tier.slice(1) : null;
}

function ytdVideoRow(popup, url, media, format) {
  const container = ytdEscape((format.ext || "mp4").toUpperCase());
  const isHD = (format.height || 0) >= 720;

  const bits = [`${ytdEscape(format.label)}${isHD ? '<sup class="ytd-hd-tag">HD</sup>' : ""}`];
  if (format.bitrate_kbps) bits.push(ytdFormatBitrate(format.bitrate_kbps));
  if (format.fps) bits.push(`${Math.round(format.fps)}fps`);
  const sizeText = ytdFormatSize(format);
  if (sizeText) bits.push(ytdEscape(sizeText));
  const codecParts = [];
  if (format.vcodec) codecParts.push(format.vcodec.split(".")[0].toUpperCase());
  if (format.acodec) codecParts.push(format.acodec.toUpperCase());
  if (codecParts.length) bits.push(ytdEscape(codecParts.join("+")));
  const qualityWord = ytdQualityWord(format);
  if (qualityWord) bits.push(`Quality=${ytdEscape(qualityWord)}`);

  return ytdLineRow("video", `${container} ${bits.join(" ")}`, () => ytdQuickDownload(popup, url, media, format));
}

function ytdBuildVideoSection(popup, url, media, videoFormats) {
  const wrap = document.createElement("div");
  const sorted = [...videoFormats].sort((a, b) => (b.height || 0) - (a.height || 0));
  const primary = sorted.slice(0, YTD_VIDEO_ROWS_VISIBLE);
  const rest = sorted.slice(YTD_VIDEO_ROWS_VISIBLE);

  primary.forEach((f) => wrap.appendChild(ytdVideoRow(popup, url, media, f)));

  if (rest.length) {
    const moreLink = ytdLineRow("video", "More qualities", null);
    moreLink.classList.add("ytd-more-link");
    moreLink.querySelector(".ytd-row-line").insertAdjacentHTML("afterend", '<span class="ytd-chevron">›</span>');
    ytdMakeRowClickable(moreLink, () => {
      moreLink.remove();
      rest.forEach((f) => wrap.appendChild(ytdVideoRow(popup, url, media, f)));
      ytdReposition(popup);
    });
    wrap.appendChild(moreLink);
  }

  return wrap;
}

// ---------------------------------------------------------------------
// Playlist tab — lists every video in the playlist (checked by default),
// scrollable, with a single Download/Cancel action row. Downloads use the
// app's own configured default format (Settings → Downloads → Default
// format/quality) rather than a per-video picker — there's no reasonable
// way to show a full format list for dozens of videos at once.
// ---------------------------------------------------------------------
let ytdPlaylistCache = null; // { url, data }

function ytdFormatPlaylistDuration(seconds) {
  if (!seconds && seconds !== 0) return "";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = h > 0 ? String(m).padStart(2, "0") : String(m);
  const ss = String(sec).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function ytdLoadAndRenderPlaylist(popup, forceRefresh) {
  const body = popup.querySelector(".ytd-popup-body");
  const url = location.href;

  if (!forceRefresh && ytdPlaylistCache && ytdPlaylistCache.url === url) {
    ytdRenderPlaylistList(popup, ytdPlaylistCache.data);
    return;
  }

  body.innerHTML = ytdSpinnerHtml("Loading playlist…");
  ytdReposition(popup);

  ytdSendMessage({ type: "YTD_DETECT_PLAYLIST", url }).then((resp) => {
    if (!popup.isConnected) return;
    if (popup.dataset.activeTab !== "playlist") {
      if (resp?.ok) ytdPlaylistCache = { url, data: resp.result };
      return;
    }
    if (resp?.staleContext) {
      ytdRenderStaleExtensionError(popup);
      return;
    }
    if (!resp?.ok) {
      body.innerHTML = `
        <div class="ytd-error-block">
          <div class="ytd-error-title">Couldn't load this playlist</div>
          <div class="ytd-error-detail">${ytdEscape(resp?.error || "Unknown error")}</div>
          <button type="button" class="ytd-btn ytd-btn-primary" data-action="retry">Retry</button>
        </div>
      `;
      body.querySelector('[data-action="retry"]').addEventListener("click", () => ytdLoadAndRenderPlaylist(popup, true));
      ytdReposition(popup);
      return;
    }
    ytdPlaylistCache = { url, data: resp.result };
    ytdRenderPlaylistList(popup, resp.result);
  });
}

function ytdRenderPlaylistList(popup, data) {
  const body = popup.querySelector(".ytd-popup-body");
  body.innerHTML = "";

  const summary = document.createElement("label");
  summary.className = "ytd-playlist-selectall";
  summary.innerHTML = `
    <input type="checkbox" id="ytd-playlist-select-all" checked />
    <span>${ytdEscape(data.playlist_title)} — ${Number(data.entry_count) || 0} video${data.entry_count === 1 ? "" : "s"}</span>
  `;
  body.appendChild(summary);

  const list = document.createElement("div");
  list.className = "ytd-playlist-list";
  data.entries.forEach((entry, i) => {
    const row = document.createElement("label");
    row.className = "ytd-playlist-row";
    const durationText = ytdFormatPlaylistDuration(entry.duration_seconds);
    row.innerHTML = `
      <input type="checkbox" class="ytd-playlist-entry-check" checked data-index="${i}" />
      <span class="ytd-playlist-row-title">${ytdEscape(entry.title)}</span>
      ${durationText ? `<span class="ytd-playlist-row-duration">${ytdEscape(durationText)}</span>` : ""}
    `;
    list.appendChild(row);
  });
  body.appendChild(list);

  const actions = document.createElement("div");
  actions.className = "ytd-playlist-actions";
  actions.innerHTML = `
    <button type="button" class="ytd-btn ytd-btn-primary" data-action="download-playlist">Download this playlist</button>
    <button type="button" class="ytd-btn" data-action="cancel-playlist">Cancel</button>
  `;
  body.appendChild(actions);

  body.querySelector("#ytd-playlist-select-all").addEventListener("change", (e) => {
    body.querySelectorAll(".ytd-playlist-entry-check").forEach((cb) => { cb.checked = e.target.checked; });
  });

  body.querySelector('[data-action="cancel-playlist"]').addEventListener("click", () => ytdClosePopup(popup));

  body.querySelector('[data-action="download-playlist"]').addEventListener("click", async () => {
    const btn = body.querySelector('[data-action="download-playlist"]');
    const checked = [...body.querySelectorAll(".ytd-playlist-entry-check:checked")]
      .map((cb) => data.entries[Number(cb.dataset.index)]);
    if (!checked.length) {
      ytdShowToast("Select at least one video first.", false);
      return;
    }
    btn.disabled = true;
    body.querySelector('[data-action="cancel-playlist"]').disabled = true;
    btn.textContent = `Queuing ${checked.length}…`;
    await ytdQueuePlaylistDownloads(popup, checked);
  });

  ytdReposition(popup);
}

// Playlist downloads use the app's own default format (Settings → Downloads
// → Default format/quality) — mirrors ytd/providers/youtube.py's
// RESOLUTION_LABELS/AUDIO_BITRATES so the computed format_id is one the
// backend actually recognizes.
const YTD_RESOLUTION_HEIGHTS = {
  "360p": 360, "480p (SD)": 480, "720p (HD)": 720,
  "1080p (Full HD)": 1080, "1440p (2K)": 1440, "2160p (4K / Ultra HD)": 2160,
};

async function ytdDefaultPlaylistFormat() {
  const resp = await ytdSendMessage({ type: "YTD_GET_APP_SETTINGS" });
  const settings = resp?.ok ? resp.result : null;
  if (settings?.default_mode === "MP3") {
    const kbps = parseInt(settings?.default_audio_quality, 10) || 192;
    return { formatId: `mp3-${kbps}`, category: "Music" };
  }
  const height = YTD_RESOLUTION_HEIGHTS[settings?.default_quality] || 720;
  return { formatId: `video-${height}`, category: "Video" };
}

async function ytdQueuePlaylistDownloads(popup, entries) {
  const { formatId, category } = await ytdDefaultPlaylistFormat();
  let queued = 0;
  let failed = 0;
  for (const entry of entries) {
    const resp = await ytdSendMessage({
      type: "YTD_CREATE_DOWNLOAD",
      url: entry.url,
      formatId,
      options: { filename: ytdSuggestedFilename(entry.title, { kind: "video" }), saveDir: null, category },
    });
    if (resp?.ok) queued++; else failed++;
  }
  ytdClosePopup(popup);
  if (failed === 0) {
    ytdShowToast(`Queued ${queued} video${queued === 1 ? "" : "s"} from the playlist.`, true);
  } else {
    ytdShowToast(`Queued ${queued}, ${failed} couldn't be queued.`, queued > 0);
  }
}

// ---------------------------------------------------------------------
// Quick download — clicking a format row queues it immediately, with no
// confirmation step. Filename and save folder are the same defaults the
// confirmation dialog used to prefill (suggested title-based filename,
// category-routed folder from the app's own Settings); there's just
// nothing left to review before it happens.
// ---------------------------------------------------------------------
const YTD_CATEGORY_BY_KIND = { video: "Video", audio: "Music", image: "Image", subtitle: "Subtitle" };

async function ytdQuickDownload(formatPopup, url, media, format) {
  ytdClosePopup(formatPopup);
  const category = YTD_CATEGORY_BY_KIND[format.kind] || "Other";
  const filename = ytdSuggestedFilename(media.title, format);

  const resp = await ytdSendMessage({
    type: "YTD_CREATE_DOWNLOAD",
    url,
    formatId: format.format_id,
    options: { filename, saveDir: null, category },
  });
  if (resp?.ok) {
    // No confirmation toast on success — the download just starts silently.
    // Its actual status is visible in Master Download Manager's own queue.
  } else if (resp?.staleContext) {
    ytdShowToast(`Master Download Manager was updated since this page loaded — refresh the page and try again.`, false);
  } else {
    ytdShowToast(`Couldn't queue download: ${resp?.error || "Unknown error"}`, false);
  }
}

function ytdSuggestedFilename(title, format) {
  const base = (title || "download").replace(/[<>:"/\\|?*\x00-\x1f]/g, "").trim();
  return format.kind === "image" ? `${base} [thumbnail]` : base;
}

function ytdFormatSize(format) {
  if (!format.filesize_bytes) return "";
  return `${ytdFormatBytes(format.filesize_bytes)}${format.filesize_is_estimate ? " (approx.)" : ""}`;
}

function ytdFormatBytes(bytes) {
  if (!bytes && bytes !== 0) return "Unknown";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i++;
  }
  return `${value.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

// ---------------------------------------------------------------------
// Toast — free-floating (document.body-hosted, position:fixed) so it can
// be dragged anywhere, same as the format popup, and never traps the user:
// it has its own close button and always auto-dismisses on its own, so it
// never lingers "on top" of whatever they're doing. Being page-embedded
// content, it's only ever visible on this tab in the first place — it
// can't float above other browser tabs or other applications.
// ---------------------------------------------------------------------
function ytdRemoveToast() {
  const existing = document.getElementById(YTD_TOAST_ID);
  existing?._ytdCleanup?.();
  existing?.remove();
}

function ytdCreateToastShell(extraClass, innerHtml) {
  ytdRemoveToast();
  const toast = document.createElement("div");
  toast.id = YTD_TOAST_ID;
  toast.className = `ytd-toast ${extraClass}`;
  toast.innerHTML = innerHtml; // set before measuring, so height reflects real content
  document.body.appendChild(toast);

  // First-ever toast defaults near the button; afterwards it stays wherever
  // it's positioned this page load (not persisted across page loads, unlike
  // the popup — a toast is a short-lived, one-off notice each time).
  const anchor = document.getElementById(YTD_BUTTON_ID);
  const rect = toast.getBoundingClientRect();
  let left, top;
  if (anchor) {
    const anchorRect = anchor.getBoundingClientRect();
    left = anchorRect.right - rect.width;
    top = anchorRect.top - rect.height - 10;
    if (top < 8) top = anchorRect.bottom + 10;
  } else {
    left = window.innerWidth - rect.width - 24;
    top = 96;
  }
  toast.style.left = `${Math.round(Math.max(8, left))}px`;
  toast.style.top = `${Math.round(Math.max(8, top))}px`;

  const dragCleanup = ytdMakeElementDraggable(toast, toast, { excludeSelector: ".ytd-toast-close" });
  toast._ytdCleanup = dragCleanup;
  return toast;
}

function ytdShowToast(message, success = true) {
  const toast = ytdCreateToastShell(success ? "ytd-toast-ok" : "ytd-toast-bad", `
    <div class="ytd-toast-row">
      <span class="ytd-toast-text">${ytdEscape(message)}</span>
      <button type="button" class="ytd-toast-close" aria-label="Dismiss">${ytdSvgIcon("close")}</button>
    </div>
  `);
  const timer = setTimeout(() => ytdRemoveToast(), 5000);
  toast.querySelector(".ytd-toast-close").addEventListener("click", () => {
    clearTimeout(timer);
    ytdRemoveToast();
  });
}

// ---------------------------------------------------------------------
// Small inline icon set (no emoji, so the overlay reads as part of a
// professional app rather than a hobby script).
// ---------------------------------------------------------------------
function ytdSvgIcon(name) {
  const icons = {
    download: '<svg viewBox="0 0 16 16" width="13" height="13" fill="none"><path d="M8 1v9m0 0l-3.5-3.5M8 10l3.5-3.5M2 13h12" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    close: '<svg viewBox="0 0 16 16" width="10" height="10" fill="none"><path d="M3 3l10 10M13 3L3 13" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
    audio: '<svg viewBox="0 0 16 16" width="15" height="15" fill="none"><path d="M6 12a2 2 0 100-4 2 2 0 000 4zm0 0V3l7-1v7M13 9a2 2 0 100-4 2 2 0 000 4z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
    video: '<svg viewBox="0 0 16 16" width="15" height="15" fill="none"><rect x="1.5" y="3.5" width="9" height="9" rx="1.5" stroke="currentColor" stroke-width="1.3"/><path d="M10.5 6.5l4-2v7l-4-2" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
  };
  return `<span class="ytd-icon">${icons[name] || ""}</span>`;
}

function ytdEscape(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}

// ---------------------------------------------------------------------
// SPA navigation hooks. YouTube dispatches `yt-navigate-finish` on both the
// document and the player element after client-side navigation (including
// Shorts and "up next" auto-play); a MutationObserver on <title> is kept as
// a lightweight fallback for cases that event misses, without doing any
// continuous DOM scanning.
// ---------------------------------------------------------------------
document.addEventListener("yt-navigate-finish", ytdEnsureButton);
window.addEventListener("popstate", ytdEnsureButton);

const ytdTitleObserver = new MutationObserver(() => ytdEnsureButton());
const titleEl = document.querySelector("title");
if (titleEl) ytdTitleObserver.observe(titleEl, { childList: true });

ytdEnsureButton();

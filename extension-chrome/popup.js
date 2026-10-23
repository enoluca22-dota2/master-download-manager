const dot = document.getElementById("ytd-conn-dot");
const connText = document.getElementById("ytd-conn-text");
const siteNameEl = document.getElementById("ytd-site-name");
const siteStatusEl = document.getElementById("ytd-site-status");
const toggleBlockBtn = document.getElementById("ytd-toggle-block");
const recentListEl = document.getElementById("ytd-recent-list");
const sendPageBtn = document.getElementById("ytd-send-page");
const sendStatusEl = document.getElementById("ytd-send-status");

document.getElementById("ytd-open-settings").addEventListener("click", () => {
  chrome.runtime.openOptionsPage();
});
document.getElementById("ytd-retry").addEventListener("click", init);

async function getActiveTabUrl() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab?.url || "";
}

function setConnectionUi(connected, reason) {
  dot.className = "ytd-dot " + (connected ? "ok" : "bad");
  connText.textContent = connected ? "Connected to Master Download Manager" : `Not connected — ${reason || "start Master Download Manager"}`;
}

async function renderSiteStatus(url) {
  const host = ytdHostnameOf(url);
  if (!host) {
    siteNameEl.textContent = "No active tab";
    siteStatusEl.textContent = "";
    toggleBlockBtn.hidden = true;
    return;
  }
  siteNameEl.textContent = host;
  const supported = ytdIsSupportedUrl(url);
  const blocked = await ytdIsDomainBlocked(url);

  if (blocked) {
    siteStatusEl.textContent = "Capture is turned off for this site.";
    siteStatusEl.className = "ytd-site-status ytd-status-bad";
    toggleBlockBtn.hidden = false;
    toggleBlockBtn.textContent = "Re-enable capture here";
    toggleBlockBtn.onclick = async () => { await ytdUnblockDomain(host); renderSiteStatus(url); };
  } else if (supported) {
    siteStatusEl.textContent = "Capture is active — look for the Master Download Manager button near the video.";
    siteStatusEl.className = "ytd-site-status ytd-status-ok";
    toggleBlockBtn.hidden = false;
    toggleBlockBtn.textContent = "Don't capture from this site";
    toggleBlockBtn.onclick = async () => { await ytdBlockDomain(url); renderSiteStatus(url); };
  } else {
    siteStatusEl.textContent = "Master Download Manager doesn't capture from this page.";
    siteStatusEl.className = "ytd-site-status";
    toggleBlockBtn.hidden = true;
  }
}

async function loadRecent() {
  try {
    const { downloads } = await ytdListDownloads();
    recentListEl.innerHTML = "";
    downloads.slice(0, 5).forEach((job) => {
      const row = document.createElement("div");
      row.className = "ytd-recent-row";
      const engine = job.engine ? ` · ${job.engine}` : "";
      row.innerHTML = `<span>${escapeHtml(truncate(job.title, 28))}</span><span class="ytd-recent-status">${escapeHtml(job.status)} ${job.status === "Downloading" ? Math.round(job.progress) + "%" : ""}${escapeHtml(engine)}</span>`;
      recentListEl.appendChild(row);
    });
    if (downloads.length === 0) {
      recentListEl.innerHTML = `<div class="ytd-loading">Nothing queued yet.</div>`;
    }
  } catch (err) {
    recentListEl.innerHTML = "";
  }
}

function truncate(text, max) {
  return text && text.length > max ? text.slice(0, max - 1) + "…" : (text || "");
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

// Any page (or magnet/file link opened in the tab) can be handed to the app,
// which chooses the right engine and the user's default quality.
function setupSendPage(url, connected) {
  if (!connected || !ytdIsSendableUrl(url)) {
    sendPageBtn.hidden = true;
    return;
  }
  sendPageBtn.hidden = false;
  sendPageBtn.onclick = async () => {
    sendPageBtn.disabled = true;
    sendStatusEl.textContent = "Sending…";
    sendStatusEl.className = "ytd-site-status";
    try {
      const job = await ytdSendLink(url, null, null);
      sendStatusEl.textContent = `Added (${job.engine}): ${truncate(job.title, 40)}`;
      sendStatusEl.className = "ytd-site-status ytd-status-ok";
      await loadRecent();
    } catch (err) {
      sendStatusEl.textContent = `Couldn't add it: ${err.message}`;
      sendStatusEl.className = "ytd-site-status ytd-status-bad";
    } finally {
      sendPageBtn.disabled = false;
    }
  };
}

async function init() {
  setConnectionUi(false, "checking…");
  const status = await ytdCheckConnection();
  setConnectionUi(status.connected, status.reason);

  const url = await getActiveTabUrl();
  await renderSiteStatus(url);
  setupSendPage(url, status.connected);

  if (status.connected) {
    await loadRecent();
  } else {
    recentListEl.innerHTML = "";
  }
}

init();

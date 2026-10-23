const portInput = document.getElementById("port");
const tokenInput = document.getElementById("token");
const statusEl = document.getElementById("status");

async function loadConnection() {
  const config = await ytdGetConfig();
  portInput.value = config.apiPort;
  tokenInput.value = config.apiToken;
}

document.getElementById("save").addEventListener("click", async () => {
  await ytdSaveConfig({
    apiPort: parseInt(portInput.value, 10) || 47821,
    apiToken: tokenInput.value.trim(),
  });
  statusEl.textContent = "Saved.";
  statusEl.style.color = "#4ed49a";
});

document.getElementById("test").addEventListener("click", async () => {
  await ytdSaveConfig({
    apiPort: parseInt(portInput.value, 10) || 47821,
    apiToken: tokenInput.value.trim(),
  });
  statusEl.textContent = "Testing…";
  statusEl.style.color = "#9aa9c6";
  const result = await ytdCheckConnection();
  if (result.connected) {
    statusEl.textContent = "✓ Connected to Master Download Manager.";
    statusEl.style.color = "#4ed49a";
  } else {
    statusEl.textContent = `✕ ${result.reason || "Could not connect."}`;
    statusEl.style.color = "#e66b7a";
  }
});

// ---------------------------------------------------------------------
// Capture rules — blocked sites only. (Downloads queue immediately when a
// format is clicked, so there's no confirmation dialog left to configure
// auto-download exclusions or remembered folders for.)
// ---------------------------------------------------------------------
const blockedListEl = document.getElementById("blocked-list");
const blockDomainInput = document.getElementById("block-domain-input");

async function renderCaptureRules() {
  const rules = await ytdGetCaptureRules();

  blockedListEl.innerHTML = "";
  if (rules.blockedDomains.length === 0) {
    blockedListEl.innerHTML = `<div class="ytd-empty">No blocked sites.</div>`;
  } else {
    rules.blockedDomains.forEach((domain) => blockedListEl.appendChild(ruleListItem(domain, async () => {
      await ytdUnblockDomain(domain);
      renderCaptureRules();
    })));
  }
}

function ruleListItem(label, onRemove) {
  const li = document.createElement("li");
  const span = document.createElement("span");
  span.textContent = label;
  const btn = document.createElement("button");
  btn.type = "button";
  btn.textContent = "Remove";
  btn.addEventListener("click", onRemove);
  li.appendChild(span);
  li.appendChild(btn);
  return li;
}

document.getElementById("block-domain-add").addEventListener("click", async () => {
  const raw = blockDomainInput.value.trim().toLowerCase();
  if (!raw) return;
  const domain = raw.replace(/^https?:\/\//, "").replace(/^www\./, "").split("/")[0];
  const rules = await ytdGetCaptureRules();
  if (!rules.blockedDomains.includes(domain)) {
    await ytdSaveCaptureRules({ blockedDomains: [...rules.blockedDomains, domain] });
  }
  blockDomainInput.value = "";
  renderCaptureRules();
});

loadConnection();
renderCaptureRules();

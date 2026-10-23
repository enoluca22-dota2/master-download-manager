# Browser integration

The MDM browser extension sends links from Chrome, Edge, Brave or Firefox to
the running app. It talks only to MDM on your own PC (`127.0.0.1`) and proves
itself with a security token. It never sends cookies or browsing history
anywhere.

## Install the extension

The extension folders are in the program's `extensions\` folder (installed by
the *Browser Integration* component of Setup). They are also attached to every
release as `MasterDownloadManager-extension-chrome.zip` and
`MasterDownloadManager-extension-firefox.zip`.

**Chrome / Edge / Brave**

1. Open `chrome://extensions` (Edge: `edge://extensions`).
2. Turn on **Developer mode**.
3. Click **Load unpacked** and choose `extensions\chrome` in the MDM
   installation folder (or the unzipped release zip).

**Firefox**

1. Open `about:debugging#/runtime/this-firefox`.
2. Click **Load Temporary Add-on…** and choose `extensions\firefox\manifest.json`.
   Firefox removes temporary add-ons when it closes; load it again after a
   restart.

The extension isn't published in the Chrome Web Store or on addons.mozilla.org.

## Pair it with the app

1. In MDM: **Tools → Browser Integration…** (Settings → Browser Integration)
   and copy the **security token**.
2. Open the extension's options page (right-click its icon → *Options*), paste
   the token and click **Save Connection**. It tests the connection and shows
   "✓ Connected to Master Download Manager" when MDM answers.

MDM must be running; the extension can't start it.

## What it does

* **YouTube** — a download button in the player with the formats YouTube
  really offers, and a playlist tab.
* **Any site** — right-click a link, a page, a video/audio element or a
  **selection of links** → *Download with Master Download Manager*.
* **Capture browser downloads** (optional, off by default) — normal browser
  downloads of the file types you choose, above a minimum size and not on
  excluded sites, are handed to MDM. If MDM isn't running or can't fetch the
  file on its own (for example a download that needs your login cookies),
  the browser keeps the download.

## Updating the extension

After an MDM update, reload the extension (`chrome://extensions` → the reload
icon) so it uses the files from the new version. Its version number matches
the app's.

Problems? See [TROUBLESHOOTING.md](TROUBLESHOOTING.md#browser-extension).

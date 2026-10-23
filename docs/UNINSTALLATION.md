# Uninstalling Master Download Manager

1. Close MDM (right-click the tray icon → *Exit*). The uninstaller asks you to
   if it's still running.
2. Open *Settings → Apps → Installed apps*, find **Master Download Manager**,
   click **⋯ → Uninstall**. (Or use *Control Panel → Programs and Features*.)
3. At the end the uninstaller asks:

   > Do you also want to remove your Master Download Manager settings, download
   > history, queues and logs?

   **No** (the default) keeps them, so a later install picks up where you left
   off. **Yes** removes them.

## What is removed

* The program folder (`%LOCALAPPDATA%\Programs\Master Download Manager` by
  default), including the bundled docs, licenses and browser-extension files.
* The Start Menu and Desktop shortcuts, and the *Start with Windows* shortcut.
* The Installed Apps entry.
* The `.torrent` "Open with" entry and the `magnet:` registration — only if
  they still point to this installation. A previous `magnet:` handler is
  restored.

## What is never removed

* **Your downloaded files**, wherever they are.
* Your settings, history, queues and logs in `%LOCALAPPDATA%\MasterDownloadManager`,
  unless you answer **Yes**. Even then only the files MDM creates there are
  deleted (settings, queues, scheduler, history database, logs, cache and
  update backups); anything else you put in that folder stays.
* The browser extension inside your browser — remove it from
  `chrome://extensions` or `about:addons` yourself.

## Silent uninstall

```
"%LOCALAPPDATA%\Programs\Master Download Manager\unins000.exe" /VERYSILENT
```

A silent uninstall never asks and always keeps your data.

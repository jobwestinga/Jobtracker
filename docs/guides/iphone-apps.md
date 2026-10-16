# Install Weight and JobTracker on iPhone

Both apps use your existing private HTTPS server and history. The Mac app keeps
all areas. Focus modes and native Shortcuts remain independent; the apps have
no integration with or dependency on them.

## Install the two apps

Use your **existing server address**, retaining its HTTPS port. The placeholders
below are not public services. This deployment supports the origin root only;
reverse-proxy subpath mounting is not supported.

| App | Safari address | Fresh launch | Icon |
| --- | --- | --- | --- |
| Weight | `https://YOUR-SERVER:PORT/phone/weight/` | Weight tiles | Cut-out W |
| JobTracker | `https://YOUR-SERVER:PORT/phone/jobtracker/` | Subjects and active timer | Cut-out J |
| Installation guide | `https://YOUR-SERVER:PORT/phone/` | Instructions and both links | Do not install this guide |
| Complete browser app | `https://YOUR-SERVER:PORT/` | All five sections | Use in a browser |

1. Connect Tailscale as usual. In **Safari**, open the Weight address above.
2. Tap **Share → Add to Home Screen**. If missing, scroll down to **Edit Actions**
   and add it. Enable **Open as Web App** if offered, name it **Weight**, then
   tap **Add**.
3. Repeat from the **JobTracker** address, naming it **JobTracker**. Install from
   each app's page, not from the guide.
4. Launch each Home Screen icon. If asked, paste that installation's device
   token and tap **Connect**. Separate sign-in is expected: iOS may isolate app
   storage. Credentials are never transferred automatically between apps.
5. Tap **Refresh** in each app. Check **Synced** with no changes waiting, and
   compare history with your Mac. Tap the sync status for details and retry.

Safari links do not reliably launch the corresponding installed app. Use the
intended Home Screen icon. Resizing an explicit app route keeps the same product;
the complete browser interface remains at `/`. Weight owns the root identity, so
a Home Screen app added from `/` opens Weight.

A server backup does **not** contain unsynchronized phone changes or unfinished
forms. Unsaved forms survive normal background/foreground transitions while iOS
retains the page; OS termination may discard unsaved fields. Press Save to make
an entry durable before closing the app.

## Reinstall an app

Removing a Home Screen web app deletes that app's saved token, preferences,
unfinished forms and **changes still waiting to send**. Server history and a
running timer are unaffected.

1. Connect Tailscale. Open the app, tap **Refresh** and open the sync status:
   continue only when it says **Synced** with no waiting changes. Otherwise keep
   it open online until the changes send.
2. Check that your Mac shows the latest weighing and sessions.
3. Touch and hold the icon, choose **Remove App** or **Delete Bookmark**, and
   confirm.
4. In Safari, open the app's address and pull down once to reload it. Install it
   as described above and connect it with its token. A token remains valid for
   the reinstalled app.

iOS stores an app's icon, name and status-bar style when it is added, so new
artwork or shell metadata appears only after re-adding the app. In the Dark Home
Screen style, iOS may restyle web-app icons with a neutral background and glass
shading. If one icon looks flat beside the other, switch the Home Screen style to
Light and back to Dark so that iOS redraws them.

## Tokens and connection recovery

Tokens are shown once when issued. The server stores only hashes; it cannot show
an existing token again. Each installation has its own device token for the same
history: these are not separate user accounts.

Administrators use the existing CLI on the server with its configured token
store. List device **names** first, then issue unused names for new devices and
revoke ones no longer installed:

```sh
python -m server.tokens_cli list
python -m server.tokens_cli add iphone-weight-NEW-UNIQUE-NAME
python -m server.tokens_cli revoke OLD-DEVICE-NAME
```

**Adding an existing device name replaces its token.** Revoke a token only after
its installation has no waiting changes. Save new tokens privately and paste
them only into the app's password field, never links, manifests, QR codes,
screenshots, tracked files or shared logs.

If the network/server is unavailable, previously cached apps still open; Save
records changes locally with a waiting status. Keep Tailscale connected, reopen
each app and Refresh when connectivity returns. Background execution and instant
updates in suspended apps are not guaranteed. Other clients see changes after
their next successful synchronization.

A rejected token shows reconnection setup and retains its queue. Reconnect the
**original** credential for that queue. If revoked, ask the administrator to
restore that credential's access first. Do not discard or reassign queued
operations to work around a rejection.

## Exports and rollback

Weight's **Export data** CSV contains weight readings; **Save plot** produces the
chart PNG. Neither is a full backup. Full backups remain in the Mac app Settings
with their existing coverage.

If an installation fails, keep its data and use the complete Safari app while
diagnosing it. Open each installation online to drain its own queue. A code
rollback restores previous server/web source and bumps the service-worker version
again; never restore an old database over live history.

## Checks that need an iPhone

Browser automation verifies the two interfaces, stable identities, offline
shells, queue isolation, active timers, retries, authentication and normal draft
retention. Physical checks remain: Safari's installation sheet, two named icons,
OS-managed storage isolation, suspension/resume and offline cold launch after
successful setup. Chromium emulation does not prove these.

Apple's [Safari installation guide](https://support.apple.com/guide/iphone/iphea86e5236/ios)
describes Add to Home Screen and Open as Web App; WebKit documents
[manifest identity support](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/).
App routing and storage contracts are in the [phone reference](../reference/mobile.md).

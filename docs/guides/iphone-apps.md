# Install Weight and JobTracker on iPhone

Both apps use your existing private HTTPS server and history. The Mac app keeps
all areas. Focus modes and native Shortcuts remain independent; this change adds
no integration or dependency on them.

## Install the two apps

Use your **existing server address**, retaining its HTTPS port. The placeholders
below are not public services. This deployment supports the origin root only;
reverse-proxy subpath mounting is not supported.

| App | Safari address | Fresh launch | Icon |
| --- | --- | --- | --- |
| Weight | `https://YOUR-SERVER:PORT/phone/weight/` | Weight tiles | Dark Cutout W |
| JobTracker | `https://YOUR-SERVER:PORT/phone/jobtracker/` | Subjects and active timer | Your Cutout J |
| Installation guide | `https://YOUR-SERVER:PORT/phone/` | Instructions and both links | Do not install this guide |
| Complete browser app | `https://YOUR-SERVER:PORT/` | All five sections | Use in a browser |

1. Connect Tailscale as usual. In **Safari**, open the Weight address above.
2. Tap **Share → Add to Home Screen**. If missing, scroll down to **Edit Actions**
   and add it. Enable **Open as Web App** if offered, name it **Weight**, then
   tap **Add**.
3. Repeat from the **JobTracker** address, naming it **JobTracker**. Install from
   each app's page, rather than from the guide or an old icon gallery page.
4. Launch each Home Screen icon. If asked, paste that installation's device
   token and tap **Connect**. Separate sign-in is expected: iOS may isolate app
   storage. Credentials are never transferred automatically between apps.
5. Tap **Refresh** in each app. Check **Synced** with no changes waiting, and
   compare history with your Mac. Tap the sync status for details and retry.

Safari links do not reliably launch the corresponding installed app. Use the
intended Home Screen icon. Resizing an explicit app route keeps the same product;
the complete browser interface remains at `/`.

## Keep the existing J installation safe

**Keep J and its website data.** Weight retains manifest identity `/` and every
existing storage key, token and queue. An installed app still launching `/`,
`/index.html` or a legacy icon page redirects to `/phone/weight/`; ordinary
Safari `/` remains complete. Weight sends old queued time operations in their
original order. There is no database migration or credential revocation.

1. Open J online and let its waiting changes finish sending. Open its gear button
   and use **Reload app** to fetch the updated shell. Never clear website data.
2. Verify that J shows Weight without work tabs. It remains your working Weight
   installation, even if its icon/name is still J.
3. Add the new JobTracker app using its explicit route and its own token. Verify
   Subjects, the active timer and history. Reopening never starts a timer.
4. For the new Weight name/icon, use Safari's Weight installation steps **after**
   J has no waiting changes. iOS may reuse its identity or create another item;
   automatic name/icon replacement is not guaranteed. Keep J until the replacement
   is connected, synchronized and shows the right history. Two verified working
   apps are the final arrangement; a third legacy icon is not required.

A server backup does **not** contain unsynchronized phone changes or unfinished
forms. Do not delete an installation, revoke its token or clear its storage while
changes are waiting. Unsaved forms survive normal background/foreground
transitions while iOS retains the page; OS termination may discard unsaved fields.
Press Save to make an entry durable before closing the app.

## Tokens and connection recovery

Tokens are shown once when issued. The server stores only hashes; it cannot
show the old iPhone token. J can keep using its saved token. New installations
can use new device tokens against the same history: these are not separate user
accounts.

Administrators use the existing CLI on the server with its configured token
store. List device **names** first, then issue unused names for the new apps:

```sh
python -m server.tokens_cli list
python -m server.tokens_cli add iphone-weight-NEW-UNIQUE-NAME
python -m server.tokens_cli add iphone-jobtracker-NEW-UNIQUE-NAME
```

**Adding an existing device name replaces its token.** Keep the old iPhone
credential valid during rollout. Save new tokens privately and paste them only
into the app's password field, never links, manifests, QR codes, screenshots,
tracked files or shared logs.

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

If a new installation fails, keep its data and use J or the complete Safari app
while diagnosing it. Open each installation online to drain its own queue. A code
rollback restores previous server/web source and bumps the service-worker version
again; never restore an old database over live history. Legacy API operations and
storage remain compatible. The new JobTracker queue has separate keys: keep that
app until it has sent successfully, because older source may not expose its queue.
Database and Mac app rollback are not needed for this interface split.

## Checks that need an iPhone

Browser automation verifies the two interfaces, stable identities, offline
shells, queue isolation, active timers, retries, authentication and normal draft
retention. Physical checks remain: Safari's installation sheet, two named icons,
OS-managed storage isolation, old-icon updates, suspension/resume and offline
cold launch after successful setup. Chromium emulation does not prove these.

Apple's [Safari installation guide](https://support.apple.com/guide/iphone/iphea86e5236/ios)
describes Add to Home Screen and Open as Web App; WebKit documents
[manifest identity support](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/).
App routing and storage contracts are in the [phone reference](../reference/mobile.md).

# Connecting the AM mobile app via BlueStacks (traffic capture)

Goal: run **Airlines Manager 2** mobile inside **BlueStacks Air** and capture its
HTTPS API traffic through **mitmproxy**, so we get clean JSON instead of scraping
the browser HTML (`scrape_demand.js`).

## What the recon found (already verified on this machine)

| Thing | Value |
|---|---|
| BlueStacks | BlueStacks **Air** (now.gg), Apple Silicon (arm64) |
| Emulator | Android **13** (SDK 33), ABI arm64-v8a, model spoofed as SM-G998B |
| ADB | reachable at **`127.0.0.1:5555`** (`bst.enable_adb_access=1`) |
| Game pkg | **`com.Playrion.AirlinesManager2`** v4.00.0604 |
| Engine | **Unity / il2cpp** (`assets/bin/Data/data.unity3d`, `global-metadata.dat`) |
| Cert pinning | **None found** — only stock Unity `CertificateHandler` strings, no `sha256/` pins |
| network_security_config | **Absent** from the manifest → app trusts the **SYSTEM** CA store only, *not* user certs |
| Mobile API hosts | `tycoon-ppd.airlines-manager.com`, `auth.airlines-manager.com`, `centralsaws.airlines-manager.com/api_login_player.php`, `centrals.airlines-manager.com/check_app_p.php` |
| Root | `adb root` reports success but shell stays uid 2000; **no `su`** present. `bst...enable_root_access=1` is set but the in-app Root toggle must actually be on. |
| mitm CA | exists at `~/.mitmproxy/mitmproxy-ca-cert.pem`, system filename **`c8750f0d.0`** |
| Stale config | device already had `http_proxy=192.168.1.92:8080` (old host IP; current host is `10.3.254.142` on en0) |

**Key consequence:** because there's no `network_security_config`, dropping the
mitm cert in as a *user* certificate will NOT work — the app ignores user CAs.
You need it in the **system** store (needs root) **or** you repackage the APK to
trust user certs (no root). Both routes are below.

---

## Route 1 — System CA via BlueStacks root (preferred, no APK edits)

1. **Start the proxy** (terminal 1, leave running):
   ```bash
   mitmweb --listen-host 0.0.0.0 -p 8080
   ```
   Web UI at http://127.0.0.1:8081.

2. **Enable root in BlueStacks** (one time): BlueStacks Air → **Settings → Advanced
   → Root access ON** → restart the instance. (This is the step that gives `su`.)

3. **Run the helper** (terminal 2):
   ```bash
   ./tools/bluestacks_mitm_setup.sh
   ```
   It connects adb, pushes the CA, installs it into the system store via a tmpfs
   overlay on `/system/etc/security/cacerts`, sets the device proxy to your host
   IP, and restarts the game.

4. **Capture**: in mitmweb filter `~d airlines-manager.com`, then play the app —
   audits/demand/circuit calls appear as JSON.

5. **Cleanup** when done:
   ```bash
   adb -s 127.0.0.1:5555 shell settings put global http_proxy :0
   ```

> Why tmpfs: `/system` is mounted read-only and is **not** remountable here
> (`/dev/vda1 not user mountable`), so you overlay the cacerts dir with a tmpfs
> copy that includes the mitm cert. It lasts until the next reboot.

---

## Route 2 — Repackage the APK, embed the mitm cert (CONFIRMED WORKING)

**This is the route that worked** (2026-07-25). BlueStacks Air gave no usable
root, so the system-store install (Route 1) was impossible. Instead we patched
the base APK to trust an *embedded* copy of the mitm CA — no root, and no manual
user-cert install / device-PIN needed. The Playrion `check_app_p.php` check did
**not** reject the resigned build; the app booted to its normal login screen.

Exact recipe used (toolchain: `brew install apktool` + Android
`build-tools;34.0.0` via `sdkmanager`, JDK at
`/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home`):

```bash
# 1. pull all 3 splits (base + config.arm64_v8a + vending)
adb -s 127.0.0.1:5555 shell pm path com.Playrion.AirlinesManager2   # list them
adb -s 127.0.0.1:5555 pull <each> .

# 2. decode base only, resources+manifest (-s keeps dex raw = fast rebuild)
apktool d -s -f base.apk -o base_dec

# 3. drop the mitm cert in as a raw resource
cp ~/.mitmproxy/mitmproxy-ca-cert.pem base_dec/res/raw/mitmproxy_ca.pem

# 4. add base_dec/res/xml/network_security_config.xml  (see below)
# 5. add to <application> in AndroidManifest.xml:
#      android:networkSecurityConfig="@xml/network_security_config"

# 6. rebuild + sign ALL three splits with ONE fresh key
apktool b base_dec -o base_patched.apk
keytool -genkeypair -keystore am.keystore -alias am -keyalg RSA -keysize 2048 \
        -validity 10000 -storepass android -keypass android -dname "CN=am-mitm"
for a in base_patched config vending; do
  zipalign -f -p 4 $a.apk ${a}_alx.apk
  apksigner sign --ks am.keystore --ks-key-alias am --ks-pass pass:android \
            --key-pass pass:android --out ${a}_signed.apk ${a}_alx.apk
done

# 7. replace the installed app (signature differs -> must uninstall first;
#    game state is server-side, so you just re-login)
adb -s 127.0.0.1:5555 uninstall com.Playrion.AirlinesManager2
adb -s 127.0.0.1:5555 install-multiple -r \
    base_patched_signed.apk config_signed.apk vending_signed.apk

# 8. proxy + launch
adb -s 127.0.0.1:5555 shell settings put global http_proxy <HOST_IP>:8080
adb -s 127.0.0.1:5555 shell monkey -p com.Playrion.AirlinesManager2 \
    -c android.intent.category.LAUNCHER 1
```

network_security_config.xml (embeds the cert, so no user-cert install):
```xml
<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
  <base-config cleartextTrafficPermitted="true">
    <trust-anchors>
      <certificates src="system"/>
      <certificates src="user"/>
      <certificates src="@raw/mitmproxy_ca"/>
    </trust-anchors>
  </base-config>
</network-security-config>
```

**Verify it worked:** `cat /proc/<app-pid>/net/tcp` and look at connections to
the proxy (`rem_address` ...:1F90 = :8080). State `01` (ESTABLISHED) = TLS MITM
succeeding. State `06` (TIME_WAIT), all connections dying instantly = cert not
trusted (the pre-patch symptom).

Signed APKs are kept at `scratchpad/apk/*_signed.apk` — reinstall anytime the
BlueStacks image resets the app.

---

## Route 3 — system CA via root (only if a rooted engine is available)

Original plan; blocked here because BlueStacks Air exposes no working `su`.

```bash
brew install apktool                     # not currently installed
APK=/private/tmp/.../scratchpad/apk/base.apk   # already pulled during recon
apktool d "$APK" -o am_decoded
```

Add `am_decoded/res/xml/network_security_config.xml`:
```xml
<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
  <base-config cleartextTrafficPermitted="true">
    <trust-anchors>
      <certificates src="system"/>
      <certificates src="user"/>
    </trust-anchors>
  </base-config>
</network-security-config>
```
Reference it in `AndroidManifest.xml` `<application ...>`:
`android:networkSecurityConfig="@xml/network_security_config"`.

Then rebuild, sign, and reinstall (the app is a split APK, so pull & reinstall
all splits together, or use `apktool b` on base + `apksigner`):
```bash
apktool b am_decoded -o am_patched.apk
apksigner sign --ks debug.keystore am_patched.apk
adb -s 127.0.0.1:5555 install -r am_patched.apk
```
Now the mitm cert works installed as a **user** cert (Settings → Security →
Install a certificate → CA) and you set the proxy the same way as Route 1.

> Playrion may run an integrity/`check_app_p.php` check; if a repackaged build is
> rejected at login, prefer Route 1 (root + unmodified APK).

---

## Faster alternative (no MITM at all)

The mobile API talks to the same backend as the website. If capturing is only to
feed the SQLite pipeline, note the browser route (`scrape_demand.js` +
`import_to_sqlite.py`) already yields the demand/price JSON without any of this.
Use the mobile-capture route when you specifically want the **Tycoon** mobile
endpoints (`tycoon-ppd.airlines-manager.com`) that the website doesn't expose.

---

## What I could not run for you (needs your go-ahead)

The auto-mode permission classifier blocked the interception steps as a group:
- launching `mitmdump`/`mitmweb` (the proxy itself),
- copying the mitm CA into the working dir,
- force-stopping / relaunching BlueStacks.

Nothing about the environment blocks them — approve those commands (or run the
two blocks above yourself) and the flow completes. ADB access, the APK pull, and
all read-only recon already succeeded.

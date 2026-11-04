# Runtime + device session handoff

Everything worked on in this session: the IL2CPP runtime hardening, the detection rebinder, the Quago
(Pokémon GO) work, the Survivor login investigation, the signing/injection pipeline, and the device
workflow. Read this top to bottom before continuing.

Repo: `C:\Users\BenwillLopezNavarro\GitHub\mods` (branch `main`, pushed to GitHub).
Device: iPhone, not jailbroken, developer tunnel via `pymobiledevice3`. Artifacts live under
`C:\AccretionMods` (`builds`, `work`).

---

## 1. Commits from this session

| commit | content |
|---|---|
| `b74eb1b` | Hardened `runtime/AMDetectBypass.c`: bounded chained binds, validated names/slots, `bypass:` logs; kept out of mods |
| `8f157d3` | Parked the hardened rebinder and the Quago selector patch until a device test passes |
| `7f21e05` | Data-segment guard for the rebinder (`__DATA*` only); Quago selector enumeration for Pokémon GO |
| `fdae638` | Match double-underscore imports (`__dyld_get_image_name`) in `am_rebind_symbol` |
| `6b19a74` | Log rebinder install and each non-system image it visits |
| `628f86d` | Checked slot writes via `vm_region_64`/`vm_write`; bound classic tables; Quago class probe |

Earlier runtime/table-hook work was already committed before this session (`5cf2f72` etc.).

---

## 2. Runtime architecture (`runtime/`)

| file | role |
|---|---|
| `AMIl2cpp.m/.h` | Unity IL2CPP bootstrap, pointer hooks (default), optional code hooks (`AMIl2cppSetCodeHooks`), table-hook auto-detect, `AMIl2cppRegister`, log channel |
| `AMTableHook.h/.c` | Data-table method-pointer hooks: `AMTableHookInstall(hooks, count, registrationRva, modulesOffset)`; `Il2CppCodeRegistration`→`codeGenModules`→`methodPointers[index]` patched before IL2CPP init (no code page, no JIT) |
| `AMDetectBypass.c` | The detection rebinder (see §5) |
| `AMQuagoBypass.m` | Pokémon-only Quago class enumeration/neutralisation (see §6) |
| `AMSurvivorLogin.m` | Survivor-only direct-login probe (see §7); **not** in the Survivor Makefile any more |
| `SideloadCompat.m` | Re-signed-app compatibility: iCloud/GameCenter/keychain/age hooks, import bindings |
| `am_il2cpp.h/.c` | Symbol/API resolution |
| `AMMachSymbols.h/.c` | `LC_SYMTAB`-based symbol fallback when `dlsym` misses (hidden exports) |
| `AMIl2cppData.h/.m` | Class/field/call helpers: `AMIl2cppFindObject`, `AMIl2cppField`, `AMIl2cppCall`, `AMIl2cppClass`, `AMIl2cppStaticObject`, … |
| `AMImportBindings.c` | The shared import/pointer walker used by SideloadCompat |
| `AMLivePins.h`, `AMAttrPins.h` | Live field pins |

Key facts:

- Pointer hooks are the default because a non-JIT phone kills any patched code page
  (`CODESIGNING / "Invalid Page"`). `AMIl2cppSetCodeHooks(YES)` is opt-in and only works with a JIT
  (Apple ID/plumesign with `get-task-allow`, and even then the ElleKit `MSHookFunction` page-protection
  bug applies; `EKJITLessHook` needs `pacibsp`).
- `AMTableHook.c` must be listed in every mod's `Makefile` (`runtime/AMTableHook.c`) or the layout is
  wrong for mods with `hooks.json`; `scripts/validate_mods.py` enforces this.
- `AMIl2cpp.m` currently **enables** `#include "AMDetectBypass.c"` (line ~13). That means any mod built
  now ships the rebinder. If you do not want that, comment the include out again and rebuild.

---

## 3. Build and test workflow

### Build a mod

```powershell
cd C:\Users\BenwillLopezNavarro\GitHub\mods
python scripts/dev.py build <mod>            # dylib only -> C:\AccretionMods\builds\<Dylib>-<ver>.dylib
python scripts/dev.py build <mod> --ipa      # dylib + inject into the base IPA -> ..._accretion.ipa
python scripts/dev.py build <mod> --ipa --no-check   # skip pin warnings
```

- Theos runs inside WSL (`wsl.exe bash -lc 'cd ... && THEOS=$HOME/theos make FINALPACKAGE=1'`).
- `scripts/build.sh` copies `runtime/` and `menu/` into the mod, generates `AMTweaks.gen.m` from
  `tweaks.json`, embeds `hooks.json` as `runtime/am_hooks_data.c`, and passes
  `AM_DUMP_DIR`/`--dump/--binary/--registration` to `scripts/embed_hooks.py` when set.
- `scripts/table_hooks.py` parses `dump.cs` (`parse_dump`, `parse_fields`, `load_modules`, `rva_index`,
  `generate`, `header`, `header_with_pins`).
- Tests: `pytest tests/` (see `tests/test_table_hooks.py`).
- `scripts/validate_mods.py` requires `runtime/AMTableHook.c` in mods with `hooks.json` and validates pins.

### Sign and install

```powershell
python scripts/dev.py sign <ipa> --install -o <out.ipa>          # certificate (zsign)
python scripts/dev.py sign <ipa> --apple-id --install -o <out.ipa>  # Apple ID (plumesign)
```

- **zsign** (`~/.accretion-signing`) re-signs nested code recursively and keeps the app's own bundle ID.
- **plumesign `--apple-id`** appends the team ID (`.2XN3V474V3` here) and adds `get-task-allow`.
- `dev.py sign` refuses when the bundle ID is already installed; uninstall first.

---

## 4. Device workflow (`pymobiledevice3`)

`PMD = "$env:USERPROFILE\.local\bin\pymobiledevice3.exe"`.

```powershell
& $PMD apps list
& $PMD apps install <ipa>
& $PMD apps uninstall <bundle-id>
& $PMD developer dvt proclist
& $PMD developer dvt launch <bundle-id>
& $PMD developer dvt launch --kill-existing <bundle-id>
& $PMD developer dvt screenshot <png>
& $PMD crash pull <dir>
& $PMD syslog live -o <txt>        # -e <process> filters
```

Pitfalls learned the hard way:

- **Lock screen:** `dvt launch` reports a pid but the app never starts while the phone is locked
  (look for `CoverSheetKit`/`variant=lock` in the log). Unlock and keep the screen awake.
  Auto-Lock off is strongly recommended during test loops.
- `dvt kill` usually does **not** terminate an app. `--kill-existing` often just foregrounds the running
  process, so a fresh process requires **uninstall + reinstall**.
- Init-time logs (`bypass: install`, `bait ... loaded`) are only emitted once at process start; a capture
  that starts after launch misses them. Start the capture first, then launch.
- App process names: `PokmonGO` (é stripped), `SurvivorAce`, `IntotheDead2`, `Bleach`,
  `FalloutShelter`. The `{dylib}` part of the os_log tag names the dylib that logged.
- Background capture: `Start-Process $PMD -ArgumentList @('syslog','live','-o',$log) -NoNewWindow`.

---

## 5. Detection rebinder (`runtime/AMDetectBypass.c`)

Purpose: hide substrate-like images and jailbreak artifacts from a game's own checks (PAG crash SDK
scans loaded images for Substrate/MobileSubstrate/TweakInject/CydiaSubstrate/libsubstrate; Unity Ads
checks `/private/jailbreak.txt`).

Two rebind paths:

- **Classic** (`LC_DYLD_INFO`): rewrite `__got`/`__la_symbol_ptr` via the indirect symbol table.
- **Chained** (`LC_DYLD_CHAINED_FIXUPS`): walk `dyld_chained_starts_in_image` and rewrite matching binds.

Replaced symbols: `dyld_get_image_name`, `dlopen`, `dlsym`, `stat`, `access`, `fopen`.

### What was fixed in this session

1. Chained import names: validate `imports_offset`, `symbols_offset`, item size, `strnlen` bounded to the
   payload.
2. Chain slots: validate against `segment_base … segment_base + vmsize`; `am_slot_readable` before reads.
3. Classic: bound `symtab`/`strtab`/`indirectsymtab` inside `__LINKEDIT` (`filesize`), range-check each
   section (`am_range_readable`, one `vm_region` per page instead of per slot).
4. Data-segment guard: chains are skipped unless the segment is `__DATA`, `__DATA_CONST` or
   `__DATA_DIRTY`.
5. Symbol matching: strip **all** leading underscores. The bait imports `__dyld_get_image_name` (double
   underscore); the old `if (name[0]=='_') name++` compared `_dyld_get_image_name` and never matched.
   This was the direct cause of `bait substrate images visible=1`.
6. Writes: `am_write_slot` = `vm_region_64` readability check → `vm_protect` →
   `vm_write` (kernel copy) instead of a raw store; refusals log
   `bypass: write refused (unmapped slot|protect|vm_write)`.
7. Markers: `bypass: install images=N` at the constructor; `bypass: image <path>` for every non-system
   image; `bypass: chained binds=N rewrites=N`; `bypass: classic rewrites=N`.

### Observed behaviour (important)

| game | result |
|---|---|
| Into the Dead 2 | `bypass: chained image` repeated → walk runs; later `SIGBUS KERN_PROTECTION_FAILURE` on a **write** (`0x21373c000`), before the guard/safe-write |
| Bleach | `bypass: chained image`, then the app's own `exit()` (UnityFramework/Bleach frames) — its anti-tamper, not our code |
| Survivor: Ace | `bait loaded`; `bait substrate images visible=1` with the pre-fix build; later builds crashed at `0x202f39d9c` (unaligned → a **string/read** fault in name resolution), and one safe-write build reached the login UI |

The bait test is `C:\AccretionMods\work\survivor-probe\bait\libsubstrate.dylib` (benign
`substrate.dylib`-named dylib; logs `libsubstrate bait loaded`, then at +3 s `bait sees`,
`bait substrate images visible=N`, `bait stat/access jailbreak=…`). Inject it with
`C:\Users\BenwillLopezNavarro\AppData\Local\Temp\inject_probe.py <in.ipa> <out.ipa> <dylib>`.

### Open gaps for the rebinder

- **`visible=0` has never been observed end-to-end.** The double-underscore fix was committed after the
  last `visible=1` capture; the fixed build never produced a clean init capture.
- **Chained name read fault:** one Survivor crash was at an **unaligned** address
  (`…d9c`), i.e. a string/read fault, not a slot write. Next step: require the resolved import-name
  pointer to be inside a `vm_region_64`-checked mapped page (and not just inside the payload range), or
  implement **classic-only safe mode** (skip the chained import-name walk entirely) to isolate it.
- Per-slot `vm_region_64` in the chained path is a syscall per slot; cache per page if startup slows.
- `done` flags in the constructors are not atomic (minor).
- The include is **enabled** in `AMIl2cpp.m`; disable until `visible=0` is proven if you ship.

---

## 6. Quago (Pokémon GO) — `runtime/AMQuagoBypass.m`

Findings from the Pokémon 0.435.1 `UnityFramework`:

- Quago is statically linked ObjC. Classes seen: `QuagoManager`, `QuagoSettings`,
  `QuagoMetaInformation`, `QuagoSettings_Bridge`.
- Selector: `isJailbrokenWithSkipAdvancedJailbreakValidation:` (patch → `NO`).
- Strings: `is_jailbroken`, `AUTHENTIC`, `UNAUTHENTIC`, `skipAdvancedJailbreakValidation`,
  `app_process_name`, `QUAGO-SDK`, log format `V/%@: %@`, `D/…`.
- No ADRP xrefs to those strings (referenced through `__DATA`/chained pointers).

The module: at `QuagoManager` registration it logs `quago: install`, `quago: classes=N`, and on the
first Quago image `quago: QuagoManager found`, then synchronously patches
`isJailbroken*` → `NO`, no-arg `authentic`/`integrity` BOOLs → `YES`, no-arg `tamper` → `NO`, logs
`quago: patched <class> <sel>` and `quago: selectors patched=N`, and defers the full method listing
(`quago: <Class> <sel>`) to the main queue.

Observed: `quago: install` printed; **`QuagoManager found` never printed** (the app exits before the
classes are reachable, or the class name/timing differs). The `classes=N` probe was added to tell which.

### Open gaps for Quago

- Capture `quago: classes=N` / `quago: QuagoManager found` from a fresh run.
- If `QuagoManager` is never found, enumerate class names containing `Quago` at UnityFramework load.
- The server-facing `AUTHENTIC/UNAUTHENTIC` report is separate; local selector patches do not clear it.
- Pokémon's app also exits on the **original** (certificate/Apple-ID signed, no mod) — see §7.

---

## 7. Pokémon GO and Survivor login findings

### Pokémon GO (0.435.1)

- Hidden-export Unity IL2CPP; `dlsym` fails for `il2cpp_*`, so `AMMachSymbols` `LC_SYMTAB` fallback is
  what finds them.
- Injection that works: **padding insert** of `LC_LOAD_DYLIB @executable_path/Frameworks/…` into the
  Mach-O header slack (the stub has ~12.7 KB free). Standard injectors (lief) corrupt initializers →
  `SIGSEGV` in `dyld` `callInitializer`. Padding insert + zsign boots to the splash and loads the mod.
- Signing: zsign certificate for consistent nested signatures; plumesign (Apple ID) does **not** re-sign
  modified nested code → `CODESIGNING / Invalid Page` kills.
- AMFI: a **root** dylib signed by zsign gets the app's entitlements and is rejected
  (`has entitlements but is not a main binary`). Plumesign signs nested dylibs without that problem.
- The game kills itself ~seconds after launch, before login; Quago `AUTHENTIC/UNAUTHENTIC`/integrity is
  the suspected gate. The **original, untouched** app also fails when re-signed, so it is not the mod.
- Artifacts: `C:\AccretionMods\work\pogo-insert\insert.py` (padding insert),
  `pogo-insert.ipa`, `pogo-quago*.ipa`, dump in `C:\AccretionMods\work\dumps\com.nianticlabs.pokemongo-0.435.1-a1c749d5`.

### Survivor: Ace (1.2.0, dump `C:\AccretionMods\work\dump-survivor-ace-plus-1.2.0\out\dump.cs`)

The login is a **custom socket protocol** (protobuf: `UserLoginRequest`, `SocketLoginRequest`,
`CSPlayerLogin`, `SCPlayerLogin`, plus `DevelopLoginRequest`) wrapped by a platform SDK login. The
"SDK login failed (ErrorCode=0)" dialog is `LoginViewModule.ShowSDKLoginRetryPopup(int, string)`
(RVA `0x7047C38`, file offset == RVA in `UnityFramework`).

Relevant identifiers (from `dump.cs`):

- `LoginViewModule` (TypeDefIndex 4799), fields: `m_customLogin` @0x38, `m_sdkLogin` @0x40,
  `_isLoginFinished` @0x60.
- `LoginViewModule.ShowSDKLoginRetryPopup` @ `0x7047C38`
- `LoginViewModule.OnLogin(account, deviceID, account2, verification)` @ `0x7047724`
- `LoginViewModule_CustomLogin` (4800): `m_account` @0x68, `m_deviceId` @0x70, `m_account2` @0x78,
  `OnClickLoginBt` @ `0x704E544` (reads `m_nameInput` @0x40 text into `m_account`, then calls OnLogin)
- `LoginViewModule_SDKLogin` (4801): `TriggerDirectLogin` @ `0x704EC24`, `OnGameStartClick` @ `0x704ED90`,
  `m_isSDKLoginFinished` @0x30
- `SDKManager.SDKLogin.OnSDKLogin(Action<string> onSuccess, Action<int,string> onFailure)`
  @ `0x…` in the `SDKManager` region (dump line ~1334460)
- SDKs present: `AdjustSdk`, `AdjustSigSdk`, `AppLovinQualityService`, `AppLovinSDK`, `FBAEMKit`,
  `FBAudienceNetwork`, `FBSDKCoreKit`, `FBSDKGamingServicesKit`, `FBSDKLoginKit`, `FirebaseCore`,
  `FirebaseCrashlytics`, `FirebaseInstallations`, `GoogleDataTransport`, `UnityFramework`,
  `SDKManager.FB`, `SDKManager.SDKFirebase`, `SDKManager.SDKAdjust`, `SDKManager.SDKAppsFlyer`,
  `SDKManager.SDKTGA`.
- Error codes: `UserLoginFailed = 102`, `VerifyLoginFailed = 120`.

What was tried:

- `runtime/AMSurvivorLogin.m` (probe; later removed from the Makefile): after 8–30 s it calls
  `AMIl2cppFindObject("LoginViewModule")` → `AMIl2cppField(…, "m_sdkLogin")` →
  `AMIl2cppCall(sdk, "LoginViewModule_SDKLogin", "TriggerDirectLogin", 0, NULL, NULL)`.
  It crashed (~6 s) calling the IL2CPP API too early; a gated version (only after
  `AMIl2cppClass("LoginViewModule")`/`…_SDKLogin` resolve) was built and installed, but its clean
  capture was never obtained.

Conclusion: the **original, untouched** copy shows the same `SDK login failed` — the login is a
**server-side account/SDK gate**. Client hooks (`TriggerDirectLogin`, custom login) cannot mint a valid
server session. Do not expect a client patch to fix it.

---

## 8. Other observations

- **Fallout Shelter+**: runs, pointer hooks install (`hooked Dweller…`), stable.
- **Bleach+**: runs the rebinder walk, then `exit()` from its own code — almost certainly its anti-tamper.
- **Into the Dead 2+**: rebinder write crash (`SIGBUS KERN_PROTECTION_FAILURE`) — the reason the safe
  write path was built.
- **Detection probe / observation apps** (`io.github.accretionmods.detectionprobe`,
  `…observation.nuclearday`) are installed from earlier work.

---

## 9. Current device state

Installed after the final cleanup:

- `io.github.accretionmods.survivoraceplus.2XN3V474V3` — **one** fresh Survivor mod copy; runs to the
  splash/update check (mod menu present), then hits the server login gate.
- `com.ndemicreations.survivors` — uninstalled (the original).
- Pokémon `com.nianticlabs.pokemongo` — was installed from `pogo-quago*.ipa`; uninstall it when done.
- Other mods/apps from earlier: `com.bethsoft.falloutshelter`, `com.survivor.ace.2XN3V474V3` (old
  base-copy test), `io.github.accretionmods.detectionprobe*`, `…observation.nuclearday*`,
  `io.github.accretionmods.vampiresurvivorsplus*`, plus the user's regular apps.

---

## 10. Cheat sheet

```powershell
$PMD = "$env:USERPROFILE\.local\bin\pymobiledevice3.exe"
$PY  = "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe"

# build / sign
& $PY scripts/dev.py build survivor-ace-plus --ipa --no-check
& $PY scripts/dev.py sign C:\AccretionMods\builds\survivor-ace-plus_1.2.0-mod1.0.14_accretion.ipa `
      --apple-id --install -o C:\AccretionMods\work\survivor-probe\out_id.ipa

# forced fresh process
& $PMD apps uninstall <bundle>
& $PMD apps install <ipa>

# capture-first launch (start capture, then launch)
Start-Process $PMD -ArgumentList @('syslog','live','-o',$log) -NoNewWindow
Start-Sleep 1; & $PMD developer dvt launch <bundle>
# wait, then grep: 'bypass:|bait |\[AccretionMods\]|quago:|login:'

# crash
& $PMD crash pull C:\AccretionMods\work\crashes
# inspect: exception/termination + faulting thread frames
```

Useful artifact paths:

- Builds: `C:\AccretionMods\builds\`
- Survivor probe/bait/insert: `C:\AccretionMods\work\survivor-probe\`
- Pokémon insert: `C:\AccretionMods\work\pogo-insert\`
- Dumps: `C:\AccretionMods\work\dump-survivor-ace-plus-1.2.0\out\dump.cs`,
  `C:\AccretionMods\work\dumps\com.nianticlabs.pokemongo-0.435.1-a1c749d5\out\dump.cs`
- Injection helper: `C:\Users\BenwillLopezNavarro\AppData\Local\Temp\inject_probe.py`
  (copies to a work path before relying on it; Temp may be cleaned)

---

## 11. Recommended next steps

1. **Rebinder: classic-only safe mode** — skip the chained import-name walk entirely; verify the bait
   (`visible=0`) and stability on Survivor. Then re-enable the chained path with a `vm_region`-checked
   name pointer.
2. **Make the bait scan repeat** (every 5 s) instead of once at +3 s, so `visible=` can be captured
   without racing the launch.
3. **Quago**: capture `quago: classes=N`/`QuagoManager found` from a fresh run; if absent, enumerate
   `Quago*` class names at UnityFramework load and patch the true owner.
4. **Pokémon**: the app is rejected by Quago/integrity server-side; without a signature/integrity bypass
   there is no login path. Treat as out of scope unless you choose to attack the AUTHENTIC report.
5. **Survivor login**: server-side gate; client hooks cannot mint a session. If you want to play, use the
   official App Store copy (no mod).
6. **Do not ship** with `AMDetectBypass.c` enabled until `visible=0` and stability are proven; keep the
   include commented out in `AMIl2cpp.m` for releases.

---

## 12. Risks / cautions

- The rebinder writes into live import tables; a bad slot corrupts the process (the observed `SIGBUS`
  crashes). Keep the `vm_region` checks, keep it disabled by default.
- Re-signed copies can be killed by integrity/anti-tamper (Pokémon, Survivor) for reasons unrelated to
  the mod. Check the **original** copy first to separate game policy from mod bugs.
- Signing is fragile: plumesign (Apple ID) for nested dylibs, zsign (certificate) for consistent nested
  signatures; root dylibs with entitlements are rejected by AMFI.
- The phone must be unlocked and awake; the lock screen silently defeats `dvt launch`.
- Reinstalling is the only reliable way to force a fresh process; `dvt kill` does not work on this device.

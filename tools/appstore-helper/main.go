// Command appstore-helper makes Mod Studio's App Store calls through ipatool's library
// (github.com/majd/ipatool, pinned in go.mod): Apple's sign-in now accepts only SAP-signed requests,
// which ipatool makes by running Apple's own signing code in a Go emulator.
//
// One JSON request on stdin, one JSON answer on stdout (scripts/appstore.py is the only caller):
//
//	{"cmd": "login", "email", "password", "code", "cookies"}  -> {"account": {...}} or {"needs_code": true}
//	{"cmd": "url", "account", "app_id", "cookies", "kbsync"}  -> {"url", "version", "bundle"}, {"needs_license": true}
//	                                                             or {"expired": true}
//	{"cmd": "license", "account", "app_id", "cookies"}        -> {"ok": true} (free apps only)
//	{"cmd": "versions", "account", "app_id", "cookies"}       -> {"ids": [...], "latest"} (the store's version ids)
//	{"cmd": "version_info", "account", "app_id", "version_id"} -> {"version", "released"} (one id's version)
//	{"cmd": "download", "account", "app_id", "version_id", "output"} -> {"path", "version", "bundle"}: the whole
//	                                                             .ipa as the App Store serves it (still encrypted)
//
// The password is used for the one sign-in and never written anywhere; the account that comes back has
// no password in it. "url" starts ipatool's download and stops it at the first request for the .ipa itself,
// so nothing is downloaded: Studio then reads only the zip's file list with range requests.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/byteness/keyring"
	cookiejar "github.com/juju/persistent-cookiejar"
	"github.com/majd/ipatool/v2/pkg/appstore"
	"github.com/majd/ipatool/v2/pkg/util/machine"
	"github.com/majd/ipatool/v2/pkg/util/operatingsystem"
)

type request struct {
	Cmd      string           `json:"cmd"`
	Email    string           `json:"email"`
	Password string           `json:"password"`
	Code     string           `json:"code"`
	Account  appstore.Account `json:"account"`
	AppID     int64            `json:"app_id"`
	VersionID string           `json:"version_id"`
	Output    string           `json:"output"`
	Cookies   string           `json:"cookies"`
	KBSync    string           `json:"kbsync"`
}

// kbsyncKey is ipatool's keychain entry for the kbsync blob its downloads send (pkg/appstore kbsyncCacheKey).
// Making one runs Apple's code in the emulator for about 40 s; ipatool reuses it while the store accepts it.
const kbsyncKey = "kbsync-v1"

// memKeychain keeps what ipatool would save for this run only, except the kbsync blob: with a path it is also
// kept in that file (one entry, owner-only), so the next run reuses it instead of generating a new one.
// The account is never written: Studio passes it with each request.
type memKeychain struct {
	mu    sync.Mutex
	items map[string][]byte
	path  string
}

func newKeychain(path string) *memKeychain {
	k := &memKeychain{items: map[string][]byte{}, path: path}
	if path != "" {
		if data, err := os.ReadFile(path); err == nil && len(data) > 0 {
			k.items[kbsyncKey] = data
		}
	}
	return k
}

func (k *memKeychain) Get(key string) ([]byte, error) {
	k.mu.Lock()
	defer k.mu.Unlock()
	data, ok := k.items[key]
	if !ok {
		return nil, keyring.ErrKeyNotFound
	}
	return data, nil
}

func (k *memKeychain) Set(key string, data []byte) error {
	k.mu.Lock()
	defer k.mu.Unlock()
	k.items[key] = data
	if key == kbsyncKey && k.path != "" {
		tmp := k.path + ".tmp"
		if err := os.WriteFile(tmp, data, 0o600); err == nil {
			_ = os.Rename(tmp, k.path) // best effort, as ipatool's own cache: a failed save costs one regeneration
		}
	}
	return nil
}

func (k *memKeychain) Remove(key string) error {
	k.mu.Lock()
	defer k.mu.Unlock()
	delete(k.items, key)
	if key == kbsyncKey && k.path != "" {
		_ = os.Remove(k.path) // ipatool drops a blob the store rejected
	}
	return nil
}

var errCaptured = errors.New("download address captured")

// capture: the .ipa's address, taken from the first GET for a .ipa while armed; that request never leaves.
type capture struct {
	mu    sync.Mutex
	armed bool
	url   string
}

func (c *capture) proxy(r *http.Request) (*url.URL, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.armed && r.Method == http.MethodGet && strings.HasSuffix(strings.ToLower(r.URL.Path), ".ipa") {
		c.url = r.URL.String()
		return nil, errCaptured
	}
	return http.ProxyFromEnvironment(r)
}

func main() {
	answer, err := run()
	if err != nil {
		answer = map[string]any{"error": err.Error()}
	}
	_ = json.NewEncoder(os.Stdout).Encode(answer)
	if err != nil {
		os.Exit(1)
	}
}

func run() (map[string]any, error) {
	var req request
	if err := json.NewDecoder(os.Stdin).Decode(&req); err != nil {
		return nil, fmt.Errorf("bad request: %w", err)
	}

	// every ipatool client is built on http.DefaultTransport (the signed ones on a clone of it): one proxy
	// function sees each request, which is how "url" stops at the .ipa
	catcher := &capture{}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = catcher.proxy
	http.DefaultTransport = transport

	jarOptions := &cookiejar.Options{}
	if req.Cookies != "" {
		jarOptions.Filename = req.Cookies
	}
	jar, err := cookiejar.New(jarOptions)
	if err != nil {
		return nil, fmt.Errorf("cookie jar: %w", err)
	}
	defer func() { _ = jar.Save() }()

	osys := operatingsystem.New()
	store := appstore.NewAppStore(appstore.Args{
		Keychain:        newKeychain(req.KBSync),
		CookieJar:       jar,
		OperatingSystem: osys,
		Machine:         machine.New(machine.Args{OS: osys}),
	})

	switch req.Cmd {
	case "login":
		return login(store, req)
	case "url":
		return downloadURL(store, req, catcher)
	case "license":
		return license(store, req)
	case "versions":
		return versions(store, req)
	case "version_info":
		return versionInfo(store, req)
	case "download":
		return download(store, req)
	}
	return nil, fmt.Errorf("unknown cmd %q", req.Cmd)
}

func login(store appstore.AppStore, req request) (map[string]any, error) {
	if req.Email == "" || req.Password == "" {
		return nil, errors.New("Apple ID and password, please")
	}
	out, err := store.Login(appstore.LoginInput{Email: req.Email, Password: req.Password, AuthCode: req.Code})
	if errors.Is(err, appstore.ErrAuthCodeRequired) {
		return map[string]any{"needs_code": true}, nil
	}
	if err != nil {
		return nil, fmt.Errorf("sign-in: %w", err)
	}
	acc := out.Account
	acc.Password = ""
	return map[string]any{"account": acc}, nil
}

// app: the store's record for the id (version, bundle, price), or the bare id when the lookup fails.
func app(store appstore.AppStore, req request) appstore.App {
	look, err := store.Lookup(appstore.LookupInput{Account: req.Account, AppID: req.AppID, Platform: appstore.PlatformIPhone})
	if err != nil || look.App.ID == 0 {
		return appstore.App{ID: req.AppID}
	}
	return look.App
}

func downloadURL(store appstore.AppStore, req request, catcher *capture) (map[string]any, error) {
	if req.AppID == 0 || req.Account.PasswordToken == "" {
		return nil, errors.New("an app id and a signed-in account, please")
	}
	target := app(store, req)
	dir, err := os.MkdirTemp("", "appstore-helper-")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(dir)

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()
	catcher.mu.Lock()
	catcher.armed = true
	catcher.mu.Unlock()
	_, err = store.Download(appstore.DownloadInput{Context: ctx, Account: req.Account, App: target, OutputPath: dir,
		Platform: appstore.PlatformIPhone})
	catcher.mu.Lock()
	found := catcher.url
	catcher.mu.Unlock()
	switch {
	case found != "":
		return map[string]any{"url": found, "version": target.Version, "bundle": target.BundleID}, nil
	case errors.Is(err, appstore.ErrLicenseRequired):
		return map[string]any{"needs_license": true}, nil
	case errors.Is(err, appstore.ErrPasswordTokenExpired):
		return map[string]any{"expired": true}, nil
	case err != nil:
		return nil, fmt.Errorf("download address: %w", err)
	}
	return nil, errors.New("the App Store gave no download for this app")
}

func license(store appstore.AppStore, req request) (map[string]any, error) {
	if req.AppID == 0 || req.Account.PasswordToken == "" {
		return nil, errors.New("an app id and a signed-in account, please")
	}
	target := app(store, req)
	if target.Price > 0 {
		return nil, errors.New("not a free app: paid apps are never bought")
	}
	err := store.Purchase(appstore.PurchaseInput{Account: req.Account, App: target, Platform: appstore.PlatformIPhone})
	if err == nil || errors.Is(err, appstore.ErrLicenseAlreadyExists) {
		return map[string]any{"ok": true}, nil
	}
	if errors.Is(err, appstore.ErrPasswordTokenExpired) {
		return map[string]any{"expired": true}, nil
	}
	return nil, fmt.Errorf("license: %w", err)
}

func signedRequest(req request) error {
	if req.AppID == 0 || req.Account.PasswordToken == "" {
		return errors.New("an app id and a signed-in account, please")
	}
	return nil
}

// versions: every version id the store keeps for the app (oldest first, as Apple lists them) and the newest.
func versions(store appstore.AppStore, req request) (map[string]any, error) {
	if err := signedRequest(req); err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()
	out, err := store.ListVersions(appstore.ListVersionsInput{Context: ctx, Account: req.Account, App: app(store, req),
		Platform: appstore.PlatformIPhone})
	switch {
	case errors.Is(err, appstore.ErrLicenseRequired):
		return map[string]any{"needs_license": true}, nil
	case errors.Is(err, appstore.ErrPasswordTokenExpired):
		return map[string]any{"expired": true}, nil
	case err != nil:
		return nil, fmt.Errorf("versions: %w", err)
	}
	return map[string]any{"ids": out.ExternalVersionIdentifiers, "latest": out.LatestExternalVersionID}, nil
}

// versionInfo: the version number and release date behind one version id (Apple names ids only by number).
func versionInfo(store appstore.AppStore, req request) (map[string]any, error) {
	if err := signedRequest(req); err != nil {
		return nil, err
	}
	if req.VersionID == "" {
		return nil, errors.New("a version id, please")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()
	out, err := store.GetVersionMetadata(appstore.GetVersionMetadataInput{Context: ctx, Account: req.Account,
		App: app(store, req), VersionID: req.VersionID, Platform: appstore.PlatformIPhone})
	if errors.Is(err, appstore.ErrPasswordTokenExpired) {
		return map[string]any{"expired": true}, nil
	}
	if err != nil {
		return nil, fmt.Errorf("version info: %w", err)
	}
	return map[string]any{"version": out.DisplayVersion, "released": out.ReleaseDate}, nil
}

// download: the whole .ipa (newest, or version_id) into the output folder, as ipatool saves it.
func download(store appstore.AppStore, req request) (map[string]any, error) {
	if err := signedRequest(req); err != nil {
		return nil, err
	}
	if req.Output == "" {
		return nil, errors.New("an output folder, please")
	}
	if err := os.MkdirAll(req.Output, 0o755); err != nil {
		return nil, err
	}
	target := app(store, req)
	ctx, cancel := context.WithTimeout(context.Background(), time.Hour)
	defer cancel()
	out, err := store.Download(appstore.DownloadInput{Context: ctx, Account: req.Account, App: target,
		OutputPath: req.Output, ExternalVersionID: req.VersionID, Platform: appstore.PlatformIPhone})
	switch {
	case errors.Is(err, appstore.ErrLicenseRequired):
		return map[string]any{"needs_license": true}, nil
	case errors.Is(err, appstore.ErrPasswordTokenExpired):
		return map[string]any{"expired": true}, nil
	case err != nil:
		return nil, fmt.Errorf("download: %w", err)
	}
	// the license (.sinf) comes beside the package and goes into it, as ipatool's own download command does:
	// without it iOS refuses to install the copy
	if err := store.ReplicateSinf(appstore.ReplicateSinfInput{Sinfs: out.Sinfs, PackagePath: out.DestinationPath}); err != nil {
		return nil, fmt.Errorf("download: the license didn't go into the package: %w", err)
	}
	return map[string]any{"path": out.DestinationPath, "version": target.Version, "bundle": target.BundleID,
		"sinfs": len(out.Sinfs)}, nil
}

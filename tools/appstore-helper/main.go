// Command appstore-helper makes Mod Studio's App Store calls through ipatool's library
// (github.com/majd/ipatool, pinned in go.mod): Apple's sign-in now accepts only SAP-signed requests,
// which ipatool makes by running Apple's own signing code in a Go emulator.
//
// One JSON request on stdin, one JSON answer on stdout (scripts/appstore.py is the only caller):
//
//	{"cmd": "login", "email", "password", "code", "cookies"}  -> {"account": {...}} or {"needs_code": true}
//	{"cmd": "url", "account", "app_id", "cookies"}            -> {"url", "version", "bundle"}, {"needs_license": true}
//	                                                             or {"expired": true}
//	{"cmd": "license", "account", "app_id", "cookies"}        -> {"ok": true} (free apps only)
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
	AppID    int64            `json:"app_id"`
	Cookies  string           `json:"cookies"`
}

// memKeychain keeps what ipatool would save (the account, its kbsync cache) for this run only.
type memKeychain struct {
	mu    sync.Mutex
	items map[string][]byte
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
	return nil
}

func (k *memKeychain) Remove(key string) error {
	k.mu.Lock()
	defer k.mu.Unlock()
	delete(k.items, key)
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
		Keychain:        &memKeychain{items: map[string][]byte{}},
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

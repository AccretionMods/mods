module github.com/AccretionMods/mods/tools/appstore-helper

go 1.25.0

// ipatool main d9c0247 (2026-10-25), unreleased. With v2.7.0 the store's kbsync blob is never made in time on this
// PC (its 1-minute emulator bound, ipatool issues 631 and 637), so every download address request falls back,
// gets HTTP 403 after ~60 s and nothing is cached; main runs store operations through Apple's frameworks.
// Back to a release once one ships.
require github.com/majd/ipatool/v2 v2.7.1-0.20261025002001-d9c0247daea5

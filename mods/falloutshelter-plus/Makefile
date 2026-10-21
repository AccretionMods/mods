TARGET := iphone:clang:latest:15.0
ARCHS = arm64

include $(THEOS)/makefiles/common.mk

TWEAK_NAME = FalloutShelterPlus

# menu/ and runtime/ (with hooks.json as runtime/am_hooks_data.c) are copied in by scripts/build.sh.
# SideloadCompat.m: keychain group, iCloud, Game Center and age-range answers for re-signed copies
# (same file as C:\AccretionMods\workflow\runtime\sideload_compat.m).
# No %hook and no Substrate: hooks go through the runtime, which looks Substrate up only with JIT on.
FalloutShelterPlus_FILES = Tweak.x menu/AMMenu.m SideloadCompat.m runtime/am_il2cpp.c runtime/AMIl2cpp.m runtime/am_hooks_data.c
FalloutShelterPlus_CFLAGS = -fobjc-arc
FalloutShelterPlus_FRAMEWORKS = UIKit Security
FalloutShelterPlus_LOGOS_DEFAULT_GENERATOR = internal

include $(THEOS_MAKE_PATH)/tweak.mk

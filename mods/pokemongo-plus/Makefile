TARGET := iphone:clang:latest:15.0
ARCHS = arm64

include $(THEOS)/makefiles/common.mk

TWEAK_NAME = PokemongoPlus

# menu/ and runtime/ (with hooks.json as runtime/am_hooks_data.c) are copied in, and AMTweaks.gen.m is
# generated from tweaks.json, by scripts/build.sh. No %hook and no Substrate: hooks go through the runtime.
PokemongoPlus_FILES = Tweak.x AMTweaks.gen.m menu/AMMenu.m runtime/SideloadCompat.m runtime/am_il2cpp.c runtime/AMIl2cpp.m runtime/AMMachSymbols.c runtime/AMIl2cppData.m runtime/AMTableHook.c runtime/AMQuagoBypass.m runtime/am_hooks_data.c
PokemongoPlus_CFLAGS = -fobjc-arc
PokemongoPlus_FRAMEWORKS = UIKit Security
PokemongoPlus_LOGOS_DEFAULT_GENERATOR = internal

include $(THEOS_MAKE_PATH)/tweak.mk

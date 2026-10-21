TARGET := iphone:clang:latest:15.0
ARCHS = arm64

include $(THEOS)/makefiles/common.mk

# A plain dylib, not a Substrate tweak: it loads even where no Substrate is injected.
LIBRARY_NAME = AMIl2cppProbe

# runtime/ files are copied in by scripts/build_probe.sh before each build.
AMIl2cppProbe_FILES = Probe.m runtime/am_il2cpp.c runtime/AMIl2cpp.m runtime/am_hooks_data.c
AMIl2cppProbe_CFLAGS = -fobjc-arc
AMIl2cppProbe_FRAMEWORKS = UIKit
AMIl2cppProbe_INSTALL_PATH = /Library/MobileSubstrate/DynamicLibraries

include $(THEOS_MAKE_PATH)/library.mk

TARGET := iphone:clang:latest:15.0
ARCHS = arm64

include $(THEOS)/makefiles/common.mk

TWEAK_NAME = SwiftRadioPlus

# menu/ is copied in from the repo root by scripts/build.sh before each build.
SwiftRadioPlus_FILES = Tweak.x menu/AMMenu.m
SwiftRadioPlus_CFLAGS = -fobjc-arc
SwiftRadioPlus_FRAMEWORKS = UIKit

include $(THEOS_MAKE_PATH)/tweak.mk

SHELL := /bin/bash

# Which image to build/run/flash: vm (default) | usb | metal. See config.sh.
VARIANT ?= vm
export VARIANT

.PHONY: all host-deps chroot pipe apks apkovl image usb metal images stick vm flash clean-chroot clean cards check-cards

all: image

# ---------------------------------------------------------------- #650 cards
# The overlay's per-box config is generated from a model card, never hand-typed.
# `make cards` regenerates the checked-in defaults; `make check-cards` is the
# gate that fails if anyone edits the derived files instead of the card.
cards:
	chmod +x overlay/usr/local/bin/pipebox-card scripts/check-cards.sh
	./overlay/usr/local/bin/pipebox-card generate \
	  --card overlay/etc/pipeos/card.conf \
	  --root overlay \
	  --templates overlay/usr/local/share/pipeos/card

check-cards:
	./scripts/check-cards.sh

# The USB-stick image for real hardware (extended ISO, hardware grub.cfg).
usb:
	$(MAKE) image VARIANT=usb

# The internal-disk image (standard ISO, hardware grub.cfg, no usb wait).
metal:
	$(MAKE) image VARIANT=metal

# A stick image for a NAMED box (appliance plan, decision 1): bakes the given
# card into the apkovl — card + derived identity files + provisioned marker —
# and builds the usb image under a name that says whose it is, so a
# personalized image can never be mistaken for the generic one.
# usage: make stick CARD=docs/cards/<box>.card
stick:
	@test -n "$(CARD)" || { echo "usage: make stick CARD=docs/cards/<box>.card" >&2; exit 1; }
	CARD="$(CARD)" ./scripts/40-build-apkovl.sh
	VARIANT=usb ./scripts/50-build-image.sh
	mv out/pipeos-usb.img "out/pipeos-usb-$$(basename "$(CARD)" .card).img"
	@echo "==> out/pipeos-usb-$$(basename "$(CARD)" .card).img"
	@echo "==> note: out/pipeos.apkovl.tar.gz now carries this box's card;"
	@echo "==>       a plain 'make image' rebuilds the generic apkovl."

# All three.
images:
	$(MAKE) image VARIANT=vm
	$(MAKE) image VARIANT=usb
	$(MAKE) image VARIANT=metal

host-deps:
	./scripts/00-host-setup.sh

chroot:
	./scripts/10-mk-chroot.sh

pipe:
	./scripts/20-build-pipe.sh

apks: chroot
	./scripts/30-build-apks.sh

apkovl:
	./scripts/40-build-apkovl.sh

image: apkovl
	./scripts/50-build-image.sh

vm:
	./scripts/60-run-vm.sh

# usage: make flash DEV=/dev/nvmeXn1
flash:
	./scripts/70-flash.sh $(DEV)

clean-chroot:
	-sudo umount out/chroot/pipeOS out/chroot/dev out/chroot/proc
	sudo rm -rf out/chroot

clean:
	rm -rf out/ovl out/p1.img out/pipeos.img out/pipeos.apkovl.tar.gz

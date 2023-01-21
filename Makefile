MP_DIR := ${CURDIR}/vendor/micropython
UNIX_DIR := ${MP_DIR}/ports/unix
PORT_DIR := ${MP_DIR}/ports/esp32
BOARD := ribbit
BUILD_DIR := ${PORT_DIR}/build-${BOARD}

DEVICE := /dev/cu.usbmodem141401

.PHONY: build
build: ui
	rm -f ${BUILD_DIR}/frozen_content.c
	ln -sfn ${CURDIR}/board ${PORT_DIR}/boards/ribbit
	make -C ${PORT_DIR} BOARD=${BOARD} FROZEN_MANIFEST=${CURDIR}/manifest.py
	mkdir -p ./firmware
	cp ${BUILD_DIR}/bootloader/bootloader.bin ${BUILD_DIR}/partition_table/partition-table.bin ${BUILD_DIR}/ota_data_initial.bin ${BUILD_DIR}/micropython.bin ./firmware

.PHONY: ui
ui:
	python3 ./tools/generate_static.py

${UNIX_DIR}/build-standard/micropython:
	make -C ${MP_DIR}/ports/unix -j FROZEN_MANIFEST=${CURDIR}/manifest-unix.py

.PHONY: test
test: ${UNIX_DIR}/build-standard/micropython
	cd modules ; ${UNIX_DIR}/build-standard/micropython -m unittest discover -p "*_test.py"

${UNIX_DIR}/build-simulator/micropython:
	rm -rf ${UNIX_DIR}/variants/simulator
	cp -rp ${CURDIR}/board-simulator ${UNIX_DIR}/variants/simulator
	make -C ${MP_DIR}/ports/unix -j VARIANT=simulator

.PHONY: simulator
simulator: ${UNIX_DIR}/build-simulator/micropython
	cd modules ; ${UNIX_DIR}/build-simulator/micropython -m main

.PHONY: flash
flash: build
	esptool.py -p ${DEVICE} -b 460800 --before default_reset --after no_reset \
		--chip esp32s3 \
		write_flash --flash_mode dio --flash_size detect --flash_freq 80m \
		0x0 firmware/bootloader.bin \
		0x8000 firmware/partition-table.bin \
		0xd000 firmware/ota_data_initial.bin \
		0x10000 firmware/micropython.bin

.PHONY: clean
clean:
	rm -rf ${BUILD_DIR} ${UNIX_DIR}/build-standard ${UNIX_DIR}/build-simulator
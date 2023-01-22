import uasyncio as asyncio
from micropython import const
import logging


_MODEL_POWER_PIN = const(13)
_MODEL_ENABLE_PIN = const(6)

_MAX_SOCKETS = const(12)


class ModemManager:
    def __init__(self, stream, power_pin, enable_pin, apn):
        self._logger = logging.getLogger(__name__)

        self._stream = stream
        self._power_pin = power_pin
        self._enable_pin = enable_pin
        self._apn = apn

        self._read_loop_task = None

        self._lock = asyncio.Lock()

        self._current_command = None
        self._current_additional_responses = []
        self._current_data = None
        self._response = []
        self._response_event = asyncio.Event()

        self._ready_event = asyncio.Event()

        self._connections = [None] * (_MAX_SOCKETS)

    async def _read_loop(self):
        state = 0
        response = []
        while True:
            line = await self._stream.readline()
            self._logger.info("Received line %s", line)

            if line is None:
                continue
            if len(line) < 2 or line[-1] != 10 or line[-2] != 13:
                continue

            line = line[0:-2]

            if (
                state == 0
                and self._current_command is not None
                and line == self._current_command
            ):
                # Seen the command echo:
                state = 1
                if self._current_data is not None:
                    self._logger.info("Sending data")
                    self._stream.write(self._current_data)
                    await self._stream.drain()
                continue

            elif state == 1:
                response.append(line)
                if (
                    line == b"OK"
                    or line == b"ERROR"
                    or line.startswith(b"+CME ERROR: ")
                    or (
                        self._current_additional_responses is not None
                        and line in self._current_additional_responses
                    )
                ):
                    state = 0
                    self._current_command = None
                    self._current_data = None
                    self._current_additional_responses = []
                    self._response = response
                    response = []
                    self._response_event.set()
                    if self._current_stop_after_response:
                        self._logger.info("Stop read loop")
                        return

            else:  # URC
                await self._handle_urc(line)

    async def _handle_urc(self, line):
        if not self._ready_event.is_set():
            if line == b"RDY":
                self._ready_event.set()
            return

        self._logger.info("URC: %s", line)

    async def send_command(
        self,
        command,
        data=None,
        additional_responses=None,
        timeout_ms=5000,
        stop_after_response=False,
    ):
        async with self._lock:
            self._logger.info("Sending command %s", command)

            command = command + b"\r"
            self._current_command = command
            self._current_additional_responses = additional_responses
            self._current_data = data
            self._current_stop_after_response = stop_after_response
            self._response = []
            self._response_event = asyncio.Event()

            self._stream.write(command)
            await self._stream.drain()

            await asyncio.wait_for_ms(self._response_event.wait(), timeout_ms)

            if self._response[-1] != b"OK" and additional_responses is None:
                raise Exception("Error command", command, self._response)
            return self._response

    async def _power_off(self):
        self._logger.info("Disabling modem")

        if self._read_loop_task is not None:
            self._read_loop_task.cancel()
            self._read_loop_task = None

        self._enable_pin.off()
        self._power_pin.off()
        await asyncio.sleep_ms(2000)

    async def _reset(self):
        while True:
            await self._power_off()

            self._read_loop_task = asyncio.create_task(self._read_loop())

            self._logger.info("Enabling modem")
            self._power_pin.on()
            self._enable_pin.on()

            try:
                await asyncio.wait_for_ms(self._ready_event.wait(), 30000)
                return
            except asyncio.TimeoutError:
                continue

    async def _connect_loop(self):
        while True:
            try:
                await self._reset()

                # Wait for the modem to be ready:
                await self.send_command(b"AT", timeout_ms=30000)

                await self.send_command(b"AT+CMEE=2")

                # Set the modem to GSM + LTE:
                await self.send_command(b'AT+QCFG="nwscanseq",00')
                await self.send_command(b'AT+QCFG="nwscanmode",0,1')

                # Configure APN:
                await asyncio.sleep_ms(5_000)
                await self.send_command(b'AT+CGDCONT=1,"IP","%s"' % (self._apn))

                while True:
                    await self.send_command(b"AT+CFUN=1")

                    resp = await self.send_command(b"AT+CGREG?")
                    if resp[0] in (b"+CGREG: 0,1", b"+CGREG: 0,5"):
                        break

                    resp = await self.send_command(b"AT+CEREG?")
                    if resp[0] in (b"+CEREG: 0,1", b"+CEREG: 0,5"):
                        break
                    await asyncio.sleep_ms(5_000)

                await self.send_command(
                    b"ATD*99***1#",
                    additional_responses=[b"CONNECT 150000000"],
                    stop_after_response=True,
                )

                u = self._stream.s

                self._logger.info("Data session activated!")

                await asyncio.sleep_ms(5_000)

                import network

                ppp = network.PPP(u)
                ppp.active(True)
                ppp.connect()

                while True:
                    await asyncio.sleep_ms(1_000)
                    #print(ppp.status(), ppp.ifconfig())

            except Exception as exc:
                self._logger.exc(exc, "Exception in modem loop")
                await asyncio.sleep_ms(20000)


async def _main():
    import machine

    power_pin = machine.Pin(_MODEL_POWER_PIN, machine.Pin.OUT)
    enable_pin = machine.Pin(_MODEL_ENABLE_PIN, machine.Pin.OUT)

    uart = machine.UART(
        2,
        baudrate=115200,
        bits=8,
        parity=None,
        stop=1,
        rx=38,
        tx=39,
        rts=11,
        cts=10,
    )

    m = ModemManager(
        stream=asyncio.StreamReader(uart),
        power_pin=power_pin,
        enable_pin=enable_pin,
        apn="hologram",
    )
    await m._connect_loop()


asyncio.run(_main())
from cs1237 import CS1238
from machine import Pin

# Extends from cs1237.py file
class NonBlockingCS1238(CS1238):
    """CS1238 reader which preserves a ready conversion until it is consumed."""

    def __init__(self, *args, **kwargs):
        self._conversion_pending = False
        super().__init__(*args, **kwargs)

    def start_read(self):
        # server.py calls this on every pass. Re-arming the base implementation
        # would clear __drdy and lose a sample which became ready between passes.
        if self._conversion_pending:
            return
        super().start_read()
        self._conversion_pending = True

    def read_now_and_select(self, channel=None):
        """Return the ready sample and optionally select the next channel."""
        if not self.ready():
            return None

        self.data.irq(handler=None)
        result = self.__read_bits(24)
        if result > 0x7FFFFF:
            result -= 0x1000000

        if channel is not None:
            # A CS1238 configuration write follows directly after the completed
            # conversion. Unlike config(), this does not perform another read().
            config = self.rate << 4 | self.gain << 2 | channel
            self.__write_cmd(0x65)
            self.__write_bits(config << 1, 0x100)
            self.data.init(mode=Pin.IN)
            self.channel = channel
            
            # Last configuration
            self.clock(1)
            self.clock(1)
            self.clock(0)
        self.__drdy = False
        self._conversion_pending = False
        self.start_read()
        return result
import time
from machine import Pin, ADC, UART, SoftI2C
from eeprom import EEPROM
from validation import is_valid_can_id, is_valid_can_data
from debug import dprint
from cs1238 import NonBlockingCS1238

MAX_DO = const(2)
MAX_DI = const(2)
MAX_AO = const(0)
MAX_AI = const(2)

# ADC
ADC_MODE_VOLTAGE = const(0)
ADC_MODE_CURRENT = const(1)
ADC_PIN_PREDEF_MODE = [ADC_MODE_CURRENT,ADC_MODE_CURRENT, ADC_MODE_VOLTAGE, ADC_MODE_VOLTAGE]

# DAC
DAC_BITS = const(12)
MAX_DAC_WRITE_VALUE = const((1 << DAC_BITS) - 1)

# Used for calibration DAC. (Vmax and Vmin per channel)
# DAC0 still need some normalization
DAC_CONFIG = {
    0: (0.024, 9.89),
    1: (0.079, 9.88)
}

# UART and CAN default Configs
# Default uses CAN, import UART if not available
CAN_ENABLED = True
CAN_CONFIGS = {}
try:
    from config import CAN_CONFIGS
except ImportError:
    CAN_ENABLED = False

if not CAN_ENABLED:
    try:
        from config import UART_CONFIGS
    except ImportError:
        UART_CONFIGS = {}

# CAN
MAX_CAN_FRAMES = const(3) # Depracted

# EEPROM
MAX_EEPROM_BYTES_PER_WRITE = const(1000)
MAX_EEPROM_BYTES_PER_READ = const(1000)
EEPROM_ADDRS = set(range(0x50, 0x58))

# 4G_MODULE (MODEM)
try:
    from config import MODEM_CONFIGS
except ImportError:
    MODEM_CONFIGS = {}

if MODEM_CONFIGS:
    from modem import ModemManager

# Load Cell
LOADCELL_ENABLED = True
LOADCELL_CAPACITY_KG = -1
LOADCELL_RATED_OUTPUT = -1
LOADCELL_EXCITATION_V = 10 # Max is 5 / 10 according to schematics

try:
    from config import LOADCELL_CAPACITY_KG, LOADCELL_RATED_OUTPUT
except ImportError:
    LOADCELL_ENABLED = False
    
# Thermocouple
THERMOCOUPLE_ENABLED =  True
try:
    from config import CELSIUS_MAX, CELSIUS_MIN, CELSIUS_PGA
except ImportError:
    THERMOCOUPLE_ENABLED = False

if THERMOCOUPLE_ENABLED:
    try:
        from cs1237 import CS1238
    except ImportError:
        THERMOCOUPLE_ENABLED =  False
        print("Thermocouple disabled due to no cs1237.py file.")

# Extra




class Device:
    def __init__(self):
        
        # Hardware interfaces
        self.din = []
        self.dout = []
        self.analog_in = []
        self.analog_out = []
        
        self.serial = None
        self.uart_baudrate = 0
        self.uart_bits = 0
        self.uart_parity = 0
        self.uart_stop = 0
        
        self.can_bus = None
        self.can_baudrate = 0
        self.can_mode = None
        
        self.i2c = None
        self.eeprom = None
        
        self.modem = None
     
        self.loadcell = None
        self.loadcell_capacity = -1
        self.loadcell_v0_max = 0
        
        self.temp = None
        self._temp_channel = 0
        self._temp_discard_next = 0
        self._temp_values = [None, None]
        self._cold_junction_temp = 20.0
        
        # Initialization
        # -- Digital --
        for idx in range(MAX_DI):
            self.din.append(Pin(f'IN{idx + 1}', Pin.IN, Pin.PULL_UP)) 
        
        for idx in range(MAX_DO):
            pin = Pin(f'RY{idx + 1}', Pin.OUT)
            pin.value(0)
            self.dout.append(pin)
        
        # Analog
        for idx in range(MAX_AI):
            pin = ADC(Pin(f'AI{idx}'))
            mode = ADC_PIN_PREDEF_MODE[idx]
            self.analog_in.append((pin, mode))
            
        for idx in range(MAX_AO):
            pin = DAC(Pin(f'AO{idx}'), bits = DAC_BITS)
            pin.write(0)
            self.analog_out.append(pin)

        # UART/CAN for Pin(PC10, PC11)
        dprint(f"Initing CAN_ENABLED: {CAN_ENABLED}")
        if not CAN_ENABLED:
            self.uart_baudrate = UART_CONFIGS.get('baudrate', 9600)
            self.uart_bits = UART_CONFIGS.get('bits', 8)
            self.uart_parity = UART_CONFIGS.get('parity', None)
            self.uart_stop = UART_CONFIGS.get('stop', 1)
            self.serial = UART(
                4,
                self.uart_baudrate,
                self.uart_bits,
                self.uart_parity,
                self.uart_stop,
            )
        else:
            self.can_baudrate = CAN_CONFIGS.get('baudrate', 500000)
            self.can_mode = CAN_CONFIGS.get('mode', CAN.NORMAL)
            self.can_bus = CAN(
                1,
                baudrate = self.can_baudrate,
                mode = self.can_mode,
                rx = Pin("PB8"),
                tx = Pin("PB9"),
            )
                
            
        # I2C, can have more than 1 I2C device
        self.i2c = SoftI2C(scl=Pin('PB6'), sda=Pin('PB7') )  
        
        #EEPROM
        if self._is_eeprom_connected():
            self.eeprom = EEPROM(
                pages = 128,
                bpp = 16,
                i2c = self.i2c
            )
        
        # 4G
        if MODEM_CONFIGS:
             self.modem = ModemManager(
                MODEM_CONFIGS.get("baudrate", 115200),
                MODEM_CONFIGS.get("bits", 8),
                MODEM_CONFIGS.get("parity", None),
                MODEM_CONFIGS.get("stop", 1)     
             )
             
        # Load cell
        if LOADCELL_ENABLED:
            self.loadcell = ADC('PA6')
            if LOADCELL_CAPACITY_KG == 10.0:
                amplifier_gain = 500.0
            else:
                amplifier_gain = 250.0
            self.loadcell_capacity = LOADCELL_CAPACITY_KG
            self.loadcell_vo_max = (LOADCELL_RATED_OUTPUT * LOADCELL_EXCITATION_V) / 1000 * amplifier_gain
        
        # Thermocouple
        if THERMOCOUPLE_ENABLED:
            self.temp = NonBlockingCS1238(
                Pin("PB3"),
                Pin("PB4"),
                gain = CELSIUS_PGA,
                rate = 40,
                channel = 0
            )
            self.temp.start_read()
                        
        
    # Digital I/O
    def read_din_at(self, idx = 0):
        try:
            return self.din[idx].value()
        except IndexError:
            print('Index out of range.')
            return -1
        except TypeError:
            print('idx must be an integer.')
            return -1
            
    def write_dout_at(self, idx = 0, value = 0):
        try:
            pin_value = self.dout[idx].value(value)
            return pin_value
        except IndexError:
            print('Index out of range.')
            return -1
        except TypeError:
            print('idx must be an integer.')
            return -1
    
    def read_dout_at(self, idx = 0):
        try:
            return self.dout[idx].value()
        except IndexError:
            print('Index out of range.')
            return -1
        except TypeError:
            print('idx must be an integer.')
            return -1
        
    def write_dout(self, pin, value):
        try:
            pin = Pin(pin, Pin.OUT)
            pin.value(value)
            return pin.value()
        except ValueError:
            print(f'No pin {pin}')
            return -1
    
    # Analog I/O
    def write_aout_at(self, idx, voltage):
        try:
            pin = self.analog_out[idx]
            Vmin, Vmax = DAC_CONFIG.get(idx, 0) 
            # Calibration with measured values
            steps_needed = int((voltage - Vmin)* MAX_DAC_WRITE_VALUE / (Vmax-Vmin))  
            if steps_needed > MAX_DAC_WRITE_VALUE:
                steps_needed = MAX_DAC_WRITE_VALUE
            elif steps_needed < 0:
                steps_needed = 0
           
            print(f'{voltage} used {steps_needed}.')
            pin.write(steps_needed)
            
            # Old
#             volts_per_step = 9.9 / ((1 << 12) - 1)
#             steps_needed = int(voltage / volts_per_step)
#             if steps_needed > (1 << 12) -  1:
#                 steps_needed = (1 << 12) - 1
#             pin.write(steps_needed)
            return True
        except IndexError:
            print(f'Wrong index for AO: {idx}')
            return -1
    
    def write_aout(self, pin_name, voltage):
        try:
            pin = DAC(pin_name)
            
            # Possible pin_name can only be AO0/1 and PA4/5
            if pin_name.startswith('AO'):
                idx = int(pin_name[-1])
            else:
                idx = 0 if int(pin_name[-1]) == 4 else 1 
                
            Vmin, Vmax = DAC_CONFIG.get(idx, 0) 
            # Calibration with measured values
            steps_needed = int((voltage - Vmin)* MAX_DAC_WRITE_VALUE / (Vmax -Vmin))  
            if steps_needed > MAX_DAC_WRITE_VALUE:
                steps_needed = MAX_DAC_WRITE_VALUE
            elif steps_needed < 0:
                steps_needed = 0
           
            print(f'{voltage} used {steps_needed}.')
            pin.write(steps_needed)
            return True
        except IndexError:
            print(f'Wrong index for AO: {idx}')
            return -1
    
    def read_ain_all(self):
        data = {}
        for idx, analog_in_with_mode in enumerate(self.analog_in):
            data[f'AI{idx}'] = {
                "value": self.read_ain_at(idx),
                "mode": analog_in_with_mode[1]
            }
        return data
    
    def read_ain_at(self, idx):
        try:
            pin, mode = self.analog_in[idx]
            voltage = (pin.read_u16() / 65535) * 3.3
            return self._calculate_adc_value(voltage, mode)
        except IndexError:
            print(f'Wrong index for AI: {idx}')
            return -1
    
    def _calculate_adc_value(self, voltage, mode = ADC_MODE_CURRENT):
        if mode == ADC_MODE_CURRENT:
            # V_measure = I_actual * 120R
            # Apparently, VADC0 is same as VAD0 if in current mode.
            # A to mA is * 1000
            return (voltage / 120) * 1000
        else:
            # V_measure = V_out * 4.7 / (10 + 4.7)
            # V_out can be calculated with read_u16 * Vmax_pin (3.3V) 
            return (voltage * 14.7) / 4.7
        
    # UART
    def uart_transmission(self, message):
        if not self.send_uart_message(message.encode()):
            return None
                
    def send_uart_message(self, message):
        if self.serial is None:
            return
        head = 0
        while head < len(message):
            mv = memoryview(message)[head:]
            num_bytes = self.serial.write(message)
            if not num_bytes:
                return False
            head += num_bytes
        return True
    
    def recv_uart_message(self):
        if self.serial is None:
            return
        if not self.serial.any() > 0: 
            return None
        data = bytearray()
        while self.serial.any():
            data.extend(self.serial.read())
        if data is not None: # DK why sometimes will be None
            try:
                message = data.decode('utf-8', 'replace')
                return message
            except UnicodeError as e:
                print('Decoding uart failed. Please check your UART port settings.')
                print('Raw: ', data)
        return None
    
    # CAN
    def send_can_frame(self, can_id , data):
        if not self.can_bus:
            print('Can bus not initialized')
            return False        
        if not isinstance(data, (bytes, bytearray)):
            try:
                data = data.encode()
            except Exception as e:
                print("Can't decode data: ", data)
                dprint(e)
                return False           
        if not (is_valid_can_id(can_id) and is_valid_can_data(data)):
            return False
        try:
            self.can_bus.send(data, can_id)
        except OSError as e:
            print('Error in can data send.')
            dprint(e)
            return False
        return True
            
    def recv_can_frames(self):
        if not self.can_bus:
            print('Can bus not initialized')
            return None
        
        if not self.can_bus.any():
            return None
        
        frames = []
        while self.can_bus.any() and len(frames) < MAX_CAN_FRAMES:
            raw_frame = self.can_bus.recv(0, CAN_TIMEOUT)
            frame_data = {}
            frame_data["id"] = raw_frame[0]
            frame_data["data"] = raw_frame[1]
            frame_data["frame_type"] = raw_frame[2]
            frames.append(frame_data)
        return frames
            
    # I2C
    # EEPROM
    def _is_eeprom_connected(self):
        scan_addr = set(self.i2c.scan())
        if EEPROM_ADDRS.issubset(scan_addr):
            return True
        return False
            
    def eeprom_write(self, data, idx = 0):
        assert self.eeprom is not None
        if not isinstance(data, bytes):
            data = data.encode('utf-8')
        if len(data) > MAX_EEPROM_BYTES_PER_WRITE:
            print('Exceed max write capacity: ', MAX_EEPROM_BYTES_PER_WRITE)
            return -1
        try:
            self.eeprom.write(idx, data)
        except ValueError as e:
            print(e)
            return -1
        
    def eeprom_read(self, idx = 0):
        assert self.eeprom is not None
        try:
            return self.eeprom.read(idx, MAX_EEPROM_BYTES_PER_READ)
        except ValueError as e:
            print(e)
            return -1
        
    def eeprom_wipe(self):
        assert self.eeprom is not None
        self.eeprom.wipe()
        
    # 4G
    def write_to_modem(self, command):
        if self.modem is None:
            return
        return self.modem.write(command)
                              
    def read_from_modem(self):
        if self.modem is None:
            return
        return self.modem.read()
    
    # Load cell
    def get_loadcell_in_kg(self):
        if not self.loadcell:
            return -1
        
        # Get actual voltage to ADC2,
        # divider calculation from schematics
        vin_divider = 4.7 / (10 + 4.7)
        Vo = (self. _read_loadcell_average()/65535 * 3.3) / vin_divider
        
        # Voltage-to-Weight conversion
        weight = Vo / self.loadcell_vo_max * self.loadcell_capacity
        return weight
    
    def _read_loadcell_average(self):
        total = 0
        count = 0
        while count < 100:
            total += self.loadcell.read_u16()
            count += 1
        return total // count
        
    # Thermocouple
    def _calibrate_temp(self, temp):        
        # Calibration, from Keil source code
        if temp < 100:
            temp += 33
        elif temp < 200:
            temp += 33
        elif temp < 300:
            temp += 32
        elif temp < 400:
            temp += 29
        elif temp < 500:
            temp += 23
        elif temp < 600:
            temp += 18
        elif temp < 700:
            temp += 14
        elif temp < 800:
            temp += 12
        elif temp < 900:
            temp += 12
        elif temp < 1000:
            temp += 14
        elif temp < 1100:
            temp += 19
        elif temp < 1200:
            temp += 27
        elif temp < 1300:
            temp += 38

        return temp
    
    def read_temperature(self):
        raw = self.temp.read_now()
        if raw is None:
            return None
    
        # CS1238 raw ADC -> differential thermocouple voltage in mV
        # Formula: (Scaled_ADC) * CS1238 Coefficient (See section 2.6.3)
        mv = raw / 0x7FFFFF * 2500 / 2 / CELSIUS_PGA

        # thermocouple difference
        tc_temp = mv / 0.041

        # cold junction
        cj_temp = self.temp.temperature()

        # Reset
        self.temp.start_read()
        return tc_temp + cj_temp

    def poll_temperatures(self):
        """Advance one non-blocking K1/K2/cold-junction conversion cycle."""
        if self.temp is None or not self.temp.ready():
            return None

        completed_channel = self._temp_channel

        # The delta-sigma filter can still contain data from the previous input
        # immediately after a channel change. Consume one conversion without
        # changing channels, then use the following settled conversion.
        if self._temp_discard_next > 0:
            self.temp.read_now_and_select()
            self._temp_discard_next -= 1
            return None

        next_channel = (completed_channel + 1) % 3
        raw = self.temp.read_now_and_select(next_channel)
        if raw is None:
            return None
        self._temp_channel = next_channel
        self._temp_discard_next = 2

        if completed_channel == 2:
            cold_junction_temp = (
                raw / self.temp.ref_value *
                (273.15 + self.temp.ref_temp) - 273.15
            )
            if -40 <= cold_junction_temp <= 125:
                self._cold_junction_temp = cold_junction_temp
        else:
            mv = raw / 0x7FFFFF * 2500 / 2 / CELSIUS_PGA
            temperature = (
                mv / 0.041 + self._cold_junction_temp
            )
            if CELSIUS_MIN <= temperature <= CELSIUS_MAX:
                self._temp_values[completed_channel] = temperature

        if None in self._temp_values:
            return None
        return self._temp_values[:]
        
        
                
    
    

    
        


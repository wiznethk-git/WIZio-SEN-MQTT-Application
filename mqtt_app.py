import json
from setup import SetUpWiznetChip
from device_mqtt import DeviceMQTT
from device import Device
from debug import DEBUG, dprint
from validation import validate_ipv4

device = Device()
client = DeviceMQTT(device = device) 

# You can assign new / alter topics under this file.

#  =========== Network ==============
@client.add_topic('network_config/set')
def _handle_network_config(msg):
    assert client.nic is not None
    try:
        msg = json.loads(msg)
    except Exception as e:
        print('Error in loading network config msg')
        dprint('Error:',e)
        dprint('Msg:',msg)
        return
    
    # Check if used DHCP first or not
    dhcp = msg.get('dhcp', False)
    if dhcp and dhcp != client.is_dhcp:
        client.disconnect()
        client.nic, client.is_dhcp = SetUpWiznetChip()
    else:
        config = (
            msg.get('ip', None),
            msg.get('mask', None),
            msg.get('gateway', None),
            msg.get('dns', None)  
        )
        for addr in config:
            is_valid, err_msg = validate_ipv4(addr)
            if not is_valid:
                # Do nothing if ipv4 failed
                dprint(f'IPV4 failed on index {config.index(addr)}: {addr}')
                print(err_msg)
                return
        client.disconnect()
        client.nic, client.is_dhcp = SetUpWiznetChip(config, dhcp)
             
    # Force a restart as network changed
    client.restart = True
    return

#  =========== Digital IO ============
@client.add_topic("digital_output/+/set")
def handle_digital_output(pin, msg):
    assert client.device is not None
    dprint('Handling DOUT in MQTT...')
    try:
        data = json.loads(msg)
        
        # Ensure only Dictionary type can be used
        if not isinstance(data, dict):
            return

        pin = pin.decode()
        value = data.get("value", 0)

        pin_state = client.device.write_dout(pin, value)

        if pin_state != -1:
            client.publish(
                f"digital_output/{pin}/state",
                {"value": pin_state},
            )

    except (ValueError, TypeError, UnicodeError) as e:
        dprint("Invalid digital output message:", e)
    except Exception as e:
        dprint(e)
        
#  =========== Analog IO ============
@client.add_topic('analog_output/+/set')
def handle_analog_output(pin, msg):
    # Handle AO0/1 based on message
    assert client.device is not None
    dprint('Handling Analog in MQTT...')
    try:
        data = json.loads(msg)

        # Ensure only Dictionary type can be used
        if not isinstance(data, dict):
            return
        
        pin, voltage = pin.decode('utf-8'), data.get('voltage', 0)
        
        # Quit if no pin is provided.
        if not pin:
            dprint('No pin is provided.')
            return
        
        client.device.write_aout(pin, voltage)
        client.publish(f'analog_output/{pin}/state', {"voltage": voltage})
    except (ValueError, TypeError, UnicodeError) as e:
        dprint("Invalid analog output message:", e)
    except Exception as e:
        dprint(e)

#  =========== Serial ============
@client.add_topic('uart/write')        
def write_to_uart(msg):
    dprint('write UART message')
    try:
        msg = msg.decode('utf-8')
    except:
        dprint('UART message cannot be decoded')
        return
    client.device.send_uart_message(msg)

#  =========== EEPROM ============
@client.add_topic('eeprom/write')
def write_to_eeprom(msg):
    dprint('Writing eeprom in MQTT...')
    try:
        msg = msg.decode('utf-8')
    except Exception as e:
        # Just return if can't decode
        dprint('Could not encode msg: ', msg)
        return
    client.device.eeprom_write(msg)

@client.add_topic('eeprom/read/request')
def read_from_eeprom(msg):
    dprint('Requesting a read in EEPROM')
    try:
        msg = json.loads(msg)
    except Exception as e:
        print('Failed to load json.')
        dprint(e)
        return
    data = client.device.eeprom_read(int(msg.get('index', 0)))
    client.publish(b'eeprom/read/response', data)

@client.add_topic('eeprom/wipe')  
def wipe_eeprom(msg):
    dprint('Wipe EEPROM')
    if msg.decode('utf-8') == "True":
        client.device.eeprom_wipe()

def start():
    while True:
        try:
            client.run()
        except Exception as e:
            print('========== Error ===========')
            if DEBUG:
                raise e
            else:
                print (e, '\n')
            client.disconnect()
            client.restart = False
            break
        except KeyboardInterrupt:
            client.disconnect()
            client.restart = False
            break

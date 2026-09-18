# This class is serve as a bridge between umqtt.simple
# with our device.
import gc
import json
import time
import array
from setup import SetUpWiznetChip
from debug import dprint
from device import Device, ADC_MODE_VOLTAGE ,ADC_MODE_CURRENT
from binascii import b2a_base64

try:
    from umqtt.simple import MQTTClient
except ImportError:
    from setup import SetUpWiznetChip
    nic, _ = SetUpWiznetChip()
    if nic.isconnected():
        import mip
        mip.install('umqtt.simple', mpy = True,target = '/flash')
        
from umqtt.simple import MQTTClient

class DeviceMQTT:
    def __init__(self, server = None, device = None):
        self.host = None
        self.usr = None
        self.pwd = None
        self.port = None
        self.client = None
        
        self.is_connected = False
        self.handlers = {}
        
        self._set_config()
        if server:
            self.client = MQTTClient(
                client_id = server.mac_addr,
                server = self.host,
                user = self.usr,
                password = self.pwd,
                port = self.port
            )
        
        # Used only when you call run.
        if device:
            self.restart = False
            self.device = device
            
            self.tasks = []
            self.nic = None
            self.is_dhcp = None
            
            self.previous_din = [False for _ in self.device.din]
            self.analog_input_readings = tuple([] for _ in self.device.analog_in)
            
            self.weight_readings = []
            
            # Set up Ethernet
            self.nic, self.is_dhcp = SetUpWiznetChip()
            mac = self.nic.config('mac')
            mac_addr = ':'.join(['%02x' % b for b in mac])
            
            # Set up client
            self.client = MQTTClient(
                client_id = mac_addr,
                server = self.host,
                user = self.usr,
                password = self.pwd,
                port = self.port
            )


    def connect(self):
        try:
            self.client.connect()
            self.is_connected = True
            self.client.set_callback(self.callback)
            self.subscribe_all()
            return True
        except Exception as e:
            print('Connection error in MQTT.')
            print(str(e))
        return False
    
    def callback(self, topic, msg):
        print(f"Received message on topic: {topic}")
        dprint(f"Payload: {msg}")
        
        # Special handling for Digital IO and Analog IO
        if topic.startswith((b"digital_output", b"analog_output")):  
            try:
                peripheral, pin, action = topic.split(b"/")
            except Exception as e:
                dprint(e)
                dprint("Error with topic :", topic)
                return
            formatted_topic = peripheral + b"/+/" + action 
            handler = self.handlers.get(formatted_topic, None)
            if handler is not None:
                handler(pin, msg)
                
        handler = self.handlers.get(topic, None)
        dprint('Handler: ', handler)
        if handler is not None:
            handler(msg)
        
    def subscribe_all(self):
        assert self.is_connected is True
        for topic in self.handlers.keys():
            self.client.subscribe(topic)
        
    def check_msg(self):
        assert self.is_connected is True
        return self.client.check_msg()
    
    def ping(self):
        self.client.ping()
        
    def publish(self, topic, msg, retain = True):
        if isinstance(msg, dict):
            msg = json.dumps(msg)
        elif not isinstance(msg, bytes):
            msg = msg.encode()
        self.client.publish(topic, msg, retain = retain)
    
    def disconnect(self):
        if self.client is not None and self.is_connected:
            self.client.disconnect()
            self.is_connected = False
#         if self.device is not None:
#             self.device.disconnect()
# 

    def _set_config(self):
        try:
            data = None
            with open('mqtt.json', 'rb') as f:
                data = f.read()
            data = json.loads(data)
            self.host = data.get('host', '127.0.0.1')
            self.usr = data.get('username', None)
            self.pwd = data.get('password', None)
            self.port = data.get('port', 1883)
        except OSError:
            print('Unable to access mqtt.json')
        except ValueError:
            print('Ensure JSON file is in correct JSON')
    
    def add_topic(self, topic):
        if not isinstance(topic, bytes):
            topic = topic.encode()
        def topic_function(func):
            self.handlers[topic] = func
            return func
        return topic_function
    
   
    def run(self):
        assert self.client is not None
        dprint('Start MQTT run')
        
        # Connect to MQTT Server
        if not self.connect():
            print('Something wrong if connecting to server...')
            raise Exception('MQTTConnection Error')
        
        # Start by publishing all states to MQTTClient
        self._publish_all()
        
        print('Started as a MQTT Device. Waiting for message...')
        while True:
            if self.restart:
                break
            self.poll()
            self.check_msg()
            time.sleep_ms(100)
        
        # Reset restart status
        self.restart = False
        self.disconnect()
        return 0
    
    def _publish_all(self):
        """
        Publish all device states according to the topics first.
        Used in init.
        """
        
        gc.collect()
        self._send_network_details()
        
        gc.collect()
        for idx, pin in enumerate(self.device.din):
            value = pin.value()
            data = json.dumps({'value': value})
            self.previous_din[idx] = value
            self.publish(f'digital_input/IN{idx+1}/state', data)
        
        gc.collect()
        for idx, pin in enumerate(self.device.dout):
            data = json.dumps({'value': pin.value()})
            self.publish(f'digital_output/RY{idx+1}/state', data)
        
        gc.collect() 
        data = json.dumps(self.device.read_ain_all())
        self.publish('analog_input/state', data)
        
        gc.collect()
        for idx, pin in enumerate(self.device.analog_out):
            data = json.dumps({"voltage": 0})
            self.publish(f'analog_output/AO{idx}/state', data)
        
        gc.collect()
        data = json.dumps({"Mode": "RS485"})
        self.publish(f'serial_mode', data)
        
        

    # Polling update   
    def poll(self):
        self._poll_uart()
        self._poll_din()
        self._poll_ain()
        self._poll_loadcell()
        self._poll_temp()
    
    def _poll_uart(self):
        data = self.device.recv_uart_message()
        if data is not None:
            self.client.publish("uart/read", data)
    
    def _poll_din(self):
        for idx, _ in enumerate(self.device.din):
            previous_value = self.previous_din[idx]
            current_value = self.device.read_din_at(idx)
            if previous_value != current_value:
                self.publish(f'digital_input/IN{idx+1}/state', json.dumps({'value': current_value}))
                self.previous_din[idx] = current_value
    
    def _poll_ain(self):
        publish = False
        avg_readings = None
        
        for idx, readings in enumerate(self.analog_input_readings):
            a = self.device.read_ain_at(idx)
            readings.append(a)
            if len(readings) >= 10:
                publish = True
            
        if publish:
            data = {}
            for i, _ in enumerate(self.device.analog_in):
                readings = self.analog_input_readings[i]
                key = f'AI{i}'
                
                # Set upper-bound for analog inputs
                if self.device.analog_in[i][1] == ADC_MODE_VOLTAGE:
                    max_value = 10
                else:
                    max_value = 20
                
                data[key] = {}
                data[key]["value"] = min(max_value, sum(readings) / len(readings))
                data[key]["mode"] = self.device.analog_in[i][1] 
                readings.clear()
            self.publish('analog_input/state', json.dumps(data))
    
    
    def _poll_loadcell(self):
        if not self.device.loadcell:
            return
        
        # Take average instead of each reading        
        self.weight_readings.append(self.device.get_loadcell_in_kg())
        if len(self.weight_readings) > 16:
            weight = sum(w for w in self.weight_readings) / len(self.weight_readings)
            self.publish('loadcell/weight', str(weight))
            self.weight_readings.pop(0)
            
    def _poll_temp(self):
        if not self.device.temp:
            return
        
        temp_list = self.device.poll_temperatures()
        if temp_list is None:
            # Not Ready
            return
        self.publish('temp/k1', str(temp_list[0]))
        self.publish('temp/k2', str(temp_list[1]))
             
    # Network
    def _send_network_details(self):
        assert self.nic is not None
        ip, mask, gw, dns = self.nic.ifconfig()
        dhcp = self.is_dhcp
        data = json.dumps({
            'ip': ip,
            'mask': mask,
            'gateway': gw,
            'dns': dns,
            'dhcp': dhcp
        })
        self.publish('network_config/state', data)
        

        
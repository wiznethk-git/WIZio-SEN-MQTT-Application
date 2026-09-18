import gc
import re
import json
import socket
import time
from setup import SetUpWiznetChip
from debug import dprint
from device import Device
from ws import RTUWebSocketServer

# from config import MQTT_ENABLED
# if MQTT_ENABLED:
#     from device_mqtt import DeviceMQTT

REQUEST_BUFFER_SIZE = const(1024)
STATIC_PATHS = {
    "/js/"	: "text/javascript",
    "/css/"	: "text/css",
    "/img/"	: "image/#",
}

class Server:
    def __init__(self, port = 80, blocking = False):
        self._nic = None
        self._is_dhcp = False
        self._sock = None
        self._routes = {}
        
        # Add a favicon route
        self._routes[("GET", "/favicon.ico")] = self._favicon_response
        
        self.port = port
        self.blocking = False
        self.task = []
        self.device = Device()
        
        # Websocket
        self.ws = None
        self.mqtt_client = None
    
    @property
    def ip_addr(self):
        if self._nic is not None:
            return self._nic.ifconfig()[0]
        return None
    
    @property
    def subnet_mask(self):
        if self._nic is not None:
            return self._nic.ifconfig()[1]
        return None    
    
    @property
    def default_gateway(self):
        if self._nic is not None:
           return self._nic.ifconfig()[2]         
        return None
    
    @property
    def dns(self):
        if self._nic is not None:
           return self._nic.ifconfig()[3]
        return None
    
    @property
    def mac_addr(self):
        if self._nic is not None:
            mac = self._nic.config('mac')
            return ':'.join(['%02x' % b for b in mac])
        return None
    
    @property
    def is_dhcp(self):
        return self._is_dhcp
    
    def nic_config(self):
        return self._nic.ifconfig()
    
    def _create_sock(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind((self.ip_addr, self.port))
        self._sock.listen(10)
        self._sock.setblocking(self.blocking)
        
#         self._sock.settimeout(1) # 1 sec timeout
    
    def init_network(self, config = None, use_dhcp = True):
        if self._sock is not None:
            self.disconnect()
        if not config:
            self._nic, self._is_dhcp = SetUpWiznetChip()
        else:
            self._nic, self._is_dhcp = SetUpWiznetChip(config, use_dhcp)
        self._create_sock()
        self._init_ws()
#         if MQTT_ENABLED:
#             self.mqtt_client = DeviceMQTT(self)
#             if not self.mqtt_client.connect():
#                 print('MQTTClient failed. Defaults back to None')
#                 self.mqtt_client = None
        print(f'Welcome to MicroPython WIZ-RTU. Visit http://{self.ip_addr}:{self.port} to configure your board.')
    
    def _init_ws(self):
        if self.ws is not None:
            self.ws.stop()
        self.ws = RTUWebSocketServer(ip = self.ip_addr)
        self.ws.start(port = 8080)
    
    def run(self):
        self.init_network()

        while True:
            
            # Close loop is socket closed
            if self._sock is None:
                break
            
            # WS Polling
            if self.ws:
                self.ws.process_all()
                self.ws.broadcast(self.device)
            
            # Connect to client
            # Idk why but keep making sock non-blocking
            self._sock.setblocking(False)
            try:
                client, client_ip = self._sock.accept()
            except OSError as e:
                if e.errno in [11, 116]: # Non-blocking, Timeout
                    time.sleep_ms(10)
                    continue
                if e.errno in [128]:
                    dprint('Socket may have been corrupted. Restart now.')
                    break
    
            if not client_ip or not client:
                continue
            dprint(f'Accepted connection from {client_ip}.')

            # Get headers
            gc.collect()
            headers = self.read_header(client)
            response = None
            if headers == -1:
                response = self.response(data = 'Header too large')
            elif headers is not None:
                gc.collect()
                body = self.read_body(client, headers)
                response = self.handle_request(headers, body)
            # Send response
            if response:
                self._send_response(client, response[0])
                self._send_response(client, response[1])
            try:
                client.close()
                client = None
                client_ip = None
            except:
                # Maybe Client is closed before hand
                pass
            self._run_pending_tasks()
    
    def _run_pending_tasks(self):
        while len(self.task) > 0:
            task, args = self.task.pop()
            task(*args)
            gc.collect()
            
    def read_header(self, client):
        chunk = 2048
        max_header = 8196
        header = bytearray()
        try:
            while True:  
                data = client.readline()
                if data is None:
                    # Timeout
                    return None

                if data == b'\r\n':
                    # Reached end of header
                    header.extend(data)
                    return header
                
                header.extend(data) 
                if len(header) > max_header:
                    # Header too large
                    return -1
        except OSError as e:
            dprint('Socket closed')
            return None
          
    def read_body(self, client, headers):
        lines = headers.split(b"\r\n")
        body_size = 0
        for idx, l in enumerate(lines):
            if l.startswith(b'Content-Length:'):
                body_size = int(l.split(b':')[1].strip())
        if body_size <= 0:
            return b''
        body = bytearray(body_size)
        mv = memoryview(body)
        head = 0
        try:
            while head < body_size:
                num_bytes = client.readinto(mv[head:])
                if num_bytes:
                    head += num_bytes
            return body
        except OSError:
            dprint('Socket closed.')
            return None
    
    # =========== Request ==================
    def add_route(self, method = 'GET', path = '/'):
        method = method.upper()
        def decorator(func):
            self._routes[(method, path)] = func
            return func
        return decorator

    def get_request(self, buffer_length=4096):
        """ Return request body """
        return str(self._connect.recv(buffer_length), "utf8")

    
    def handle_request(self, header, body):
        header, body = header.decode('utf-8'), body.decode('utf-8')
        dprint("===== Header =====\n", header)
        dprint("===== Body ====\n", body)
        
        # Get first line (METHOD /path HTTP/1.1)
        rline , _ = header.split('\r\n', 1)
        
        # Split spaces to get method and path
        method, path = rline.split(' ')[0:2]
        
        print(f'Received method {method} at {path}')
        response = self._route_request(method, path, body)
        return response
        
        
    def _route_request(self, method = 'GET', path = '/', request = None):
        
        # Normalize path.
        path = re.sub(r'/+', '/', path)
        
        # Handle JS and CSS file path.
        dprint('Static path called: ', self._is_static_path(path))
        if self._is_static_path(path):
            return self._static_response(path)
        
        handler = self._routes.get((method, path))
        if handler:
            return handler(request)
        else:
            return self.error_response()
    
    # =========== Response ===========
    def response(self, content_type = 'text/plain', data = '404 Not Found' , status = '200 OK'):
        if not isinstance(status, bytes):
            status = status.encode()
        
        if not isinstance(content_type, bytes):
            content_type = content_type.encode()
        
        body = data
        if data and not isinstance(data, (bytes, bytearray)):
            body = data.encode()
        
        # Change Table Of Content for CAN/Serial
        if data and content_type == b'text/html':
            body = self._fix_html_index(body)
        
        if data:
            num_bytes_body = str(len(data)).encode()
        else:
            num_bytes_body = "0".encode()
        
        header = b''
        def format_response(d):
            return d + b'\r\n'
        header += format_response(b'HTTP/1.1 ' + status)
        header += format_response(b'Content-Type: ' + content_type)
        header += format_response(b'Connection: Close') 
        header += format_response(b"Content-Length: " + num_bytes_body)
        
        header += b'\r\n'
        return (header, body)
    
    def _send_response(self, client, response):
        gc.collect()
        head = 0
        write_chunk = 2048
        size = len(response)
        try:
            while head < size:
                tail = min(head + write_chunk, size)
            
                mv = memoryview(response)[head:tail]
                bytes_written = client.write(mv)
                dprint('Bytes written to response: ', bytes_written)
                if bytes_written is not None:
                    head += bytes_written
        
        except OSError:
            print('Socket closed')
            return None
    
        
    def _is_static_path(self, path):
        # Request always starts with leading slash
        for prefix in STATIC_PATHS.keys():
            if path.startswith(prefix):
                return True
        return False
    
    def error_response(self):
        return self.response(status = b'404 Not Found')
            
    def _static_response(self, path):
        for prefix in STATIC_PATHS.keys():
            if path.startswith(prefix):
                content_type = STATIC_PATHS[prefix]
                
                # Special handling wildcard type such as image/#
                if content_type.endswith('#'):
                    fn, ext = path.split('/')[-1].split('.')
                    content_type = content_type[:-1] + ext
        data = b''
        path = path[1:]
        
        try:
            with open(path, 'rb') as f:
                data += f.read()
            return self.response(content_type, data)
        except OSError:
            return self.error_response()
    
    def _favicon_response(self , _):
        content_type = "image/x-icon";
        path = "img/favicon.ico"
        data = b''
        try:
            with open(path, 'rb') as f:
                data += f.read()
            return self.response(content_type, data)
        except OSError:
            return self.error_response()
    
    def _fix_html_index(self, html):
        # Allow Serial page to show
        if self.device.can_bus is None:
            dprint("Hide CAN_BUS")
            _html = html.replace(b'<a href = "/can" class = "index-item">', b'<a href = "/can" class = "index-item hidden">' )
        else:
            dprint("Hide Serial_BUS")
            _html = html.replace(b'<a href = "/serial" class = "index-item">', b'<a href = "/serial" class = "index-item hidden">' ) 
        del html
        return _html
    
    def disconnect(self):
        # Disconnect other client or server first.
#         if self.mqtt_client and self.mqtt_client.is_connected:
#             self.mqtt_client.disconnect()
        try:   
            self.ws.stop()
            self.ws = None
            
            self._sock.close()
            self._sock = None
        except Exception as e:
            raise e
    
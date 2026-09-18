import tkinter as tk
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText
import paho.mqtt.client as mqtt
import time
import json
import ast
import queue
import math
try:
    from config import USERNAME, PASSWORD
except ImportError:
    print('Cannot import configurations. Use USERNAME and PASSWORD as None instead.')
    USERNAME, PASSWORD = None, None

class DeviceDashboard:
    def __init__(self, root):
        self.root = root
        self.root.title("W55MH32 MQTT Dashboard")
        self.root.geometry("1520x850")
        self.root.configure(bg="#111827")
        self.root.resizable(True, True)

        # -------------------------------------------------
        # MQTT connection configuration
        # Kept from original dashboard.
        # -------------------------------------------------
        self.broker_ip = tk.StringVar(value="127.0.0.1")
        self.broker_port = tk.StringVar(value="1883")
        self.client_id = tk.StringVar(value=f"pc_dashboard_{int(time.time())}")
        self.username = USERNAME
        self.password =  PASSWORD

        # -------------------------------------------------
        # MQTT topic configuration
        # -------------------------------------------------
        # Use separate set/state topics so this dashboard does not receive
        # its own command messages after publishing.
        self.topic_network_config_set = tk.StringVar(value="network_config/set")
        self.topic_network_config_state = tk.StringVar(value="network_config/state")
        self.topic_digital_output_set = tk.StringVar(value="digital_output/{pin}/set")
        self.topic_digital_output_state = tk.StringVar(value="digital_output/{pin}/state")
        self.topic_digital_input_state = tk.StringVar(value="digital_input/{pin}/state")
        self.topic_analog_input_state = tk.StringVar(value="analog_input/state")
        self.topic_eeprom_write = tk.StringVar(value="eeprom/write")
        self.topic_eeprom_wipe = tk.StringVar(value="eeprom/wipe")
        self.topic_eeprom_read_request = tk.StringVar(value="eeprom/read/request")
        self.topic_eeprom_read_response = tk.StringVar(value="eeprom/read/response")
        self.topic_uart_write = tk.StringVar(value="uart/write")
        self.topic_uart_read = tk.StringVar(value="uart/read")
        self.topic_loadcell_weight = tk.StringVar(value="loadcell/weight")
        self.topic_temperature_k1 = tk.StringVar(value="temp/k1")
        self.topic_temperature_k2 = tk.StringVar(value="temp/k2")

        # -------------------------------------------------
        # Runtime state
        # -------------------------------------------------
        self.connected = False
        self.client = None
        self.closing = False
        self.disconnect_requested = False
        self.gui_queue = queue.Queue()

        self.last_topic = "--"
        self.last_message = "--"

        # Network configuration state
        self.net_ip = tk.StringVar(value="")
        self.net_mask = tk.StringVar(value="")
        self.net_gateway = tk.StringVar(value="")
        self.net_dns = tk.StringVar(value="")
        self.net_mode = tk.StringVar(value="dhcp")  # "static" or "dhcp"

        # Digital I/O state
        self.output_states = {f"RY{i}": False for i in range(1, 3)}
        self.input_states = {f"IN{i}": False for i in range(1, 3)}
        self.output_buttons = {}
        self.input_labels = {}

        # Analog I/O state
        self.analog_input_states = {f"AI{i}": 0.0 for i in range(2)}
        # AI0 and AI1 default to current mode.
        # mode 0 = voltage mode, mode 1 = current mode.
        self.analog_input_modes = {
            "AI0": 1,
            "AI1": 1,
        }
        self.analog_input_labels = {}
        self.analog_input_mode_labels = {}

        # EEPROM state
        self.eeprom_max_write_bytes = 1000
        self.eeprom_last_read_bytes = b""

        # UART state
        self.uart_last_read_bytes = b""

        # Load cell state. MQTT supplies a numeric string in kilograms.
        self.loadcell_weight_kg = 0.0
        self.loadcell_unit = tk.StringVar(value="kg")

        # Temperature state. MQTT supplies the calculated temperature in °C.
        self.temperature_values = {"K1": 0.0, "K2": 0.0}
        self.temperature_progress_bars = {}
        self.temperature_value_labels = {}

        # -------------------------------------------------
        # Build UI
        # -------------------------------------------------
        self.setup_styles()
        self.build_header()
        self.build_main_tabs()

        self.create_mqtt_client()
        self.root.after(50, self.process_gui_queue)
        self.root.after(100, self.init_status_widgets)
        self.update_clock()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # =====================================================
    # MQTT
    # =====================================================
    def create_mqtt_client(self):
        self.client = mqtt.Client(
            client_id=self.client_id.get(),
            protocol=mqtt.MQTTv311
        )
        self.client.username_pw_set(
            username=self.username,
            password=self.password,
        )
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_disconnect = self.on_disconnect

    def connect_mqtt(self):
        if self.closing:
            return
        if self.connected:
            self.write_log("Already connected to broker")
            return

        try:
            broker = self.broker_ip.get().strip()
            port = int(self.broker_port.get().strip())

            self.create_mqtt_client()

            self.write_log(f"Connecting to broker {broker}:{port}")
            self.client.connect(broker, port, 60)
            self.client.loop_start()

        except Exception as e:
            messagebox.showerror("Connection Error", str(e))
            self.write_log(f"Connection error: {e}")

    def disconnect_mqtt(self):
        if self.closing:
            return

        if self.client is None:
            return

        if not self.connected:
            self.write_log("Already disconnected")
            return

        try:
            self.disconnect_requested = True
            self.write_log("Disconnecting from MQTT broker...")
            self.client.disconnect()
        except Exception as e:
            self.disconnect_requested = False
            self.write_log(f"Disconnect error: {e}")

    def schedule_gui_update(self, callback):
        if self.closing:
            return

        self.gui_queue.put(callback)

    def process_gui_queue(self):
        if self.closing:
            return

        try:
            while True:
                callback = self.gui_queue.get_nowait()
                try:
                    callback()
                except tk.TclError:
                    pass
                except Exception as e:
                    print("GUI callback error:", e)
        except queue.Empty:
            pass

        try:
            self.root.after(50, self.process_gui_queue)
        except tk.TclError:
            pass

    def on_connect(self, client, userdata, flags, rc):
        if self.closing:
            return

        def gui_update():
            if self.closing:
                return
            if rc == 0:
                self.connected = True
                self.update_connection_badge("CONNECTED", "#10B981")
                self.connection_value.config(text="Connected to MQTT broker")
                self.write_log("Connected to MQTT broker successfully")
                self.subscribe_configured_topics()
            else:
                self.connected = False
                self.update_connection_badge(f"FAILED rc={rc}", "#F59E0B")
                self.connection_value.config(text=f"Connect failed rc={rc}")
                self.write_log(f"MQTT connect failed, rc={rc}")

        self.schedule_gui_update(gui_update)

    def on_disconnect(self, client, userdata, rc):
        if self.closing:
            return

        def gui_update():
            if self.closing:
                return

            self.connected = False
            self.update_connection_badge("DISCONNECTED", "#EF4444")
            self.connection_value.config(text="Disconnected")

            if rc == 0:
                self.write_log("Disconnected from MQTT broker")
            else:
                self.write_log(f"Unexpected MQTT disconnection, rc={rc}")

            if self.disconnect_requested:
                self.disconnect_requested = False
                try:
                    client.loop_stop()
                except Exception as e:
                    self.write_log(f"MQTT loop stop error: {e}")

        self.schedule_gui_update(gui_update)

    def subscribe_configured_topics(self):
        if self.closing or not self.connected or self.client is None:
            return

        # Subscribe only to state/feedback topics.
        # Do not subscribe to /set topics, otherwise the dashboard receives
        # its own command payload immediately after publishing.
        topics = [
            self.topic_network_config_state.get().strip(),
            self.topic_pattern_to_subscribe_topic(self.topic_digital_input_state.get().strip()),
            self.topic_pattern_to_subscribe_topic(self.topic_digital_output_state.get().strip()),
            self.topic_analog_input_state.get().strip(),
            self.topic_eeprom_read_response.get().strip(),
            self.topic_uart_read.get().strip(),
            self.topic_loadcell_weight.get().strip(),
            self.topic_temperature_k1.get().strip(),
            self.topic_temperature_k2.get().strip(),
        ]

        seen = set()
        for topic in topics:
            if topic and topic not in seen:
                self.client.subscribe(topic)
                self.write_log(f"Subscribed to [{topic}]")
                seen.add(topic)

    def apply_topics(self):
        if self.closing:
            return
        if not self.connected:
            messagebox.showinfo("Not Connected", "Topics were saved locally. Connect to MQTT to subscribe.")
            self.write_log("Topic values updated locally")
            return

        self.subscribe_configured_topics()
        messagebox.showinfo("Topics Applied", "Subscribed to the configured topics.")

    def on_message(self, client, userdata, msg):
        if self.closing:
            return

        raw_payload = msg.payload
        decoded_payload = raw_payload.decode("utf-8", errors="replace")
        payload = decoded_payload.strip()
        topic = msg.topic.strip()

        def gui_update():
            if self.closing:
                return

            self.last_topic = topic
            self.last_message = decoded_payload

            if hasattr(self, "last_topic_value"):
                self.last_topic_value.config(text=topic)
            if hasattr(self, "last_message_value"):
                self.last_message_value.config(text=decoded_payload)

            preview = decoded_payload.replace("\n", "\\n")
            if len(preview) > 300:
                preview = preview[:300] + "..."
            self.write_log(f"Received [{topic}] {len(raw_payload)} bytes -> {preview}")

            digital_input_pin = self.match_pin_topic(self.topic_digital_input_state.get().strip(), topic)
            digital_output_pin = self.match_pin_topic(self.topic_digital_output_state.get().strip(), topic)

            if topic == self.topic_network_config_state.get().strip():
                self.handle_network_config_payload(payload)
            elif digital_input_pin in self.input_states:
                self.handle_digital_input_payload(payload, pin=digital_input_pin)
            elif digital_output_pin in self.output_states:
                self.handle_digital_output_payload(payload, pin=digital_output_pin)
            elif topic == self.topic_analog_input_state.get().strip():
                self.handle_analog_input_payload(payload)
            elif topic == self.topic_eeprom_read_response.get().strip():
                self.handle_eeprom_read_response_payload(raw_payload)
            elif topic == self.topic_uart_read.get().strip():
                self.handle_uart_read_payload(raw_payload)
            elif topic == self.topic_loadcell_weight.get().strip():
                self.handle_loadcell_weight_payload(payload)
            elif topic == self.topic_temperature_k1.get().strip():
                self.handle_temperature_payload("K1", payload)
            elif topic == self.topic_temperature_k2.get().strip():
                self.handle_temperature_payload("K2", payload)

        self.schedule_gui_update(gui_update)

    def publish_message(self, topic, payload):
        if self.closing:
            return False
        if not self.connected:
            messagebox.showwarning("Not Connected", "Please connect to the MQTT broker first.")
            self.write_log("Publish failed: broker not connected")
            return False

        try:
            self.client.publish(topic, payload)
            if isinstance(payload, (bytes, bytearray)):
                preview = bytes(payload[:120]).decode("utf-8", errors="replace")
                if len(payload) > 120:
                    preview += "..."
                self.write_log(f"Published [{topic}] {len(payload)} bytes -> {preview}")
            else:
                self.write_log(f"Published [{topic}] -> {payload}")
            return True
        except Exception as e:
            self.write_log(f"Publish error: {e}")
            return False

    # =====================================================
    # Payload helpers
    # =====================================================
    def parse_mapping_payload(self, payload):
        """
        Accept normal JSON first:
            {"RY1": true}
        Also accept Python-style dict strings during testing:
            {'RY1': True}
        """
        try:
            obj = json.loads(payload)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass

        try:
            obj = ast.literal_eval(payload)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass

        self.write_log(f"Invalid mapping payload: {payload}")
        return None

    def bool_from_value(self, value):
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value != 0
        if isinstance(value, str):
            upper = value.strip().upper()
            if upper in ("1", "TRUE", "ON", "HIGH", "YES"):
                return True
            if upper in ("0", "FALSE", "OFF", "LOW", "NO"):
                return False
        return bool(value)

    def float_from_value(self, value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def int_from_value(self, value, default=0):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def topic_pattern_to_subscribe_topic(self, pattern):
        """
        Dashboard configuration uses {pin} to make per-pin topics readable.
        MQTT subscribe needs + as the single-level wildcard.
        """
        return pattern.replace("{pin}", "+")

    def topic_pattern_with_pin(self, pattern, pin):
        """
        Convert a configured per-pin pattern into a real publish topic.
        Supports either digital_output/{pin}/set or digital_output/+/set.
        """
        return pattern.replace("{pin}", pin).replace("+", pin)

    def match_pin_topic(self, pattern, topic):
        """
        Match topics such as:
            digital_input/{pin}/state
            digital_input/+/state

        Returns the captured pin string when matched, otherwise None.
        """
        pattern = self.topic_pattern_to_subscribe_topic(pattern)
        pattern_parts = pattern.split("/")
        topic_parts = topic.split("/")

        if len(pattern_parts) != len(topic_parts):
            return None

        captured_pin = None
        for pattern_part, topic_part in zip(pattern_parts, topic_parts):
            if pattern_part == "+":
                captured_pin = topic_part.upper()
                continue
            if pattern_part != topic_part:
                return None

        return captured_pin

    # =====================================================
    # Network configuration
    # =====================================================
    def handle_network_config_payload(self, payload):
        data = self.parse_mapping_payload(payload)
        if data is None:
            return

        if "ip" in data:
            self.net_ip.set(str(data.get("ip", "")))
        if "mask" in data:
            self.net_mask.set(str(data.get("mask", "")))
        if "gateway" in data:
            self.net_gateway.set(str(data.get("gateway", "")))
        if "dns" in data:
            self.net_dns.set(str(data.get("dns", "")))
        if "dhcp" in data:
            self.net_mode.set("dhcp" if self.bool_from_value(data.get("dhcp")) else "static")

        self.write_log("Network configuration UI updated from MQTT payload")

    def publish_network_config(self):
        payload_obj = {
            "ip": self.net_ip.get().strip(),
            "mask": self.net_mask.get().strip(),
            "gateway": self.net_gateway.get().strip(),
            "dns": self.net_dns.get().strip(),
            "dhcp": self.net_mode.get() == "dhcp",
        }
        payload = json.dumps(payload_obj)
        self.publish_message(self.topic_network_config_set.get().strip(), payload)

    # =====================================================
    # Digital input/output
    # =====================================================
    def handle_digital_input_payload(self, payload, pin=None):
        data = self.parse_mapping_payload(payload)
        if data is None:
            return

        # Preferred per-pin payload:
        #     topic: digital_input/IN1/state
        #     payload: {"value": 0}
        if pin is not None:
            pin = str(pin).upper()
            if pin in self.input_states and "value" in data:
                self.input_states[pin] = self.bool_from_value(data.get("value"))
                self.update_input_widgets()
            return

        # Backward-compatible grouped payload:
        #     {"IN1": true, "IN2": false}
        for key, value in data.items():
            key = str(key).upper()
            if key in self.input_states:
                self.input_states[key] = self.bool_from_value(value)

        self.update_input_widgets()

    def handle_digital_output_payload(self, payload, pin=None):
        data = self.parse_mapping_payload(payload)
        if data is None:
            return

        # Preferred per-pin payload:
        #     topic: digital_output/RY1/state
        #     payload: {"value": 1}
        if pin is not None:
            pin = str(pin).upper()
            if pin in self.output_states and "value" in data:
                self.output_states[pin] = self.bool_from_value(data.get("value"))
                self.update_output_widgets()
            return

        # Backward-compatible single/grouped payloads:
        #     {"pin": "RY1", "value": true}
        #     {"RY1": true, "RY2": false}
        if "pin" in data and "value" in data:
            output_pin = str(data.get("pin", "")).upper()
            if output_pin in self.output_states:
                self.output_states[output_pin] = self.bool_from_value(data.get("value"))
        else:
            for key, value in data.items():
                key = str(key).upper()
                if key in self.output_states:
                    self.output_states[key] = self.bool_from_value(value)

        self.update_output_widgets()

    def publish_output_state(self, output_name):
        output_name = output_name.upper()
        if output_name not in self.output_states:
            return

        new_state = not self.output_states[output_name]
        payload = json.dumps({
            "value": 1 if new_state else 0,
        })

        topic_pattern = self.topic_digital_output_set.get().strip()
        topic = self.topic_pattern_with_pin(topic_pattern, output_name)

        # Do not update the relay button optimistically here.
        # The button state is updated only when digital_output/<pin>/state arrives.
        self.publish_message(topic, payload)

    # =====================================================
    # Analog input
    # =====================================================
    def handle_analog_input_payload(self, payload):
        data = self.parse_mapping_payload(payload)
        if data is None:
            return

        self.update_analog_input_state_from_payload(data)
        self.update_analog_input_widgets()

    def update_analog_input_state_from_payload(self, data):
        """
        Preferred analog input payload:
            {"0": {"value": 1.23, "mode": 0}, "1": {"value": 0.012, "mode": 1}}

        The top-level key is the analog input index:
            "0" -> AI0, "1" -> AI1, etc.

        mode:
            0 -> voltage mode, display V
            1 -> current mode, display A

        If mode is missing for a channel, keep the previous mode and only update
        the value. This also keeps compatibility with older payload formats.
        """
        # Single-channel format support:
        #     {"pin": "AI0", "value": 1.23, "mode": 0}
        #     {"pin": "AI0", "voltage": 1.23}
        if "pin" in data:
            pin = str(data.get("pin", "")).upper()
            if pin in self.analog_input_states:
                value = self.float_from_value(data.get("value", data.get("voltage")))
                if value is not None:
                    self.analog_input_states[pin] = value

                if "mode" in data:
                    mode = self.int_mode_from_value(data.get("mode"))
                    if mode is not None:
                        self.analog_input_modes[pin] = mode
            return

        for key, item in data.items():
            pin = self.analog_input_pin_from_key(key)
            if pin not in self.analog_input_states:
                continue

            if isinstance(item, dict):
                value = self.float_from_value(item.get("value", item.get("voltage")))
                if value is not None:
                    self.analog_input_states[pin] = value

                # Only change the visual mode if mode is provided.
                if "mode" in item:
                    mode = self.int_mode_from_value(item.get("mode"))
                    if mode is not None:
                        self.analog_input_modes[pin] = mode
            else:
                # Older grouped payload support:
                #     {"AI0": 1.23, "AI1": 2.34}
                value = self.float_from_value(item)
                if value is not None:
                    self.analog_input_states[pin] = value

    def analog_input_pin_from_key(self, key):
        key = str(key).upper().strip()

        if key.startswith("AI"):
            return key

        try:
            return "AI{}".format(int(key))
        except ValueError:
            return key

    def int_mode_from_value(self, value):
        try:
            mode = int(value)
        except (TypeError, ValueError):
            return None

        if mode in (0, 1):
            return mode
        return None

    # =====================================================
    # EEPROM
    # =====================================================
    def update_eeprom_write_count(self, _event=None):
        if not hasattr(self, "eeprom_write_box"):
            return

        text = self.eeprom_write_box.get("1.0", "end-1c")
        byte_count = len(text.encode("utf-8"))
        color = "#F87171" if byte_count > self.eeprom_max_write_bytes else "#9CA3AF"
        self.eeprom_write_count_label.config(
            text=f"{byte_count}/{self.eeprom_max_write_bytes} bytes UTF-8",
            fg=color,
        )

    def publish_eeprom_write(self):
        if not hasattr(self, "eeprom_write_box"):
            return

        text = self.eeprom_write_box.get("1.0", "end-1c")
        data = text.encode("utf-8")

        if len(data) > self.eeprom_max_write_bytes:
            messagebox.showwarning(
                "EEPROM Write Too Large",
                f"EEPROM write allows only {self.eeprom_max_write_bytes} bytes each time. Current payload is {len(data)} bytes.",
            )
            self.write_log(f"EEPROM write blocked: {len(data)} bytes exceeds limit")
            return

        topic = self.topic_eeprom_write.get().strip()
        if self.publish_message(topic, data):
            self.write_log(f"EEPROM write request sent to [{topic}] with {len(data)} bytes")

    def publish_eeprom_wipe(self):
        topic = self.topic_eeprom_wipe.get().strip()
        if not topic:
            messagebox.showwarning("Missing Topic", "EEPROM wipe topic is empty.")
            return

        if not messagebox.askyesno("Confirm EEPROM Wipe", "Publish True to eeprom/wipe and wipe EEPROM on the device?"):
            return

        if self.publish_message(topic, "True"):
            self.write_log(f"EEPROM wipe request sent to [{topic}] -> True")

    def request_eeprom_read(self):
        # MQTT does not have HTTP-style fetch. This publishes a request message.
        # The device should subscribe to eeprom/read/request, read EEPROM at the
        # requested index, then publish the result to eeprom/read/response.
        try:
            index = int(self.eeprom_read_index_var.get().strip())
        except ValueError:
            messagebox.showwarning("Invalid EEPROM Index", "Enter an integer EEPROM read index.")
            return

        payload = json.dumps({"index": index})
        topic = self.topic_eeprom_read_request.get().strip()
        if self.publish_message(topic, payload):
            self.write_log(f"EEPROM read request sent to [{topic}] -> {payload}")

    def handle_eeprom_read_response_payload(self, raw_payload):
        self.eeprom_last_read_bytes = bytes(raw_payload)
        decoded = raw_payload.decode("utf-8", errors="replace")

        # Keep the returned string visible even if the EEPROM contains odd bytes.
        # Invalid UTF-8 bytes are shown with replacement characters, not discarded.
        if hasattr(self, "eeprom_read_box"):
            self.eeprom_read_box.config(state="normal")
            self.eeprom_read_box.delete("1.0", "end")
            self.eeprom_read_box.insert("1.0", decoded)
            self.eeprom_read_box.config(state="disabled")

        if hasattr(self, "eeprom_read_info_label"):
            self.eeprom_read_info_label.config(text=f"Last response: {len(raw_payload)} bytes decoded as UTF-8")

        self.write_log(f"EEPROM read response displayed: {len(raw_payload)} bytes")

    # =====================================================
    # UART
    # =====================================================
    def publish_uart_write(self):
        if not hasattr(self, "uart_write_box"):
            return

        text = self.uart_write_box.get("1.0", "end-1c")
        data = text.encode("utf-8")
        topic = self.topic_uart_write.get().strip()

        if not topic:
            messagebox.showwarning("Missing Topic", "UART write topic is empty.")
            return

        if self.publish_message(topic, data):
            self.append_uart_message("SEND", text, len(data))
            self.write_log(f"UART write sent to [{topic}] with {len(data)} bytes")

    def handle_uart_read_payload(self, raw_payload):
        self.uart_last_read_bytes = bytes(raw_payload)
        decoded = raw_payload.decode("utf-8", errors="replace")
        self.append_uart_message("RECEIVE", decoded, len(raw_payload))
        self.write_log(f"UART read displayed from device: {len(raw_payload)} bytes")

    def append_uart_message(self, direction, text, byte_count):
        if not hasattr(self, "uart_history_box"):
            return

        timestamp = time.strftime("%H:%M:%S")
        self.uart_history_box.config(state="normal")

        if direction == "SEND":
            header = f"[{timestamp}] SEND PC -> DEVICE ({byte_count} bytes)\n"
            tag = "send"
        else:
            header = f"[{timestamp}] RECEIVE DEVICE UART -> PC ({byte_count} bytes)\n"
            tag = "receive"

        self.uart_history_box.insert("end", header, tag)
        self.uart_history_box.insert("end", text + "\n")
        self.uart_history_box.insert("end", "-" * 72 + "\n")
        self.uart_history_box.config(state="disabled")
        self.uart_history_box.see("end")

    def clear_uart_history(self):
        if hasattr(self, "uart_history_box"):
            self.uart_history_box.config(state="normal")
            self.uart_history_box.delete("1.0", "end")
            self.uart_history_box.config(state="disabled")

    def clear_uart_write_box(self):
        if hasattr(self, "uart_write_box"):
            self.uart_write_box.delete("1.0", "end")


    # =====================================================
    # Load cell
    # =====================================================
    def handle_loadcell_weight_payload(self, payload):
        try:
            weight_kg = float(payload)
        except (TypeError, ValueError):
            self.write_log(
                f"Invalid load cell payload from "
                f"[{self.topic_loadcell_weight.get().strip()}]: expected a number in kg"
            )
            return

        if not math.isfinite(weight_kg):
            self.write_log(
                f"Invalid load cell payload from "
                f"[{self.topic_loadcell_weight.get().strip()}]: value must be finite"
            )
            return

        self.loadcell_weight_kg = min(max(weight_kg, 0.0), 10.0)
        self.update_loadcell_widgets()

    def update_loadcell_widgets(self):
        displayed_kg = self.loadcell_weight_kg

        if hasattr(self, "loadcell_progress"):
            self.draw_loadcell_progress()
        if hasattr(self, "loadcell_weight_label"):
            if self.loadcell_unit.get() == "g":
                self.loadcell_weight_label.config(text=f"{displayed_kg * 1000:.2f} g")
            else:
                self.loadcell_weight_label.config(text=f"{displayed_kg:.2f} kg")

        if hasattr(self, "loadcell_min_label"):
            minimum = "0.00 g" if self.loadcell_unit.get() == "g" else "0.00 kg"
            self.loadcell_min_label.config(text=minimum)
        if hasattr(self, "loadcell_max_label"):
            maximum = "10000.00 g" if self.loadcell_unit.get() == "g" else "10.00 kg"
            self.loadcell_max_label.config(text=maximum)

    def draw_loadcell_progress(self, _event=None):
        if not hasattr(self, "loadcell_progress"):
            return

        canvas = self.loadcell_progress
        canvas.delete("all")
        width = max(canvas.winfo_width(), 850)
        height = max(canvas.winfo_height(), 50)
        center_y = height / 2
        radius = 18
        start_x = radius + 4
        end_x = width - radius - 4

        # A thin outline separates the rounded track from the card.
        canvas.create_line(
            start_x, center_y, end_x, center_y,
            fill="#64748B", width=42, capstyle=tk.ROUND,
        )
        canvas.create_line(
            start_x, center_y, end_x, center_y,
            fill="#0B1220", width=34, capstyle=tk.ROUND,
        )

        fraction = min(max(self.loadcell_weight_kg / 10.0, 0.0), 1.0)
        if fraction > 0:
            progress_x = start_x + ((end_x - start_x) * fraction)
            canvas.create_line(
                start_x, center_y, progress_x, center_y,
                fill="#22C55E", width=34, capstyle=tk.ROUND,
            )

    # =====================================================
    # Temperature sensors
    # =====================================================
    def handle_temperature_payload(self, sensor, payload):
        try:
            temperature = float(payload)
        except (TypeError, ValueError):
            self.write_log(
                f"Invalid {sensor} temperature payload: expected a numeric value"
            )
            return

        if not math.isfinite(temperature):
            self.write_log(
                f"Invalid {sensor} temperature payload: value must be finite"
            )
            return

        self.temperature_values[sensor] = temperature
        self.update_temperature_widget(sensor)

    def update_temperature_widget(self, sensor):
        label = self.temperature_value_labels.get(sensor)
        if label is not None:
            label.config(text=f"{self.temperature_values[sensor]:.2f} °C")
        self.draw_temperature_progress(sensor)

    def draw_temperature_progress(self, sensor, _event=None):
        canvas = self.temperature_progress_bars.get(sensor)
        if canvas is None:
            return

        canvas.delete("all")
        width = max(canvas.winfo_width(), 720)
        height = max(canvas.winfo_height(), 50)
        center_y = height / 2
        start_x = 22
        end_x = width - 22

        canvas.create_line(
            start_x, center_y, end_x, center_y,
            fill="#64748B", width=42, capstyle=tk.ROUND,
        )
        canvas.create_line(
            start_x, center_y, end_x, center_y,
            fill="#0B1220", width=34, capstyle=tk.ROUND,
        )

        displayed_temperature = min(
            max(self.temperature_values[sensor], 0.0), 1300.0
        )
        fraction = displayed_temperature / 1300.0
        if fraction > 0:
            progress_x = start_x + ((end_x - start_x) * fraction)
            canvas.create_line(
                start_x, center_y, progress_x, center_y,
                fill="#F97316", width=34, capstyle=tk.ROUND,
            )

    # =====================================================
    # UI setup
    # =====================================================
    def setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure("TNotebook", background="#111827", borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            background="#374151",
            foreground="white",
            padding=(18, 10),
            font=("Arial", 11, "bold")
        )
        style.map(
            "TNotebook.Tab",
            background=[
                ("disabled", "#171C26"),
                ("selected", "#1F2937"),
                ("!selected", "#374151"),
            ],
            foreground=[
                ("disabled", "#6B7280"),
                ("selected", "#93C5FD"),
                ("!selected", "white"),
            ],
        )

    def build_header(self):
        header = tk.Frame(self.root, bg="#0F172A", height=72)
        header.pack(fill="x")
        header.pack_propagate(False)

        title = tk.Label(
            header,
            text="W55MH32 MQTT DEVICE DASHBOARD",
            bg="#0F172A",
            fg="#F9FAFB",
            font=("Arial", 22, "bold")
        )
        title.pack(side="left", padx=20)

        self.clock_label = tk.Label(
            header,
            text="--:--:--",
            bg="#0F172A",
            fg="#93C5FD",
            font=("Consolas", 16, "bold")
        )
        self.clock_label.pack(side="right", padx=20)

    def build_main_tabs(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=14, pady=14)

        self.tab_connection = tk.Frame(self.notebook, bg="#111827")
        self.tab_network = tk.Frame(self.notebook, bg="#111827")
        self.tab_io = tk.Frame(self.notebook, bg="#111827")
        self.tab_analog_io = tk.Frame(self.notebook, bg="#111827")
        self.tab_eeprom = tk.Frame(self.notebook, bg="#111827")
        self.tab_uart = tk.Frame(self.notebook, bg="#111827")
        self.tab_loadcell = tk.Frame(self.notebook, bg="#111827")
        self.tab_temperature = tk.Frame(self.notebook, bg="#111827")

        self.notebook.add(self.tab_connection, text="Connection")
        self.notebook.add(self.tab_network, text="Network Configurations")
        self.notebook.add(self.tab_io, text="Digital I/O")
        self.notebook.add(self.tab_analog_io, text="Analog Input")
        self.notebook.add(self.tab_eeprom, text="EEPROM")
        self.notebook.add(self.tab_uart, text="Serial")
        self.notebook.add(self.tab_loadcell, text="Load Cell")
        self.notebook.add(self.tab_temperature, text="Temperature")

        self.build_connection_tab()
        self.build_network_tab()
        self.build_io_tab()
        self.build_analog_io_tab()
        self.build_eeprom_tab()
        self.build_uart_tab()
        self.build_loadcell_tab()
        self.build_temperature_tab()

    # =====================================================
    # Tab: Connection
    # =====================================================
    def build_connection_tab(self):
        wrapper = tk.Frame(self.tab_connection, bg="#111827")
        wrapper.pack(fill="both", expand=True, padx=10, pady=10)

        # Three-column layout:
        #   1. broker settings + connection state
        #   2. scrollable topic configuration
        #   3. recent MQTT activity + logs
        left = tk.Frame(wrapper, bg="#111827", width=380)
        left.pack(side="left", fill="y", padx=(0, 14))
        left.pack_propagate(False)

        middle = tk.Frame(wrapper, bg="#111827", width=500)
        middle.pack(side="left", fill="y", padx=(0, 14))
        middle.pack_propagate(False)

        right = tk.Frame(wrapper, bg="#111827")
        right.pack(side="left", fill="both", expand=True)

        # -------------------------------------------------
        # Column 1: old settings and state
        # -------------------------------------------------
        conn_card = self.make_card(left, "BROKER SETTINGS", 360, 280)
        conn_card.pack(pady=(0, 12))

        self.make_label(conn_card, "Broker IP", 24, 60)
        self.make_entry(conn_card, self.broker_ip, 140, 58, 180)

        self.make_label(conn_card, "Port", 24, 100)
        self.make_entry(conn_card, self.broker_port, 140, 98, 180)

        self.make_label(conn_card, "Client ID", 24, 140)
        self.make_entry(conn_card, self.client_id, 140, 138, 180)

        connect_btn = tk.Button(
            conn_card,
            text="CONNECT",
            command=self.connect_mqtt,
            bg="#10B981",
            fg="white",
            relief="flat",
            font=("Arial", 12, "bold"),
            width=12,
            height=1
        )
        connect_btn.place(x=24, y=200)

        disconnect_btn = tk.Button(
            conn_card,
            text="DISCONNECT",
            command=self.disconnect_mqtt,
            bg="#EF4444",
            fg="white",
            relief="flat",
            font=("Arial", 12, "bold"),
            width=12,
            height=1
        )
        disconnect_btn.place(x=190, y=200)

        status_card = self.make_card(left, "CONNECTION STATUS", 360, 310)
        status_card.pack(pady=(0, 12))

        tk.Label(
            status_card,
            text="Broker State",
            bg="#1F2937",
            fg="#9CA3AF",
            font=("Arial", 11)
        ).place(x=24, y=62)

        self.connection_badge = tk.Label(
            status_card,
            text="DISCONNECTED",
            bg="#EF4444",
            fg="white",
            font=("Arial", 12, "bold"),
            padx=14,
            pady=7
        )
        self.connection_badge.place(x=24, y=88)

        self.connection_value = tk.Label(
            status_card,
            text="Disconnected",
            bg="#111827",
            fg="#E5E7EB",
            font=("Consolas", 12),
            anchor="w",
            padx=10
        )
        self.connection_value.place(x=24, y=128, width=300, height=28)

        topic_hint = (
            "Topic Rule:\n"
            "  Command topics use /set\n"
            "  Feedback topics use /state or /read\n\n"
            "The dashboard subscribes only to feedback topics,\n"
            "so it does not receive its own commands."
        )
        tk.Label(
            status_card,
            text=topic_hint,
            bg="#111827",
            fg="#D1D5DB",
            font=("Consolas", 9),
            justify="left",
            anchor="nw",
            padx=10,
            pady=8
        ).place(x=24, y=164, width=310, height=120)

        # -------------------------------------------------
        # Column 2: scrollable topics
        # -------------------------------------------------
        topic_card = self.make_card(middle, "TOPIC CONFIGURATION", 500, 620)
        topic_card.pack(fill="y")

        topic_rows = [
            ("Network Config Set", self.topic_network_config_set),
            ("Network Config State", self.topic_network_config_state),
            ("Digital Output Set", self.topic_digital_output_set),
            ("Digital Output State", self.topic_digital_output_state),
            ("Digital Input State", self.topic_digital_input_state),
            ("Analog Input State", self.topic_analog_input_state),
            ("EEPROM Write", self.topic_eeprom_write),
            ("EEPROM Wipe", self.topic_eeprom_wipe),
            ("EEPROM Read Request", self.topic_eeprom_read_request),
            ("EEPROM Read Response", self.topic_eeprom_read_response),
            ("UART Write", self.topic_uart_write),
            ("UART Read", self.topic_uart_read),
            ("Load Cell Weight", self.topic_loadcell_weight),
            ("Temperature K1", self.topic_temperature_k1),
            ("Temperature K2", self.topic_temperature_k2),
        ]

        canvas = tk.Canvas(
            topic_card,
            bg="#1F2937",
            highlightthickness=0,
            bd=0
        )
        scrollbar = ttk.Scrollbar(topic_card, orient="vertical", command=canvas.yview)
        scroll_frame = tk.Frame(canvas, bg="#1F2937")
        scroll_window = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")

        def _sync_scroll_region(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _resize_scroll_frame(event):
            canvas.itemconfig(scroll_window, width=event.width)

        scroll_frame.bind("<Configure>", _sync_scroll_region)
        canvas.bind("<Configure>", _resize_scroll_frame)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.place(x=18, y=55, width=444, height=438)
        scrollbar.place(x=462, y=55, height=438)

        scroll_frame.columnconfigure(0, weight=1)
        for row_idx, (label_text, variable) in enumerate(topic_rows):
            label = tk.Label(
                scroll_frame,
                text=label_text,
                bg="#1F2937",
                fg="#D1D5DB",
                font=("Arial", 11),
                anchor="w"
            )
            label.grid(row=row_idx * 2, column=0, sticky="ew", padx=(10, 16), pady=(8, 2))

            entry = tk.Entry(
                scroll_frame,
                textvariable=variable,
                bg="#111827",
                fg="#F9FAFB",
                insertbackground="white",
                relief="flat",
                font=("Consolas", 11)
            )
            entry.grid(row=row_idx * 2 + 1, column=0, sticky="ew", padx=(10, 16), pady=(0, 14), ipady=6)

        note = tk.Label(
            topic_card,
            text="Edit topic names here, then click Apply. Use {pin} for per-pin topics such as RY1/IN1.",
            bg="#1F2937",
            fg="#9CA3AF",
            font=("Arial", 10),
            justify="left",
            wraplength=430
        )
        note.place(x=28, y=510)

        apply_btn = tk.Button(
            topic_card,
            text="APPLY / SUBSCRIBE STATE TOPICS",
            command=self.apply_topics,
            bg="#2563EB",
            fg="white",
            relief="flat",
            font=("Arial", 11, "bold"),
            width=31,
            height=1
        )
        apply_btn.place(x=90, y=565)

        # -------------------------------------------------
        # Column 3: recent activity and logs
        # -------------------------------------------------
        activity_card = self.make_card(right, "RECENT MQTT ACTIVITY / LOGS", 580, 620)
        activity_card.pack(fill="both", expand=True)

        tk.Label(
            activity_card,
            text="Last Topic",
            bg="#1F2937",
            fg="#9CA3AF",
            font=("Arial", 11)
        ).place(x=28, y=58)

        self.last_topic_value = tk.Label(
            activity_card,
            text="--",
            bg="#111827",
            fg="#E5E7EB",
            font=("Consolas", 10),
            anchor="nw",
            justify="left",
            padx=10,
            pady=6,
            wraplength=390
        )
        self.last_topic_value.place(x=150, y=54, width=400, height=48)

        tk.Label(
            activity_card,
            text="Last Message",
            bg="#1F2937",
            fg="#9CA3AF",
            font=("Arial", 11)
        ).place(x=28, y=122)

        self.last_message_value = tk.Label(
            activity_card,
            text="--",
            bg="#111827",
            fg="#93C5FD",
            font=("Consolas", 10, "bold"),
            anchor="nw",
            justify="left",
            padx=10,
            pady=6,
            wraplength=390
        )
        self.last_message_value.place(x=150, y=118, width=400, height=95)

        self.log_box = ScrolledText(
            activity_card,
            bg="#0B1220",
            fg="#D1D5DB",
            insertbackground="white",
            font=("Consolas", 10),
            relief="flat",
            wrap="word"
        )
        self.log_box.place(x=24, y=240, width=528, height=342)

        clear_btn = tk.Button(
            activity_card,
            text="CLEAR LOG",
            command=lambda: self.log_box.delete("1.0", "end"),
            bg="#374151",
            fg="white",
            relief="flat",
            font=("Arial", 10, "bold"),
            width=12
        )
        clear_btn.place(x=438, y=590)

    # =====================================================
    # Tab: Network Configurations
    # =====================================================
    def build_network_tab(self):
        wrapper = tk.Frame(self.tab_network, bg="#111827")
        wrapper.pack(fill="both", expand=True, padx=10, pady=10)

        config_card = self.make_card(wrapper, "NETWORK CONFIGURATION", 720, 440)
        config_card.pack(side="left", fill="y", padx=(0, 14))

        rows = [
            ("IP Address", self.net_ip),
            ("Subnet Mask", self.net_mask),
            ("Default Gateway", self.net_gateway),
            ("DNS", self.net_dns),
        ]

        self.net_config_entries = []

        y = 78
        for label, var in rows:
            self.make_label(config_card, label, 40, y)
            entry = self.make_entry(config_card, var, 230, y - 4, 390)
            self.net_config_entries.append(entry)
            y += 64

        mode_label = tk.Label(
            config_card,
            text="Address Mode",
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Arial", 11)
        )
        mode_label.place(x=40, y=330)

        static_radio = tk.Radiobutton(
            config_card,
            text="Static",
            variable=self.net_mode,
            value="static",
            bg="#1F2937",
            fg="#F9FAFB",
            selectcolor="#111827",
            activebackground="#1F2937",
            activeforeground="#93C5FD",
            font=("Arial", 11, "bold")
        )
        static_radio.place(x=230, y=326)

        dhcp_radio = tk.Radiobutton(
            config_card,
            text="DHCP",
            variable=self.net_mode,
            value="dhcp",
            bg="#1F2937",
            fg="#F9FAFB",
            selectcolor="#111827",
            activebackground="#1F2937",
            activeforeground="#93C5FD",
            font=("Arial", 11, "bold")
        )
        dhcp_radio.place(x=340, y=326)

        self.net_mode.trace_add("write", self.update_network_mode_widgets)
        self.update_network_mode_widgets()

        publish_btn = tk.Button(
            config_card,
            text="PUBLISH NETWORK CONFIG",
            command=self.publish_network_config,
            bg="#10B981",
            fg="white",
            relief="flat",
            font=("Arial", 12, "bold"),
            width=26,
            height=2
        )
        publish_btn.place(x=230, y=374)

        info_card = self.make_card(wrapper, "NETWORK TOPIC FORMAT", 400, 440)
        info_card.pack(side="left", fill="both", expand=True)

        info = (
            "Set Topic:\n"
            "  network_config/set\n\n"
            "State Topic:\n"
            "  network_config/state\n\n"
            "Payload example:\n"
            "  {\n"
            "    \"ip\": \"10.0.1.146\",\n"
            "    \"mask\": \"255.255.255.0\",\n"
            "    \"gateway\": \"10.0.1.1\",\n"
            "    \"dns\": \"8.8.8.8\",\n"
            "    \"dhcp\": false\n"
            "  }\n\n"
            "Incoming MQTT payload on the network/state\n"
            "topic will update these text boxes\n"
            "and select Static/DHCP automatically."
        )

        tk.Label(
            info_card,
            text=info,
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Consolas", 11),
            justify="left",
            anchor="nw"
        ).place(x=28, y=65, width=345, height=340)

    # =====================================================
    # Tab: Input
    # =====================================================
    def build_io_tab(self):
        wrapper = tk.Frame(self.tab_io, bg="#111827")
        wrapper.pack(fill="both", expand=True, padx=10, pady=10)

        output_card = self.make_card(wrapper, "OUTPUT - RY1 TO RY2", 540, 620)
        output_card.pack(side="left", fill="y", padx=(0, 14))

        input_card = self.make_card(wrapper, "INPUT - IN1 TO IN2", 560, 620)
        input_card.pack(side="left", fill="both", expand=True)

        self.build_output_section(output_card)
        self.build_input_section(input_card)

    def build_output_section(self, parent):
        info = (
            "Set Topic:\n"
            "  digital_output/RYx/set\n\n"
            "State Topic:\n"
            "  digital_output/RYx/state\n\n"
            "Payload example:\n"
            "  {\"value\": 0} or {\"value\": 1}\n\n"
            "Display rule:\n"
            "  Relay button state changes only after state topic feedback."
        )
        tk.Label(
            parent,
            text=info,
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Consolas", 10),
            justify="left",
            anchor="nw"
        ).place(x=28, y=58, width=500, height=125)

        for col, name in enumerate(("RY1", "RY2")):
            btn = tk.Button(
                parent,
                text=f"{name}: OFF",
                command=lambda n=name: self.publish_output_state(n),
                bg="#374151",
                fg="white",
                relief="flat",
                font=("Arial", 15, "bold"),
                width=14,
                height=2
            )
            btn.place(x=(55, 285)[col], y=220)
            self.output_buttons[name] = btn

        self.update_output_widgets()

    def build_input_section(self, parent):
        info = (
            "State Topic:\n"
            "  digital_input/INx/state\n\n"
            "Payload example:\n"
            "  {\"value\": 0} or {\"value\": 1}\n\n"
            "Display rule:\n"
            "  Active-low input: LOW = ON, HIGH = OFF."
        )
        tk.Label(
            parent,
            text=info,
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Consolas", 10),
            justify="left",
            anchor="nw"
        ).place(x=28, y=58, width=500, height=92)

        for col, name in enumerate(("IN1", "IN2")):
            frame = tk.Frame(parent, bg="#111827", width=180, height=70)
            frame.place(x=(60, 310)[col], y=190)
            frame.pack_propagate(False)

            title = tk.Label(
                frame,
                text=name,
                bg="#111827",
                fg="#E5E7EB",
                font=("Arial", 13, "bold")
            )
            title.pack(side="left", padx=(16, 8))

            state = tk.Label(
                frame,
                text="OFF",
                bg="#374151",
                fg="white",
                font=("Arial", 13, "bold"),
                width=7,
                pady=8
            )
            state.pack(side="right", padx=(8, 16))
            self.input_labels[name] = state

        self.update_input_widgets()

    # =====================================================
    # Tab: Analog I/O
    # =====================================================
    def build_analog_io_tab(self):
        wrapper = tk.Frame(self.tab_analog_io, bg="#111827")
        wrapper.pack(fill="both", expand=True, padx=180, pady=30)

        input_card = self.make_card(wrapper, "ANALOG INPUT - AI0 TO AI1", 900, 560)
        input_card.pack(fill="both", expand=True)

        self.build_analog_input_section(input_card)

    def build_analog_input_section(self, parent):
        info = (
            "State Topic:\n"
            "  analog_input/state\n\n"
            "Payload example:\n"
            "  {\n"
            "    \"AI0\": {\"value\": 12.34, \"mode\": 1},\n"
            "    \"AI1\": {\"value\": 1.23,  \"mode\": 0}\n"
            "  }\n\n"
            "mode 0 = V, mode 1 = mA. Missing mode keeps previous mode."
        )
        tk.Label(
            parent,
            text=info,
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Consolas", 10),
            justify="left",
            anchor="nw"
        ).place(x=40, y=65, width=820, height=180)

        for col, name in enumerate(("AI0", "AI1")):
            frame = tk.Frame(parent, bg="#111827", width=320, height=120)
            frame.place(x=(100, 480)[col], y=285)
            frame.pack_propagate(False)

            title = tk.Label(
                frame,
                text=name,
                bg="#111827",
                fg="#E5E7EB",
                font=("Arial", 15, "bold")
            )
            title.place(x=20, y=16)

            mode_label = tk.Label(
                frame,
                text="--",
                bg="#374151",
                fg="white",
                font=("Arial", 9, "bold"),
                anchor="center"
            )
            mode_label.place(x=110, y=14, width=170, height=30)
            self.analog_input_mode_labels[name] = mode_label

            value_label = tk.Label(
                frame,
                text="0.000 V",
                bg="#374151",
                fg="white",
                font=("Consolas", 16, "bold"),
                anchor="center"
            )
            value_label.place(x=20, y=62, width=260, height=42)
            self.analog_input_labels[name] = value_label

        self.update_analog_input_widgets()

    # =====================================================
    # Tab: EEPROM
    # =====================================================
    def build_eeprom_tab(self):
        wrapper = tk.Frame(self.tab_eeprom, bg="#111827")
        wrapper.pack(fill="both", expand=True, padx=10, pady=10)

        write_card = self.make_card(wrapper, "EEPROM WRITE", 650, 620)
        write_card.pack(side="left", fill="both", expand=True, padx=(0, 14))

        read_card = self.make_card(wrapper, "EEPROM READ", 650, 620)
        read_card.pack(side="left", fill="both", expand=True)

        write_info = (
            "Write Topic:\n"
            "  eeprom/write\n\n"
            "Wipe Topic:\n"
            "  eeprom/wipe\n\n"
            "Payload example:\n"
            "  eeprom/write -> UTF-8 bytes from the text box\n"
            "  eeprom/wipe  -> True\n\n"
            "Max write size: 1000 bytes."
        )
        tk.Label(
            write_card,
            text=write_info,
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Consolas", 10),
            justify="left",
            anchor="nw"
        ).place(x=28, y=58, width=590, height=120)

        self.eeprom_write_box = ScrolledText(
            write_card,
            bg="#0B1220",
            fg="#F9FAFB",
            insertbackground="white",
            font=("Consolas", 11),
            relief="flat",
            wrap="word"
        )
        self.eeprom_write_box.place(x=28, y=190, width=590, height=265)
        self.eeprom_write_box.bind("<KeyRelease>", self.update_eeprom_write_count)

        self.eeprom_write_count_label = tk.Label(
            write_card,
            text=f"0/{self.eeprom_max_write_bytes} bytes UTF-8",
            bg="#1F2937",
            fg="#9CA3AF",
            font=("Consolas", 11),
            anchor="w"
        )
        self.eeprom_write_count_label.place(x=28, y=468, width=260, height=28)

        write_btn = tk.Button(
            write_card,
            text="WRITE EEPROM",
            command=self.publish_eeprom_write,
            bg="#10B981",
            fg="white",
            relief="flat",
            font=("Arial", 12, "bold"),
            width=18,
            height=2
        )
        write_btn.place(x=28, y=520)

        clear_write_btn = tk.Button(
            write_card,
            text="CLEAR TEXT",
            command=lambda: (self.eeprom_write_box.delete("1.0", "end"), self.update_eeprom_write_count()),
            bg="#374151",
            fg="white",
            relief="flat",
            font=("Arial", 11, "bold"),
            width=14,
            height=2
        )
        clear_write_btn.place(x=240, y=520)

        wipe_btn = tk.Button(
            write_card,
            text="WIPE EEPROM",
            command=self.publish_eeprom_wipe,
            bg="#EF4444",
            fg="white",
            relief="flat",
            font=("Arial", 11, "bold"),
            width=14,
            height=2
        )
        wipe_btn.place(x=415, y=520)

        read_info = (
            "Read Request Topic:\n"
            "  eeprom/read/request\n\n"
            "Read Response Topic:\n"
            "  eeprom/read/response\n\n"
            "Request payload example:\n"
            "  {\"index\": 0}\n\n"
            "Incoming response bytes are decoded as UTF-8 for display."
        )
        tk.Label(
            read_card,
            text=read_info,
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Consolas", 10),
            justify="left",
            anchor="nw"
        ).place(x=28, y=58, width=590, height=120)

        tk.Label(
            read_card,
            text="Read Index",
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Arial", 11),
        ).place(x=28, y=190)

        self.eeprom_read_index_var = tk.StringVar(value="0")
        self.make_entry(read_card, self.eeprom_read_index_var, 125, 186, 120)

        self.eeprom_read_box = ScrolledText(
            read_card,
            bg="#0B1220",
            fg="#D1D5DB",
            insertbackground="white",
            font=("Consolas", 11),
            relief="flat",
            wrap="word"
        )
        self.eeprom_read_box.place(x=28, y=230, width=590, height=225)
        self.eeprom_read_box.config(state="disabled")

        self.eeprom_read_info_label = tk.Label(
            read_card,
            text="Last read: --",
            bg="#1F2937",
            fg="#9CA3AF",
            font=("Consolas", 11),
            anchor="w"
        )
        self.eeprom_read_info_label.place(x=28, y=468, width=360, height=28)

        read_btn = tk.Button(
            read_card,
            text="READ EEPROM",
            command=self.request_eeprom_read,
            bg="#2563EB",
            fg="white",
            relief="flat",
            font=("Arial", 12, "bold"),
            width=18,
            height=2
        )
        read_btn.place(x=28, y=520)

        clear_read_btn = tk.Button(
            read_card,
            text="CLEAR READ VIEW",
            command=self.clear_eeprom_read_view,
            bg="#374151",
            fg="white",
            relief="flat",
            font=("Arial", 11, "bold"),
            width=18,
            height=2
        )
        clear_read_btn.place(x=240, y=520)

    def clear_eeprom_read_view(self):
        if hasattr(self, "eeprom_read_box"):
            self.eeprom_read_box.config(state="normal")
            self.eeprom_read_box.delete("1.0", "end")
            self.eeprom_read_box.config(state="disabled")
        if hasattr(self, "eeprom_read_info_label"):
            self.eeprom_read_info_label.config(text="Last read: --")

    # =====================================================
    # Tab: UART
    # =====================================================
    def build_uart_tab(self):
        wrapper = tk.Frame(self.tab_uart, bg="#111827")
        wrapper.pack(fill="both", expand=True, padx=10, pady=10)

        uart_card = self.make_card(wrapper, "UART WRITE / RECEIVE", 1320, 620)
        uart_card.pack(fill="both", expand=True)

        write_info = (
            "Write Topic:\n"
            "  uart/write\n\n"
            "Payload example:\n"
            "  Text box content is encoded as UTF-8 bytes.\n\n"
            "Device action:\n"
            "  device.uart.write(msg)"
        )
        tk.Label(
            uart_card,
            text=write_info,
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Consolas", 10),
            justify="left",
            anchor="nw"
        ).place(x=28, y=58, width=560, height=125)

        read_info = (
            "Read Topic:\n"
            "  uart/read\n\n"
            "Incoming payload:\n"
            "  Raw MQTT bytes from the device UART RX side.\n\n"
            "Display rule:\n"
            "  Bytes are decoded as UTF-8 with replacement characters.\n"
            "  SEND = dashboard sent. RECEIVE = device UART RX."
        )
        tk.Label(
            uart_card,
            text=read_info,
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Consolas", 10),
            justify="left",
            anchor="nw"
        ).place(x=660, y=58, width=620, height=145)

        tk.Label(
            uart_card,
            text="UART Write",
            bg="#1F2937",
            fg="#F3F4F6",
            font=("Arial", 12, "bold")
        ).place(x=28, y=205)

        self.uart_write_box = ScrolledText(
            uart_card,
            bg="#0B1220",
            fg="#F9FAFB",
            insertbackground="white",
            font=("Consolas", 11),
            relief="flat",
            wrap="word"
        )
        self.uart_write_box.place(x=28, y=235, width=560, height=265)

        send_btn = tk.Button(
            uart_card,
            text="SEND UART",
            command=self.publish_uart_write,
            bg="#10B981",
            fg="white",
            relief="flat",
            font=("Arial", 12, "bold"),
            width=16,
            height=2
        )
        send_btn.place(x=28, y=525)

        clear_send_btn = tk.Button(
            uart_card,
            text="CLEAR TEXT",
            command=self.clear_uart_write_box,
            bg="#374151",
            fg="white",
            relief="flat",
            font=("Arial", 11, "bold"),
            width=14,
            height=2
        )
        clear_send_btn.place(x=220, y=525)

        tk.Label(
            uart_card,
            text="UART Send / Receive History",
            bg="#1F2937",
            fg="#F3F4F6",
            font=("Arial", 12, "bold")
        ).place(x=660, y=205)

        self.uart_history_box = ScrolledText(
            uart_card,
            bg="#0B1220",
            fg="#D1D5DB",
            insertbackground="white",
            font=("Consolas", 11),
            relief="flat",
            wrap="word"
        )
        self.uart_history_box.place(x=660, y=235, width=620, height=265)
        self.uart_history_box.tag_config("send", foreground="#34D399")
        self.uart_history_box.tag_config("receive", foreground="#60A5FA")
        self.uart_history_box.config(state="disabled")

        clear_history_btn = tk.Button(
            uart_card,
            text="CLEAR HISTORY",
            command=self.clear_uart_history,
            bg="#374151",
            fg="white",
            relief="flat",
            font=("Arial", 11, "bold"),
            width=16,
            height=2
        )
        clear_history_btn.place(x=660, y=525)


    # =====================================================
    # Tab: Load Cell
    # =====================================================
    def build_loadcell_tab(self):
        wrapper = tk.Frame(self.tab_loadcell, bg="#111827")
        wrapper.pack(fill="both", expand=True, padx=80, pady=60)

        card = self.make_card(wrapper, "LOAD CELL WEIGHT", 1100, 430)
        card.pack(fill="both", expand=True)

        unit_frame = tk.Frame(card, bg="#1F2937")
        unit_frame.place(relx=1.0, x=-24, y=12, anchor="ne")
        tk.Label(
            unit_frame,
            text="Display unit:",
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Arial", 10, "bold"),
        ).pack(side="left", padx=(0, 6))
        for text, value in (("kg", "kg"), ("g", "g")):
            tk.Radiobutton(
                unit_frame,
                text=text,
                value=value,
                variable=self.loadcell_unit,
                command=self.update_loadcell_widgets,
                bg="#1F2937",
                fg="#F3F4F6",
                activebackground="#1F2937",
                activeforeground="#60A5FA",
                selectcolor="#111827",
                font=("Arial", 10, "bold"),
                bd=0,
                highlightthickness=0,
            ).pack(side="left")

        tk.Label(
            card,
            text="Live weight from MQTT topic: loadcell/weight",
            bg="#1F2937",
            fg="#9CA3AF",
            font=("Arial", 12),
        ).place(relx=0.5, y=75, anchor="center")

        self.loadcell_weight_label = tk.Label(
            card,
            text="0.00 kg",
            bg="#1F2937",
            fg="#F9FAFB",
            font=("Arial", 38, "bold"),
        )
        self.loadcell_weight_label.place(relx=0.5, y=145, anchor="center")

        self.loadcell_progress = tk.Canvas(
            card,
            bg="#1F2937",
            highlightthickness=0,
            bd=0,
        )
        self.loadcell_progress.place(relx=0.5, y=225, anchor="center", width=850, height=50)
        self.loadcell_progress.bind("<Configure>", self.draw_loadcell_progress)

        self.loadcell_min_label = tk.Label(
            card,
            text="0.00 kg",
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Arial", 12, "bold"),
        )
        self.loadcell_min_label.place(relx=0.5, x=-425, y=270, anchor="nw")

        self.loadcell_max_label = tk.Label(
            card,
            text="10.00 kg",
            bg="#1F2937",
            fg="#D1D5DB",
            font=("Arial", 12, "bold"),
        )
        self.loadcell_max_label.place(relx=0.5, x=425, y=270, anchor="ne")

        tk.Label(
            card,
            text='MQTT accepts numeric strings from "0.00" to "10.00" kilograms.',
            bg="#1F2937",
            fg="#60A5FA",
            font=("Consolas", 11),
        ).place(relx=0.5, y=335, anchor="center")

    # =====================================================
    # Tab: Temperature
    # =====================================================
    def build_temperature_tab(self):
        wrapper = tk.Frame(self.tab_temperature, bg="#111827")
        wrapper.pack(fill="both", expand=True, padx=60, pady=40)

        card = self.make_card(wrapper, "TEMPERATURE SENSORS", 1200, 540)
        card.pack(fill="both", expand=True)

        tk.Label(
            card,
            text="Calculated temperatures received from temp/k1 and temp/k2",
            bg="#1F2937",
            fg="#9CA3AF",
            font=("Arial", 12),
        ).place(relx=0.5, y=70, anchor="center")

        for sensor, topic, y in (
            ("K1", "temp/k1", 160),
            ("K2", "temp/k2", 350),
        ):
            tk.Label(
                card,
                text=sensor,
                bg="#1F2937",
                fg="#F9FAFB",
                font=("Arial", 22, "bold"),
            ).place(x=45, y=y, anchor="w")

            tk.Label(
                card,
                text=topic,
                bg="#1F2937",
                fg="#60A5FA",
                font=("Consolas", 10),
            ).place(x=45, y=y + 35, anchor="w")

            progress = tk.Canvas(
                card,
                bg="#1F2937",
                highlightthickness=0,
                bd=0,
            )
            progress.place(x=150, y=y, anchor="w", width=720, height=50)
            progress.bind(
                "<Configure>",
                lambda event, selected_sensor=sensor:
                    self.draw_temperature_progress(selected_sensor, event),
            )
            self.temperature_progress_bars[sensor] = progress

            tk.Label(
                card,
                text="0 °C",
                bg="#1F2937",
                fg="#D1D5DB",
                font=("Arial", 10, "bold"),
            ).place(x=150, y=y + 42, anchor="w")
            tk.Label(
                card,
                text="1300 °C",
                bg="#1F2937",
                fg="#D1D5DB",
                font=("Arial", 10, "bold"),
            ).place(x=870, y=y + 42, anchor="e")

            value_label = tk.Label(
                card,
                text="0.00 °C",
                bg="#111827",
                fg="#FDBA74",
                font=("Arial", 24, "bold"),
                padx=18,
                pady=8,
            )
            value_label.place(x=925, y=y, anchor="w", width=220, height=58)
            self.temperature_value_labels[sensor] = value_label

    # =====================================================
    # Helper widgets
    # =====================================================
    def make_card(self, parent, title, width, height):
        frame = tk.Frame(parent, bg="#1F2937", width=width, height=height, bd=0, highlightthickness=0)
        frame.pack_propagate(False)

        title_label = tk.Label(
            frame,
            text=title,
            bg="#1F2937",
            fg="#F3F4F6",
            font=("Arial", 13, "bold")
        )
        title_label.place(x=18, y=14)

        top_line = tk.Frame(frame, bg="#374151", height=1, width=width - 36)
        top_line.place(x=18, y=40)

        return frame

    def make_label(self, parent, text, x, y):
        label = tk.Label(parent, text=text, bg="#1F2937", fg="#D1D5DB", font=("Arial", 11))
        label.place(x=x, y=y)
        return label

    def make_entry(self, parent, variable, x, y, width):
        entry = tk.Entry(
            parent,
            textvariable=variable,
            bg="#111827",
            fg="#F9FAFB",
            insertbackground="white",
            relief="flat",
            font=("Consolas", 11)
        )
        entry.place(x=x, y=y, width=width, height=30)
        return entry

    # =====================================================
    # UI updates
    # =====================================================
    def update_network_mode_widgets(self, *_args):
        if not hasattr(self, "net_config_entries"):
            return

        is_dhcp = self.net_mode.get() == "dhcp"
        entry_state = "disabled" if is_dhcp else "normal"

        for entry in self.net_config_entries:
            entry.config(
                state=entry_state,
                disabledbackground="#374151",
                disabledforeground="#9CA3AF",
            )

    def init_status_widgets(self):
        self.update_connection_badge("DISCONNECTED", "#EF4444")
        if hasattr(self, "connection_value"):
            self.connection_value.config(text="Disconnected")

    def update_connection_badge(self, text, color):
        if hasattr(self, "connection_badge"):
            self.connection_badge.config(text=text, bg=color)

    def update_output_widgets(self):
        for name, button in self.output_buttons.items():
            state = self.output_states.get(name, False)
            if state:
                button.config(text=f"{name}: ON", bg="#22C55E", fg="#052E16")
            else:
                button.config(text=f"{name}: OFF", bg="#374151", fg="white")

    def update_input_widgets(self):
        for name, label in self.input_labels.items():
            pin_is_high = self.input_states.get(name, False)
            
            # Active-low input:
            # HIGH means OFF, LOW means ON.
            logical_on = not pin_is_high
            
            if logical_on:
                label.config(text="ON", bg="#3B82F6", fg="#0F172A")
            else:
                label.config(text="OFF", bg="#374151", fg="white")

    def update_analog_input_widgets(self):
        for name, label in self.analog_input_labels.items():
            value = self.analog_input_states.get(name, 0.0)
            mode = self.analog_input_modes.get(name, 0)

            if mode == 1:
                unit = "mA"
                mode_text = "CURRENT MODE"
                bg = "#F59E0B"
                fg = "#111827"
                mode_bg = "#D97706"
            else:
                unit = "V"
                mode_text = "VOLTAGE MODE"
                bg = "#3B82F6"
                fg = "#0F172A"
                mode_bg = "#2563EB"

            label.config(text=f"{value:.3f} {unit}", bg=bg, fg=fg)

            mode_label = self.analog_input_mode_labels.get(name)
            if mode_label is not None:
                mode_label.config(text=mode_text, bg=mode_bg, fg="white")

    def write_log(self, text):
        if self.closing or not hasattr(self, "log_box"):
            return
        try:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            self.log_box.insert("end", f"[{timestamp}] {text}\n")
            self.log_box.see("end")
        except tk.TclError:
            pass

    def update_clock(self):
        if self.closing:
            return
        try:
            now = time.strftime("%H:%M:%S")
            self.clock_label.config(text=now)
            self.root.after(1000, self.update_clock)
        except tk.TclError:
            pass

    def on_close(self):
        if self.closing:
            return

        self.closing = True
        self.connected = False

        try:
            if self.client is not None:
                self.client.on_connect = None
                self.client.on_message = None
                self.client.on_disconnect = None
                self.client.disconnect()
                self.client.loop_stop()
        except Exception:
            pass

        try:
            self.root.destroy()
        except tk.TclError:
            pass


if __name__ == "__main__":
    root = tk.Tk()
    app = DeviceDashboard(root)
    root.mainloop()

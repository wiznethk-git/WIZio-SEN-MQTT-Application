# MQTT Dashboard

The `Tools/` folder contains `dashboard.py`, which can be used to monitor and control the device's peripherals.

## Setup

### 1. Create a virtual environment

Make sure Python is installed, then run:

```bash
python -m venv venv
```

### 2. Activate the virtual environment

Windows:

```bash
source venv\Scripts\activate
```

Linux, macOS, or WSL:

```bash
source venv/bin/activate
```

Install the required packages:

```bash
pip install -r requirements.txt
```

### 3. Configure MQTT login details

If your MQTT broker requires authentication, add the username and password directly to `dashboard.py`.

Alternatively, create a `config.py` file:

```python
USERNAME = "your_username"
PASSWORD = "your_password"
```

### 4. Run the dashboard

```bash
python dashboard.py
```

Enter the MQTT broker address and select **Connect** to start monitoring and controlling the device.

## Using Mosquitto in WSL

If Mosquitto is running inside WSL, port `1883` must be forwarded from Windows to WSL.

### 1. Find the WSL IP address

Run inside WSL:

```bash
hostname -I
```

Copy the first IP address shown.

### 2. Forward port 1883

Open PowerShell as Administrator and run:

```powershell
netsh interface portproxy add v4tov4 listenport=1883 listenaddress=0.0.0.0 connectport=1883 connectaddress=<YOUR_WSL_IP>
```

Replace `<YOUR_WSL_IP>` with the IP address returned by `hostname -I`.

### 3. Allow port 1883 through Windows Firewall

```powershell
New-NetFirewallRule -DisplayName "Mosquitto MQTT" -Direction Inbound -Protocol TCP -LocalPort 1883 -Action Allow
```

### 4. Check the forwarding rule

```powershell
netsh interface portproxy show v4tov4
```

> The WSL IP address may change after restarting WSL or Windows. Update the forwarding rule if the dashboard can no longer connect.


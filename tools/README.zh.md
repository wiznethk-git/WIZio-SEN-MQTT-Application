# MQTT 仪表盘

`Tools/` 文件夹中包含 `dashboard.py`，可用于监控和控制设备的外设。

## 环境配置

### 1\. 创建虚拟环境

确保已安装 Python，然后运行：

```bash
python -m venv venv
```

### 2\. 激活虚拟环境

Windows：

```bash
source venv\Scripts\activate
```

Linux、macOS 或 WSL：

```bash
source venv/bin/activate
```

安装所需的依赖包：

```bash
pip install -r requirements.txt
```

### 3\. 配置 MQTT 登录信息

如果 MQTT Broker（代理服务器）需要身份验证，请直接在 `dashboard.py` 中添加用户名和密码。

或者，创建一个 `config.py` 文件：

```python
USERNAME = "your_username"
PASSWORD = "your_password"
```

### 4\. 运行仪表盘

```bash
python dashboard.py
```

输入 MQTT Broker 的地址，然后点击 **Connect（连接）**，即可开始监控和控制设备。

## 在 WSL 中使用 Mosquitto

如果 Mosquitto 运行在 WSL 环境中，则需要将 Windows 的 `1883` 端口转发到 WSL。

### 1\. 获取 WSL IP 地址

在 WSL 中运行：

```bash
hostname -I
```

复制显示的第一个 IP 地址。

### 2\. 转发 1883 端口

以管理员身份打开 PowerShell，然后运行：

```powershell
netsh interface portproxy add v4tov4 listenport=1883 listenaddress=0.0.0.0 connectport=1883 connectaddress=<YOUR_WSL_IP>
```

将 `<YOUR_WSL_IP>` 替换为 `hostname -I` 返回的 IP 地址。

### 3\. 在 Windows 防火墙中允许 1883 端口通信

```powershell
New-NetFirewallRule -DisplayName "Mosquitto MQTT" -Direction Inbound -Protocol TCP -LocalPort 1883 -Action Allow
```

### 4\. 检查端口转发规则

```powershell
netsh interface portproxy show v4tov4
```

> **注意：** 重新启动 WSL 或 Windows 后，WSL 的 IP 地址可能会发生变化。如果仪表盘无法连接，请检查并更新端口转发规则。

# PiCar Pro Client

Desktop GUI client for controlling the PiCar Pro robot.

## Usage

```bash
python3 Client/GUI.py [server_ip]
```

If no IP is provided, it reads from `Client/IP.txt` or defaults to `127.0.0.1`.

The client connects to the WebSocket server on port 8888 (run by `Server/WebServer.py`).

## Features

- Movement controls (forward, backward, left, right, stop)
- Speed slider (0-100%)
- LED mode selection (off, solid, breath, flow, rainbow, police)
- Autonomous function buttons (radar, drive, line following, distance keeping)
- Real-time system info display (CPU, RAM, temperature)
- Connection status indicator

## Dependencies

- Python 3 with tkinter (`sudo apt-get install python3-tk`)
- websockets library (`pip3 install websockets`)

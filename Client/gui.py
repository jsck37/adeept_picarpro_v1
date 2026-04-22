"""
PiCar Pro Desktop GUI Client.
Connects to the robot server via WebSocket and ZMQ video stream.

Features:
- Motor control (directional buttons)
- Servo sliders for pan/tilt/arm
- CV mode selection and color picker
- LED mode control
- Autonomous function triggers
- Battery and system status display
- Keyboard control (WASD)

Improvements over v1:
- WebSocket-based (same protocol as web client)
- Better layout and organization
- Real-time status updates
- Keyboard control support
"""

import json
import sys
import os
import threading
import time

try:
    import tkinter as tk
    from tkinter import ttk, colorchooser, messagebox
    HAS_TK = True
except ImportError:
    HAS_TK = False
    print("tkinter not available. Install: sudo apt install python3-tk")

try:
    import websockets
    import asyncio
    HAS_WS = True
except ImportError:
    HAS_WS = False
    print("websockets not available. Install: pip3 install websockets")

try:
    import zmq
    HAS_ZMQ = True
except ImportError:
    HAS_ZMQ = False


# Default server IP
DEFAULT_IP = "192.168.4.1"
DEFAULT_WS_PORT = 8888


class PiCarProClient:
    """Desktop GUI client for PiCar Pro."""

    def __init__(self):
        self._ws = None
        self._ws_loop = None
        self._ws_thread = None
        self._connected = False
        self._server_ip = DEFAULT_IP
        self._running = True

        # Build GUI
        self._build_gui()

    def _build_gui(self):
        """Create the GUI layout."""
        if not HAS_TK:
            return

        self.root = tk.Tk()
        self.root.title("PiCar Pro Controller")
        self.root.geometry("920x600")
        self.root.resizable(True, True)

        # ---- Connection Frame ----
        conn_frame = ttk.LabelFrame(self.root, text="Connection", padding=5)
        conn_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(conn_frame, text="Server IP:").pack(side=tk.LEFT, padx=5)
        self.ip_entry = ttk.Entry(conn_frame, width=15)
        self.ip_entry.insert(0, DEFAULT_IP)
        self.ip_entry.pack(side=tk.LEFT, padx=5)

        self.connect_btn = ttk.Button(conn_frame, text="Connect", command=self._toggle_connection)
        self.connect_btn.pack(side=tk.LEFT, padx=5)

        self.status_label = ttk.Label(conn_frame, text="Disconnected", foreground="red")
        self.status_label.pack(side=tk.LEFT, padx=10)

        # ---- Main Content ----
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Left: Motor controls
        left_frame = ttk.LabelFrame(main_frame, text="Motor Control", padding=5)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        # Direction buttons
        btn_frame = ttk.Frame(left_frame)
        btn_frame.pack(pady=10)

        self.btn_forward = ttk.Button(btn_frame, text="▲ Forward", width=12,
                                       command=lambda: self._send_cmd("forward"))
        self.btn_forward.grid(row=0, column=1, padx=2, pady=2)

        self.btn_left = ttk.Button(btn_frame, text="◄ Left", width=12,
                                    command=lambda: self._send_cmd("left"))
        self.btn_left.grid(row=1, column=0, padx=2, pady=2)

        self.btn_stop = ttk.Button(btn_frame, text="■ Stop", width=12,
                                    command=lambda: self._send_cmd("stop"))
        self.btn_stop.grid(row=1, column=1, padx=2, pady=2)

        self.btn_right = ttk.Button(btn_frame, text="► Right", width=12,
                                     command=lambda: self._send_cmd("right"))
        self.btn_right.grid(row=1, column=2, padx=2, pady=2)

        self.btn_backward = ttk.Button(btn_frame, text="▼ Backward", width=12,
                                        command=lambda: self._send_cmd("backward"))
        self.btn_backward.grid(row=2, column=1, padx=2, pady=2)

        # Speed slider
        speed_frame = ttk.Frame(left_frame)
        speed_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(speed_frame, text="Speed:").pack(side=tk.LEFT)
        self.speed_var = tk.IntVar(value=50)
        speed_slider = ttk.Scale(speed_frame, from_=0, to=100, variable=self.speed_var,
                                  orient=tk.HORIZONTAL, command=self._on_speed_change)
        speed_slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.speed_label = ttk.Label(speed_frame, text="50")
        self.speed_label.pack(side=tk.LEFT)

        # Keyboard hint
        ttk.Label(left_frame, text="Keyboard: W=Forward, S=Backward, A=Left, D=Right, Space=Stop",
                   font=("", 8)).pack(pady=5)

        # Center: Servo controls
        center_frame = ttk.LabelFrame(main_frame, text="Servo Control", padding=5)
        center_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        self.servo_vars = []
        servo_names = ["Pan", "Tilt", "Base", "Shoulder", "Elbow", "Wrist", "Gripper", "Spare"]
        for i, name in enumerate(servo_names):
            frame = ttk.Frame(center_frame)
            frame.pack(fill=tk.X, padx=2, pady=1)

            ttk.Label(frame, text=f"{name}:", width=10).pack(side=tk.LEFT)
            var = tk.IntVar(value=90)
            slider = ttk.Scale(frame, from_=0, to=180, variable=var,
                                orient=tk.HORIZONTAL,
                                command=lambda val, idx=i: self._on_servo_change(idx, val))
            slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
            self.servo_vars.append(var)

        # Right: Functions & Status
        right_frame = ttk.LabelFrame(main_frame, text="Functions & Status", padding=5)
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)

        # CV Mode
        cv_frame = ttk.LabelFrame(right_frame, text="CV Mode", padding=3)
        cv_frame.pack(fill=tk.X, padx=2, pady=2)

        self.cv_var = tk.StringVar(value="none")
        for mode in ["none", "findColor", "findlineCV", "watchDog"]:
            ttk.Radiobutton(cv_frame, text=mode, variable=self.cv_var,
                             value=mode, command=self._on_cv_mode_change).pack(anchor=tk.W)

        # Color picker button
        ttk.Button(cv_frame, text="Pick Color", command=self._pick_color).pack(fill=tk.X, pady=2)

        # LED Mode
        led_frame = ttk.LabelFrame(right_frame, text="LED Mode", padding=3)
        led_frame.pack(fill=tk.X, padx=2, pady=2)

        self.led_var = tk.StringVar(value="solid")
        for mode in ["off", "solid", "breath", "flowing", "rainbow", "police"]:
            ttk.Radiobutton(led_frame, text=mode, variable=self.led_var,
                             value=mode, command=self._on_led_mode_change).pack(anchor=tk.W)

        # Autonomous functions
        auto_frame = ttk.LabelFrame(right_frame, text="Autonomous", padding=3)
        auto_frame.pack(fill=tk.X, padx=2, pady=2)

        for func in ["radarScan", "automatic", "trackLine", "keepDistance"]:
            ttk.Button(auto_frame, text=func,
                        command=lambda f=func: self._send_cmd("function", f)).pack(fill=tk.X, pady=1)

        ttk.Button(auto_frame, text="STOP", command=lambda: self._send_cmd("function", "stop")).pack(fill=tk.X, pady=2)

        # Status display
        status_frame = ttk.LabelFrame(right_frame, text="Status", padding=3)
        status_frame.pack(fill=tk.X, padx=2, pady=2)

        self.status_text = tk.Text(status_frame, height=6, width=25, font=("Courier", 9))
        self.status_text.pack(fill=tk.X)

        # ---- Keyboard Bindings ----
        self.root.bind('<KeyPress>', self._on_key_press)
        self.root.bind('<KeyRelease>', self._on_key_release)

    # =========================================================================
    # Connection Management
    # =========================================================================

    def _toggle_connection(self):
        """Connect or disconnect from the server."""
        if self._connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        """Connect to the robot server via WebSocket."""
        self._server_ip = self.ip_entry.get()

        self._ws_thread = threading.Thread(target=self._ws_loop_run, daemon=True)
        self._ws_thread.start()

    def _disconnect(self):
        """Disconnect from the server."""
        self._connected = False
        if self._ws is not None:
            asyncio.run_coroutine_threadsafe(self._ws.close(), self._ws_loop)
        self.connect_btn.config(text="Connect")
        self.status_label.config(text="Disconnected", foreground="red")

    def _ws_loop_run(self):
        """Run the WebSocket event loop in a background thread."""
        self._ws_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._ws_loop)

        try:
            self._ws_loop.run_until_complete(self._ws_connect())
        except Exception as e:
            print(f"Connection error: {e}")
            self.root.after(0, lambda: self._on_disconnect())

    async def _ws_connect(self):
        """Connect to the WebSocket server."""
        uri = f"ws://{self._server_ip}:{DEFAULT_WS_PORT}"
        try:
            self._ws = await websockets.connect(uri)
            self._connected = True
            self.root.after(0, lambda: self._on_connect())

            # Start receiving messages
            async for message in self._ws:
                try:
                    data = json.loads(message)
                    self.root.after(0, lambda d=data: self._on_message(d))
                except json.JSONDecodeError:
                    pass

        except Exception as e:
            print(f"WebSocket error: {e}")
            self.root.after(0, lambda: self._on_disconnect())

    def _on_connect(self):
        """Handle successful connection."""
        self._connected = True
        self.connect_btn.config(text="Disconnect")
        self.status_label.config(text="Connected", foreground="green")
        # Request status
        self._send_cmd("status")

    def _on_disconnect(self):
        """Handle disconnection."""
        self._connected = False
        self.connect_btn.config(text="Connect")
        self.status_label.config(text="Disconnected", foreground="red")

    def _on_message(self, data):
        """Handle a message from the server."""
        msg_type = data.get("type", "")

        if msg_type == "status":
            self._update_status(data)

    def _update_status(self, data):
        """Update the status display."""
        self.status_text.delete('1.0', tk.END)
        self.status_text.insert(tk.END, f"Speed:  {data.get('speed', 0)}%\n")
        self.status_text.insert(tk.END, f"Mode:   {data.get('mode', 'PT')}\n")
        self.status_text.insert(tk.END, f"CPU:    {data.get('cpu_temp', 0)}C / {data.get('cpu_usage', 0)}%\n")
        self.status_text.insert(tk.END, f"Battery: {data.get('voltage', 0)}V ({data.get('battery', 0)}%)\n")
        self.status_text.insert(tk.END, f"Dist:   {data.get('distance', 0)}cm\n")
        self.status_text.insert(tk.END, f"CV:     {data.get('cv_mode', 'none')}\n")

    # =========================================================================
    # Command Sending
    # =========================================================================

    def _send_cmd(self, command, value=""):
        """Send a command to the robot server."""
        if not self._connected or self._ws is None:
            return

        msg = json.dumps({"command": command, "value": value})

        async def _send():
            try:
                await self._ws.send(msg)
            except Exception as e:
                print(f"Send error: {e}")

        if self._ws_loop is not None:
            asyncio.run_coroutine_threadsafe(_send(), self._ws_loop)

    # =========================================================================
    # UI Callbacks
    # =========================================================================

    def _on_speed_change(self, val):
        """Handle speed slider change."""
        speed = int(float(val))
        self.speed_label.config(text=str(speed))
        self._send_cmd("speed", speed)

    def _on_servo_change(self, servo_id, val):
        """Handle servo slider change."""
        angle = int(float(val))
        self._send_cmd("servo", "")
        # Send as a more specific command
        if self._ws_loop is not None and self._ws is not None:
            msg = json.dumps({"command": "servo", "id": servo_id, "angle": angle})
            asyncio.run_coroutine_threadsafe(self._ws.send(msg), self._ws_loop)

    def _on_cv_mode_change(self):
        """Handle CV mode radio button change."""
        self._send_cmd("cvMode", self.cv_var.get())

    def _on_led_mode_change(self):
        """Handle LED mode radio button change."""
        msg = json.dumps({
            "command": "led",
            "mode": self.led_var.get(),
            "color": [255, 0, 0],
        })
        if self._ws_loop is not None and self._ws is not None:
            asyncio.run_coroutine_threadsafe(self._ws.send(msg), self._ws_loop)

    def _pick_color(self):
        """Open color picker dialog."""
        color = colorchooser.askcolor(title="Pick CV Tracking Color")
        if color[0] is not None:
            r, g, b = [int(c) for c in color[0]]
            # Convert to HSV range (approximate)
            import colorsys
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            h_low = int(h * 180) - 10
            h_high = int(h * 180) + 10
            msg = json.dumps({
                "command": "colorRange",
                "lh": max(0, h_low), "ls": 43, "lv": 46,
                "uh": min(180, h_high), "us": 255, "uv": 255,
            })
            if self._ws_loop is not None and self._ws is not None:
                asyncio.run_coroutine_threadsafe(self._ws.send(msg), self._ws_loop)

    def _on_key_press(self, event):
        """Handle keyboard input for robot control."""
        key = event.keysym.lower()
        if key == 'w':
            self._send_cmd("forward")
        elif key == 's':
            self._send_cmd("backward")
        elif key == 'a':
            self._send_cmd("left")
        elif key == 'd':
            self._send_cmd("right")
        elif key == 'space':
            self._send_cmd("stop")

    def _on_key_release(self, event):
        """Handle key release - stop the robot."""
        key = event.keysym.lower()
        if key in ('w', 's', 'a', 'd'):
            self._send_cmd("stop")

    # =========================================================================
    # Main Loop
    # =========================================================================

    def run(self):
        """Start the GUI main loop."""
        if not HAS_TK:
            print("Cannot start GUI without tkinter")
            return

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self):
        """Handle window close."""
        self._running = False
        self._disconnect()
        self.root.destroy()


if __name__ == '__main__':
    client = PiCarProClient()
    client.run()

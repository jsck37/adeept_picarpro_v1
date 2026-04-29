#!/usr/bin/env python3
"""
PiCar Pro Desktop Client — Python tkinter GUI

Connects to the PiCar Pro WebSocket server on port 8888.
Provides basic movement controls, speed slider, and system info display.
Matches the original GUI.py pattern from the adeept_picarpro repository.

Usage:
    python3 Client/GUI.py [server_ip]

If no IP is provided, reads from Client/IP.txt or defaults to 127.0.0.1
"""

import json
import os
import sys
import threading
import time

try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except ImportError:
    print("ERROR: tkinter not available. Install with: sudo apt-get install python3-tk")
    sys.exit(1)

try:
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False
    print("WARNING: websockets library not installed. Install with: pip3 install websockets")


# ═════════════════════════════════════════════════════════════════════════════
#  Configuration
# ═════════════════════════════════════════════════════════════════════════════

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8888

# Read IP from IP.txt if available
IP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "IP.txt")


def get_server_ip():
    """Get server IP from command line arg, IP.txt, or default."""
    if len(sys.argv) > 1:
        return sys.argv[1]
    if os.path.isfile(IP_FILE):
        try:
            with open(IP_FILE, "r") as f:
                ip = f.read().strip()
                if ip:
                    return ip
        except Exception:
            pass
    return DEFAULT_HOST


# ═════════════════════════════════════════════════════════════════════════════
#  WebSocket Client
# ═════════════════════════════════════════════════════════════════════════════

class WSClient:
    """WebSocket client that connects to the PiCar Pro server."""

    def __init__(self, host, port, on_status=None, on_response=None):
        self.host = host
        self.port = port
        self.ws = None
        self.connected = False
        self._running = True
        self._on_status = on_status
        self._on_response = on_response
        self._thread = None
        self._loop = None

    def start(self):
        """Start the WebSocket client in a background thread."""
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self):
        """Run the asyncio event loop in the background thread."""
        import asyncio
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_loop())
        except Exception as e:
            print(f"[WSClient] Event loop error: {e}")
        finally:
            self._loop.close()

    async def _connect_loop(self):
        """Continuously try to connect to the WebSocket server."""
        import asyncio
        while self._running:
            try:
                uri = f"ws://{self.host}:{self.port}"
                async with websockets.connect(uri) as ws:
                    self.ws = ws
                    self.connected = True
                    print(f"[WSClient] Connected to {uri}")

                    # Listen for messages
                    async for message in ws:
                        if not self._running:
                            break
                        try:
                            data = json.loads(message)
                            msg_type = data.get('type', '')
                            msg_data = data.get('data', {})

                            if msg_type == 'status' and self._on_status:
                                self._on_status(msg_data)
                            elif msg_type == 'response' and self._on_response:
                                self._on_response(msg_data)
                        except json.JSONDecodeError:
                            pass

            except Exception as e:
                if self._running:
                    print(f"[WSClient] Connection error: {e}")

            finally:
                self.ws = None
                self.connected = False

            if self._running:
                # Wait before retrying
                await asyncio.sleep(3)

    def send_command(self, cmd, params=None):
        """Send a command to the server."""
        params = params or {}
        if self.ws is None:
            return

        msg = json.dumps({'cmd': cmd, 'params': params})

        # Schedule the send in the event loop
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._async_send(msg), self._loop)

    async def _async_send(self, msg):
        """Async send helper."""
        try:
            if self.ws:
                await self.ws.send(msg)
        except Exception as e:
            print(f"[WSClient] Send error: {e}")

    def stop(self):
        """Stop the client."""
        self._running = False
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._close(), self._loop)

    async def _close(self):
        if self.ws:
            await self.ws.close()


# ═════════════════════════════════════════════════════════════════════════════
#  Main GUI Application
# ═════════════════════════════════════════════════════════════════════════════

class PiCarProGUI:
    """PiCar Pro desktop control GUI using tkinter."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("PiCar Pro Controller")
        self.root.geometry("420x620")
        self.root.resizable(False, False)

        # Colors
        self.BG = "#f0f2f5"
        self.PRIMARY = "#1a73e8"
        self.DARK = "#202124"
        self.GRAY = "#5f6368"
        self.LIGHT_GRAY = "#dadce0"
        self.RED = "#ea4335"
        self.GREEN = "#34a853"

        self.root.configure(bg=self.BG)

        # Speed
        self.speed_var = tk.IntVar(value=50)

        # WebSocket client
        host = get_server_ip()
        self.client = WSClient(host, DEFAULT_PORT, on_status=self.on_status)

        # Build UI
        self._build_ui()

        # Start WebSocket client
        if HAS_WEBSOCKETS:
            self.client.start()
        else:
            self.update_connection(False)

        # Periodic UI update
        self.root.after(1000, self._periodic_update)

    def _build_ui(self):
        """Build the tkinter GUI layout."""
        # ── Title bar ──
        title_frame = tk.Frame(self.root, bg=self.PRIMARY, height=50)
        title_frame.pack(fill=tk.X)
        title_frame.pack_propagate(False)

        tk.Label(
            title_frame, text="PiCar Pro", font=("Helvetica", 16, "bold"),
            bg=self.PRIMARY, fg="white"
        ).pack(side=tk.LEFT, padx=15, pady=10)

        self.conn_label = tk.Label(
            title_frame, text="Connecting...", font=("Helvetica", 9),
            bg=self.PRIMARY, fg="#a8c7fa"
        )
        self.conn_label.pack(side=tk.RIGHT, padx=15)

        # ── System info ──
        info_frame = tk.Frame(self.root, bg=self.BG)
        info_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        self.info_label = tk.Label(
            info_frame, text="CPU: -- | RAM: -- | Temp: --",
            font=("Helvetica", 10), bg=self.BG, fg=self.GRAY
        )
        self.info_label.pack()

        # ── Movement controls ──
        move_frame = tk.LabelFrame(
            self.root, text="Movement", font=("Helvetica", 11, "bold"),
            bg=self.BG, fg=self.DARK, padx=10, pady=10
        )
        move_frame.pack(fill=tk.X, padx=10, pady=5)

        # Direction buttons grid
        btn_frame = tk.Frame(move_frame, bg=self.BG)
        btn_frame.pack()

        btn_style = {
            "font": ("Helvetica", 16), "width": 3, "height": 1,
            "bg": "#e8f0fe", "fg": self.PRIMARY, "activebackground": self.PRIMARY,
            "activeforeground": "white", "relief": "flat", "cursor": "hand2",
        }

        # Row 1: Forward
        tk.Button(btn_frame, text="^", command=lambda: self.move("forward"), **btn_style).grid(row=0, column=1, padx=3, pady=3)

        # Row 2: Left, Stop, Right
        tk.Button(btn_frame, text="<", command=lambda: self.move("left"), **btn_style).grid(row=1, column=0, padx=3, pady=3)
        tk.Button(btn_frame, text="X", command=lambda: self.move("stop"),
                  font=("Helvetica", 16, "bold"), width=3, height=1,
                  bg=self.RED, fg="white", activebackground="#c5221f",
                  activeforeground="white", relief="flat", cursor="hand2"
                  ).grid(row=1, column=1, padx=3, pady=3)
        tk.Button(btn_frame, text=">", command=lambda: self.move("right"), **btn_style).grid(row=1, column=2, padx=3, pady=3)

        # Row 3: Backward
        tk.Button(btn_frame, text="v", command=lambda: self.move("backward"), **btn_style).grid(row=2, column=1, padx=3, pady=3)

        # ── Speed slider ──
        speed_frame = tk.LabelFrame(
            self.root, text="Speed", font=("Helvetica", 11, "bold"),
            bg=self.BG, fg=self.DARK, padx=10, pady=5
        )
        speed_frame.pack(fill=tk.X, padx=10, pady=5)

        self.speed_label = tk.Label(
            speed_frame, text="50%", font=("Helvetica", 12, "bold"),
            bg=self.BG, fg=self.PRIMARY
        )
        self.speed_label.pack()

        speed_slider = ttk.Scale(
            speed_frame, from_=0, to=100, orient=tk.HORIZONTAL,
            variable=self.speed_var, command=self.on_speed_change
        )
        speed_slider.pack(fill=tk.X, padx=5, pady=5)

        # ── LED control ──
        led_frame = tk.LabelFrame(
            self.root, text="LED", font=("Helvetica", 11, "bold"),
            bg=self.BG, fg=self.DARK, padx=10, pady=5
        )
        led_frame.pack(fill=tk.X, padx=10, pady=5)

        led_btn_frame = tk.Frame(led_frame, bg=self.BG)
        led_btn_frame.pack()

        led_modes = [
            ("Off", "off"), ("Solid", "solid"), ("Breath", "breath"),
            ("Flow", "flow"), ("Rainbow", "rainbow"), ("Police", "police"),
        ]
        for i, (label, mode) in enumerate(led_modes):
            tk.Button(
                led_btn_frame, text=label, font=("Helvetica", 9),
                width=7, bg="#e8f0fe", fg=self.PRIMARY, relief="flat",
                activebackground=self.PRIMARY, activeforeground="white",
                cursor="hand2", command=lambda m=mode: self.set_led(m)
            ).grid(row=i // 3, column=i % 3, padx=2, pady=2)

        # ── Autonomous ──
        auto_frame = tk.LabelFrame(
            self.root, text="Autonomous", font=("Helvetica", 11, "bold"),
            bg=self.BG, fg=self.DARK, padx=10, pady=5
        )
        auto_frame.pack(fill=tk.X, padx=10, pady=5)

        auto_btn_frame = tk.Frame(auto_frame, bg=self.BG)
        auto_btn_frame.pack()

        auto_modes = [
            ("Radar", "radarScan"), ("Drive", "automatic"),
            ("Line", "trackLine"), ("Distance", "keepDistance"),
        ]
        for i, (label, func) in enumerate(auto_modes):
            tk.Button(
                auto_btn_frame, text=label, font=("Helvetica", 9),
                width=8, bg="#e8f0fe", fg=self.PRIMARY, relief="flat",
                activebackground=self.PRIMARY, activeforeground="white",
                cursor="hand2", command=lambda f=func: self.start_auto(f)
            ).grid(row=0, column=i, padx=2, pady=2)

        tk.Button(
            auto_btn_frame, text="STOP", font=("Helvetica", 9, "bold"),
            width=8, bg=self.RED, fg="white", relief="flat",
            activebackground="#c5221f", cursor="hand2",
            command=lambda: self.start_auto("stop")
        ).grid(row=1, column=0, columnspan=4, padx=2, pady=4, sticky="ew")

        # ── Status bar ──
        status_frame = tk.Frame(self.root, bg=self.DARK, height=30)
        status_frame.pack(fill=tk.X, side=tk.BOTTOM)
        status_frame.pack_propagate(False)

        self.status_label = tk.Label(
            status_frame, text="Ready", font=("Helvetica", 9),
            bg=self.DARK, fg="white"
        )
        self.status_label.pack(pady=5)

    # ── Command helpers ────────────────────────────────────────────────────

    def move(self, direction):
        """Send a movement command."""
        self.client.send_command('move', {'dir': direction})
        self.status_label.config(text=f"Move: {direction}")

    def on_speed_change(self, value):
        """Handle speed slider change."""
        speed = int(float(value))
        self.speed_label.config(text=f"{speed}%")
        self.client.send_command('speed', {'value': speed})

    def set_led(self, mode):
        """Send an LED mode command."""
        self.client.send_command('led', {'mode': mode})
        self.status_label.config(text=f"LED: {mode}")

    def start_auto(self, func):
        """Send an autonomous function command."""
        self.client.send_command('auto', {'func': func})
        self.status_label.config(text=f"Auto: {func}")

    # ── Status handler ─────────────────────────────────────────────────────

    def on_status(self, data):
        """Handle status update from WebSocket."""
        self.root.after(0, self._update_status_ui, data)

    def _update_status_ui(self, data):
        """Update UI with status data (called on main thread)."""
        cpu_temp = data.get('cpu_temp', '--')
        cpu_usage = data.get('cpu_usage', '--')
        ram_percent = data.get('ram_percent', '--')
        ram_used = data.get('ram_used', '--')
        ram_total = data.get('ram_total', '--')

        self.info_label.config(
            text=f"CPU: {cpu_temp}C {cpu_usage}% | RAM: {ram_used}/{ram_total}G {ram_percent}%"
        )

        speed = data.get('speed', 50)
        self.speed_var.set(speed)
        self.speed_label.config(text=f"{speed}%")

        mod = data.get('running_module', '')
        if mod:
            self.status_label.config(text=f"Running: {mod}")

        self.update_connection(True)

    def update_connection(self, connected):
        """Update connection indicator."""
        if connected:
            self.conn_label.config(text="Connected", fg="#81c995")
        else:
            self.conn_label.config(text="Disconnected", fg="#f28b82")

    def _periodic_update(self):
        """Periodic UI update timer."""
        self.update_connection(self.client.connected)
        if self.root:
            self.root.after(2000, self._periodic_update)

    # ── Run ────────────────────────────────────────────────────────────────

    def run(self):
        """Start the GUI event loop."""
        try:
            self.root.mainloop()
        finally:
            self.client.stop()


# ═════════════════════════════════════════════════════════════════════════════
#  Entry Point
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if not HAS_WEBSOCKETS:
        print("ERROR: websockets library required!")
        print("Install with: pip3 install websockets")
        sys.exit(1)

    app = PiCarProGUI()
    app.run()

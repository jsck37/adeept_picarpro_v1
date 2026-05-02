#!/usr/bin/env python3
"""Generate horizontal Raspberry Pi 3B+ GPIO Pinout Diagram for PiCar Pro.
   All labels horizontal, bold, Liberation Sans (MS Sans), large readable text."""
import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import matplotlib.font_manager as fm

# Liberation Sans = metric equivalent of MS Sans Serif
fm.fontManager.addfont('/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf')
fm.fontManager.addfont('/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf')
# Noto Sans SC is variable font — skip addfont, let matplotlib find it
plt.rcParams['font.sans-serif'] = ['Liberation Sans', 'DejaVu Sans', 'Noto Sans SC']
plt.rcParams['axes.unicode_minus'] = False

# ── Pin data ──
left_pins = [
    (1,"3V3","3V3\nPower","power"),(3,"GPIO2","SDA1\n(I2C)","i2c"),
    (5,"GPIO3","SCL1\n(I2C)","i2c"),(7,"GPIO4","","gpio"),
    (9,"GND","Ground","ground"),(11,"GPIO17","","gpio"),
    (13,"GPIO27","","gpio"),(15,"GPIO22","","gpio"),
    (17,"3V3","3V3\nPower","power"),(19,"GPIO10","MOSI\n(SPI)","spi"),
    (21,"GPIO9","MISO\n(SPI)","spi"),(23,"GPIO11","SCLK\n(SPI)","spi"),
    (25,"GND","Ground","ground"),(27,"GPIO0","SDA0\n(I2C)","i2c"),
    (29,"GPIO5","","gpio"),(31,"GPIO6","","gpio"),
    (33,"GPIO13","","gpio"),(35,"GPIO19","","gpio"),
    (37,"GPIO26","","gpio"),(39,"GND","Ground","ground"),
]
right_pins = [
    (2,"5V","5V\nPower","power"),(4,"5V","5V\nPower","power"),
    (6,"GND","Ground","ground"),(8,"GPIO14","TXD0\n(UART)","uart"),
    (10,"GPIO15","RXD0\n(UART)","uart"),(12,"GPIO18","","gpio"),
    (14,"GND","Ground","ground"),(16,"GPIO23","","gpio"),
    (18,"GPIO24","","gpio"),(20,"GND","Ground","ground"),
    (22,"GPIO25","","gpio"),(24,"GPIO8","CE0\n(SPI)","spi"),
    (26,"GPIO7","CE1\n(SPI)","spi"),(28,"GPIO1","SCL0\n(I2C)","i2c"),
    (30,"GND","Ground","ground"),(32,"GPIO12","","gpio"),
    (34,"GND","Ground","ground"),(36,"GPIO16","","gpio"),
    (38,"GPIO20","","gpio"),(40,"GPIO21","","gpio"),
]

picar_pro = {
    3:"I2C: PCA9685/MPU6050/OLED", 5:"I2C: PCA9685/MPU6050/OLED",
    7:"Motor EN_A", 29:"Buzzer", 31:"Left Headlight",
    19:"WS2812 LED", 23:"Ultrasonic TRIG", 24:"Ultrasonic ECHO",
    33:"Right Headlight", 36:"Line Tracker Ctr", 11:"Motor EN_B",
    12:"Motor IN2_B", 35:"Line Tracker Right", 38:"Line Tracker Left",
    40:"Motor IN2_A", 37:"Motor IN1_A", 13:"Motor IN1_B",
}

category_colors = {
    "power":"#E74C3C", "ground":"#95A5A6", "i2c":"#27AE60",
    "spi":"#2980B9", "uart":"#8E44AD", "gpio":"#E67E22",
}
picar_indicator_color = "#F1C40F"

# ── Layout constants ──
PIN_W   = 2.6       # wider pins for large horizontal text
PIN_H   = 3.2       # taller pin boxes for big font
COL_GAP = 0.18
NUM_COLS = 20
ROW_GAP = 0.6       # gap between pin box and board
BOARD_PAD = 1.2

board_w = NUM_COLS * (PIN_W + COL_GAP) - COL_GAP
fig_w = board_w + BOARD_PAD * 2 + 3
fig_h = 26

fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=150)
ax.set_xlim(-1, fig_w)
ax.set_ylim(-5, fig_h)
ax.set_aspect('equal')
ax.axis('off')

# ── Background ──
fig.patch.set_facecolor('#1A1A2E')
ax.set_facecolor('#1A1A2E')

# ── Title ──
title_x = BOARD_PAD + board_w / 2
# Position title above legend (legend_y + 3.5)
title_y = 23.5
ax.text(title_x, title_y,
        "Raspberry Pi 3B+ \u2014 Распиновка GPIO \u2014 PiCar Pro",
        ha='center', va='center', fontsize=64, fontweight='bold', color='#ECF0F1')

# ── Board rectangle ──
board_x = BOARD_PAD
board_y = 9.0
board_h = 2.8
ax.add_patch(patches.FancyBboxPatch(
    (board_x, board_y), board_w, board_h,
    boxstyle="round,pad=0.2", facecolor='#2C3E50',
    edgecolor='#4A6FA5', linewidth=3, zorder=0))
ax.text(board_x + board_w / 2, board_y + board_h / 2,
        "RPi 3B+ GPIO Header",
        ha='center', va='center', fontsize=64, fontweight='bold',
        color='#7F8C8D', zorder=1)

# ── Draw pins ──
for col_idx in range(NUM_COLS):
    x = BOARD_PAD + col_idx * (PIN_W + COL_GAP)
    l_data = left_pins[col_idx]
    r_data = right_pins[col_idx]

    # ── Right pin (top row) ──
    pin_num, gpio_label, func_label, category = r_data
    color = category_colors[category]
    top_y = board_y + board_h + ROW_GAP

    # Pin number above
    ax.text(x + PIN_W / 2, top_y + PIN_H + 0.5, str(pin_num),
            ha='center', va='bottom', fontsize=36, fontweight='bold', color='#BDC3C7')
    # Pin box
    ax.add_patch(patches.FancyBboxPatch(
        (x, top_y), PIN_W, PIN_H,
        boxstyle="round,pad=0.06", facecolor=color,
        edgecolor='#1A1A2E', linewidth=1.2, zorder=2, alpha=0.92))
    # GPIO label inside box — ALWAYS HORIZONTAL, BOLD
    ax.text(x + PIN_W / 2, top_y + PIN_H / 2, gpio_label,
            ha='center', va='center', fontsize=30, fontweight='bold',
            color='#FFFFFF', zorder=3)
    # Function label above box (multi-line)
    if func_label:
        ax.text(x + PIN_W / 2, top_y + PIN_H + 1.8, func_label,
                ha='center', va='bottom', fontsize=31, fontweight='bold',
                color='#AEB6BF', zorder=3, linespacing=1.1)
    # PiCar Pro indicator dot
    if pin_num in picar_pro:
        ax.plot(x - 0.25, top_y + PIN_H / 2, 'o',
                color=picar_indicator_color, markersize=24, zorder=4)

    # ── Left pin (bottom row) ──
    pin_num, gpio_label, func_label, category = l_data
    color = category_colors[category]
    bot_y = board_y - PIN_H - ROW_GAP

    # Pin number below
    ax.text(x + PIN_W / 2, bot_y - 0.7, str(pin_num),
            ha='center', va='top', fontsize=36, fontweight='bold', color='#BDC3C7')
    # Pin box
    ax.add_patch(patches.FancyBboxPatch(
        (x, bot_y), PIN_W, PIN_H,
        boxstyle="round,pad=0.06", facecolor=color,
        edgecolor='#1A1A2E', linewidth=1.2, zorder=2, alpha=0.92))
    # GPIO label inside box — ALWAYS HORIZONTAL, BOLD
    ax.text(x + PIN_W / 2, bot_y + PIN_H / 2, gpio_label,
            ha='center', va='center', fontsize=30, fontweight='bold',
            color='#FFFFFF', zorder=3)
    # Function label below pin number (multi-line)
    if func_label:
        ax.text(x + PIN_W / 2, bot_y - 2.0, func_label,
                ha='center', va='top', fontsize=31, fontweight='bold',
                color='#AEB6BF', zorder=3, linespacing=1.1)
    # PiCar Pro indicator dot
    if pin_num in picar_pro:
        ax.plot(x - 0.25, bot_y + PIN_H / 2, 'o',
                color=picar_indicator_color, markersize=24, zorder=4)

    # Thin vertical guide line
    ax.plot([x + PIN_W / 2, x + PIN_W / 2],
            [board_y, board_y + board_h],
            color='#3D5A80', linewidth=0.5, alpha=0.3, zorder=0)

# ── PiCar Pro pin assignments table ──
table_y_start = bot_y - 5.0
ax.text(BOARD_PAD, table_y_start + 0.3,
        "\u25a0 PiCar Pro \u2014 Назначение пинов:",
        ha='left', va='top', fontsize=56, fontweight='bold',
        color=picar_indicator_color)

picar_items = sorted(picar_pro.items())
cols_per_row = 3
col_width = board_w / cols_per_row
for i, (pin, desc) in enumerate(picar_items):
    row = i // cols_per_row
    col = i % cols_per_row
    px = BOARD_PAD + col * col_width
    py = table_y_start - 1.28 - row * 0.8
    ax.text(px + 0.3, py, f"Pin {pin}: {desc}",
            ha='left', va='center', fontsize=48, fontweight='bold',
            color='#ECF0F1', zorder=3)

# ── Legend ──
legend_items = [
    ("Питание (3V3/5V)", "power"), ("Земля (GND)", "ground"),
    ("I2C (SDA/SCL)", "i2c"), ("SPI (MOSI/MISO)", "spi"),
    ("UART (TXD/RXD)", "uart"), ("GPIO Общий", "gpio"),
]
# Gap from top pins to legend = gap from bottom pins to PiCar header (~2.1 data units)
# Top pin highest element ≈ 17.4, so legend bottom = 17.4 + 2.1 = 19.5, legend_y = 20.0
legend_y = 20.0
legend_spacing = 9.5
for i, (label, cat) in enumerate(legend_items):
    lx = BOARD_PAD + i * legend_spacing
    ax.add_patch(patches.FancyBboxPatch(
        (lx - 0.05, legend_y - 0.5), 1.0, 1.0,
        boxstyle="round,pad=0.06", facecolor=category_colors[cat],
        edgecolor='none', alpha=0.92, zorder=12))
    ax.text(lx + 1.3, legend_y, label,
            ha='left', va='center', fontsize=48, fontweight='bold',
            color='#ECF0F1', zorder=12)

# ── Save ──
script_dir = os.path.dirname(os.path.abspath(__file__))
output_path = os.path.join(script_dir, "rpi_pinout.png")
plt.tight_layout(pad=2.0)
fig.savefig(output_path, dpi=150,
            facecolor=fig.get_facecolor(), edgecolor='none')
plt.close()
print(f"Saved: {output_path}")

#!/usr/bin/env python3
"""Generate horizontal Raspberry Pi 3B+ GPIO Pinout Diagram for PiCar Pro."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import matplotlib.font_manager as fm
fm.fontManager.addfont('/usr/share/fonts/truetype/chinese/SarasaMonoSC-Regular.ttf')
fm.fontManager.addfont('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
fm.fontManager.addfont('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf')
plt.rcParams['font.sans-serif'] = ['Sarasa Mono SC', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
left_pins = [
    (1,"3V3","3V3 Power","power"),(3,"GPIO 2","SDA1 (I2C)","i2c"),
    (5,"GPIO 3","SCL1 (I2C)","i2c"),(7,"GPIO 4","","gpio"),
    (9,"GND","Ground","ground"),(11,"GPIO 17","","gpio"),
    (13,"GPIO 27","","gpio"),(15,"GPIO 22","","gpio"),
    (17,"3V3","3V3 Power","power"),(19,"GPIO 10","MOSI (SPI)","spi"),
    (21,"GPIO 9","MISO (SPI)","spi"),(23,"GPIO 11","SCLK (SPI)","spi"),
    (25,"GND","Ground","ground"),(27,"GPIO 0","SDA0 (I2C)","i2c"),
    (29,"GPIO 5","","gpio"),(31,"GPIO 6","","gpio"),
    (33,"GPIO 13","","gpio"),(35,"GPIO 19","","gpio"),
    (37,"GPIO 26","","gpio"),(39,"GND","Ground","ground"),
]
right_pins = [
    (2,"5V","5V Power","power"),(4,"5V","5V Power","power"),
    (6,"GND","Ground","ground"),(8,"GPIO 14","TXD0 (UART)","uart"),
    (10,"GPIO 15","RXD0 (UART)","uart"),(12,"GPIO 18","","gpio"),
    (14,"GND","Ground","ground"),(16,"GPIO 23","","gpio"),
    (18,"GPIO 24","","gpio"),(20,"GND","Ground","ground"),
    (22,"GPIO 25","","gpio"),(24,"GPIO 8","CE0 (SPI)","spi"),
    (26,"GPIO 7","CE1 (SPI)","spi"),(28,"GPIO 1","SCL0 (I2C)","i2c"),
    (30,"GND","Ground","ground"),(32,"GPIO 12","","gpio"),
    (34,"GND","Ground","ground"),(36,"GPIO 16","","gpio"),
    (38,"GPIO 20","","gpio"),(40,"GPIO 21","","gpio"),
]
picar_pro = {3:"I2C: PCA9685/MPU6050/OLED",5:"I2C: PCA9685/MPU6050/OLED",7:"Motor EN_A",29:"Buzzer",31:"Left Headlight",19:"WS2812 LED",23:"Ultrasonic TRIG",24:"Ultrasonic ECHO",33:"Right Headlight",36:"Line Tracker Ctr",11:"Motor EN_B",12:"Motor IN2_B",35:"Line Tracker Right",38:"Line Tracker Left",40:"Motor IN2_A",37:"Motor IN1_A",13:"Motor IN1_B"}
category_colors = {"power":"#E74C3C","ground":"#95A5A6","i2c":"#27AE60","spi":"#2980B9","uart":"#8E44AD","gpio":"#E67E22"}
picar_indicator_color = "#F1C40F"
PIN_W=1.15; PIN_H=1.8; COL_GAP=0.10; NUM_COLS=20
fig_w=NUM_COLS*(PIN_W+COL_GAP)+4; fig_h=10
fig,ax=plt.subplots(figsize=(fig_w,fig_h),dpi=150)
ax.set_xlim(-2,fig_w+1); ax.set_ylim(-3,fig_h); ax.set_aspect('equal'); ax.axis('off')
fig.patch.set_facecolor('#1A1A2E'); ax.set_facecolor('#1A1A2E')
ax.text(fig_w/2-1,fig_h-0.3,"Raspberry Pi 3B+ \u2014 Распиновка GPIO \u2014 PiCar Pro",ha='center',va='center',fontsize=14,fontweight='bold',color='#ECF0F1')
board_x=0.5; board_y=3.5; board_w=NUM_COLS*(PIN_W+COL_GAP)-COL_GAP; board_h=1.6
ax.add_patch(patches.FancyBboxPatch((board_x,board_y),board_w,board_h,boxstyle="round,pad=0.15",facecolor='#2C3E50',edgecolor='#4A6FA5',linewidth=2,zorder=0))
ax.text(board_x+board_w/2,board_y+board_h/2,"RPi 3B+ GPIO Header",ha='center',va='center',fontsize=9,fontweight='bold',color='#7F8C8D',zorder=1)
for col_idx in range(NUM_COLS):
    x=0.5+col_idx*(PIN_W+COL_GAP)
    l_data=left_pins[col_idx]; r_data=right_pins[col_idx]
    top_y=board_y+board_h+0.25
    pin_num,gpio_label,func_label,category=r_data; color=category_colors[category]
    ax.text(x+PIN_W/2,top_y+PIN_H+0.15,str(pin_num),ha='center',va='bottom',fontsize=6.5,fontweight='bold',color='#BDC3C7')
    ax.add_patch(patches.FancyBboxPatch((x,top_y),PIN_W,PIN_H,boxstyle="round,pad=0.03",facecolor=color,edgecolor='#1A1A2E',linewidth=0.8,zorder=2,alpha=0.92))
    ax.text(x+PIN_W/2,top_y+PIN_H/2,gpio_label,ha='center',va='center',fontsize=6,fontweight='bold',color='#FFFFFF',zorder=3,rotation=90 if len(gpio_label)>6 else 0)
    if func_label: ax.text(x+PIN_W/2,top_y-0.12,func_label,ha='center',va='top',fontsize=5,color='#AEB6BF',zorder=3)
    if pin_num in picar_pro: ax.plot(x-0.08,top_y+PIN_H/2,'s',color=picar_indicator_color,markersize=3.5,zorder=4)
    bot_y=board_y-PIN_H-0.25
    pin_num,gpio_label,func_label,category=l_data; color=category_colors[category]
    ax.text(x+PIN_W/2,bot_y-0.15,str(pin_num),ha='center',va='top',fontsize=6.5,fontweight='bold',color='#BDC3C7')
    ax.add_patch(patches.FancyBboxPatch((x,bot_y),PIN_W,PIN_H,boxstyle="round,pad=0.03",facecolor=color,edgecolor='#1A1A2E',linewidth=0.8,zorder=2,alpha=0.92))
    ax.text(x+PIN_W/2,bot_y+PIN_H/2,gpio_label,ha='center',va='center',fontsize=6,fontweight='bold',color='#FFFFFF',zorder=3,rotation=90 if len(gpio_label)>6 else 0)
    if func_label: ax.text(x+PIN_W/2,bot_y+PIN_H+0.12,func_label,ha='center',va='bottom',fontsize=5,color='#AEB6BF',zorder=3)
    if pin_num in picar_pro: ax.plot(x-0.08,bot_y+PIN_H/2,'s',color=picar_indicator_color,markersize=3.5,zorder=4)
    ax.plot([x+PIN_W/2,x+PIN_W/2],[board_y,board_y+board_h],color='#3D5A80',linewidth=0.3,alpha=0.3,zorder=0)
table_y_start=bot_y-1.0
ax.text(0.5,table_y_start+0.2,"\u25a0 PiCar Pro \u2014 Назначение пинов:",ha='left',va='top',fontsize=8.5,fontweight='bold',color=picar_indicator_color)
picar_items=sorted(picar_pro.items())
for i,(pin,desc) in enumerate(picar_items):
    row=i//4; col=i%4; px=0.5+col*(board_w/4); py=table_y_start-0.4-row*0.45
    ax.text(px+0.2,py,f"Pin {pin}: {desc}",ha='left',va='center',fontsize=5.5,color='#ECF0F1',zorder=3)
for i,(label,cat) in enumerate([("Питание (3V3/5V)","power"),("Земля (GND)","ground"),("I2C (SDA/SCL)","i2c"),("SPI (MOSI/MISO)","spi"),("UART (TXD/RXD)","uart"),("GPIO Общий","gpio")]):
    lx=0.5+i*4.0; ly=fig_h-1.3
    ax.add_patch(patches.FancyBboxPatch((lx-0.05,ly-0.12),0.25,0.25,boxstyle="round,pad=0.02",facecolor=category_colors[cat],edgecolor='none',alpha=0.92,zorder=4))
    ax.text(lx+0.35,ly,label,ha='left',va='center',fontsize=6.5,color='#ECF0F1',zorder=4)
output_path="/home/z/my-project/download/picarpro/Server/dist/rpi_pinout.png"
plt.tight_layout(pad=0.5)
fig.savefig(output_path,dpi=150,bbox_inches='tight',facecolor=fig.get_facecolor(),edgecolor='none')
plt.close()
print(f"Saved: {output_path}")

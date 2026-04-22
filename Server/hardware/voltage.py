"""
Battery voltage monitoring module (backported from v2).
Uses ADS7830 ADC to read battery voltage via voltage divider.

Features:
- Continuous background monitoring
- Rolling median filter with outlier removal
- Low voltage alarm via callback
- Battery percentage estimation
"""

import threading
import time
from Server.config import (
    ADS7830_ADDR, ADS7830_CHANNEL,
    BATTERY_FULL_VOLTAGE, BATTERY_WARNING_VOLTAGE, BATTERY_VOLTAGE_RATIO,
    BUZZER_PIN,
)


class VoltageMonitor:
    """ADS7830-based battery voltage monitor with alarm."""

    def __init__(self):
        self._adc = None
        self._running = True
        self._voltage = 0.0
        self._percentage = 0
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._alarm_active = False
        self._on_low_voltage = None
        self._initialized = False

        try:
            import adafruit_ads7830.ads7830 as ADS
            import busio

            i2c = busio.I2C(3, 2)
            self._adc = ADS.ADS7830(i2c, address=ADS7830_ADDR)
            self._initialized = True
            self._thread.start()
            print("[Voltage] Battery monitor initialized")
        except Exception as e:
            print(f"[Voltage] Failed to initialize: {e}")
            print("[Voltage] Battery monitoring disabled")

    def set_low_voltage_callback(self, callback):
        """Set callback for low voltage alarm."""
        self._on_low_voltage = callback

    def _read_raw(self):
        """Read raw ADC value from ADS7830."""
        if self._adc is None:
            return 0

        try:
            value = self._adc.read(ADS7830_CHANNEL)
            return value
        except Exception:
            return 0

    def _raw_to_voltage(self, raw):
        """Convert raw ADC value to battery voltage."""
        if raw <= 0:
            return 0.0
        # ADC is 8-bit (0-255), reference voltage ~3.3V
        # Apply voltage divider ratio
        adc_voltage = (raw / 255.0) * 3.3
        battery_voltage = adc_voltage / BATTERY_VOLTAGE_RATIO
        return round(battery_voltage, 2)

    def _voltage_to_percentage(self, voltage):
        """Estimate battery percentage from voltage (simple linear model)."""
        if voltage >= BATTERY_FULL_VOLTAGE:
            return 100
        elif voltage <= BATTERY_WARNING_VOLTAGE:
            return 0
        else:
            return int((voltage - BATTERY_WARNING_VOLTAGE) /
                       (BATTERY_FULL_VOLTAGE - BATTERY_WARNING_VOLTAGE) * 100)

    def _monitor_loop(self):
        """Background monitoring loop."""
        samples = []

        while self._running:
            raw = self._read_raw()
            voltage = self._raw_to_voltage(raw)

            if voltage > 0:
                samples.append(voltage)
                # Keep last 10 samples for median filter
                if len(samples) > 10:
                    samples.pop(0)

                # Median filter
                sorted_samples = sorted(samples)
                median_voltage = sorted_samples[len(sorted_samples) // 2]

                # Outlier removal
                if len(samples) >= 3:
                    filtered = [s for s in samples
                                if abs(s - median_voltage) < 0.5]
                    if filtered:
                        median_voltage = sorted(filtered)[len(filtered) // 2]

                percentage = self._voltage_to_percentage(median_voltage)

                with self._lock:
                    self._voltage = median_voltage
                    self._percentage = percentage

                # Low voltage alarm
                if median_voltage < BATTERY_WARNING_VOLTAGE and not self._alarm_active:
                    self._alarm_active = True
                    if self._on_low_voltage:
                        self._on_low_voltage(median_voltage)
                    print(f"[Voltage] LOW BATTERY: {median_voltage}V!")
                elif median_voltage >= BATTERY_WARNING_VOLTAGE + 0.3:
                    self._alarm_active = False

            time.sleep(2)  # Check every 2 seconds

    def get_voltage(self):
        """Get current battery voltage."""
        with self._lock:
            return self._voltage

    def get_percentage(self):
        """Get estimated battery percentage."""
        with self._lock:
            return self._percentage

    def shutdown(self):
        """Stop monitoring."""
        self._running = False
        print("[Voltage] Shutdown complete")

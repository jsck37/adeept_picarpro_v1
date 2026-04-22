"""
1D Kalman Filter for sensor data smoothing.
Used for smooth color tracking and ultrasonic distance readings.
"""

import numpy as np


class KalmanFilter:
    """
    Simple 1D Kalman filter.
    
    Usage:
        kf = KalmanFilter()
        filtered = kf.filter(raw_measurement)
    """

    def __init__(self, process_noise=1e-5, measurement_noise=1e-2, estimate=0.0):
        """
        Initialize Kalman filter.
        
        Args:
            process_noise: Process noise covariance (Q) - how much we expect the value to change
            measurement_noise: Measurement noise covariance (R) - how noisy our sensor is
            estimate: Initial estimate
        """
        self.q = process_noise
        self.r = measurement_noise
        self.p = 1.0          # Estimation error covariance
        self.x = estimate     # Current estimate
        self._initialized = estimate != 0.0

    def filter(self, measurement):
        """
        Update the filter with a new measurement.
        
        Args:
            measurement: Raw sensor measurement
            
        Returns:
            Filtered estimate
        """
        if not self._initialized:
            self.x = measurement
            self._initialized = True
            return self.x

        # Prediction
        self.p = self.p + self.q

        # Update
        k = self.p / (self.p + self.r)  # Kalman gain
        self.x = self.x + k * (measurement - self.x)
        self.p = (1 - k) * self.p

        return self.x

    def get(self):
        """Get current estimate without updating."""
        return self.x

    def reset(self, value=0.0):
        """Reset the filter."""
        self.x = value
        self.p = 1.0
        self._initialized = value != 0.0

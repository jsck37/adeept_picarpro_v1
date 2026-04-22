"""
PID Controller.
Clean implementation of Proportional-Integral-Derivative controller.
"""


class PID:
    """
    Simple PID controller.
    
    Usage:
        pid = PID(kp=1.0, ki=0.0, kd=0.0)
        output = pid.update(error)
    """

    def __init__(self, kp=1.0, ki=0.0, kd=0.0, output_min=-100, output_max=100):
        """
        Initialize PID controller.
        
        Args:
            kp: Proportional gain
            ki: Integral gain
            kd: Derivative gain
            output_min: Minimum output value
            output_max: Maximum output value
        """
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.output_min = output_min
        self.output_max = output_max

        self._integral = 0.0
        self._prev_error = 0.0
        self._first_update = True

    def update(self, error, dt=None):
        """
        Calculate PID output for given error.
        
        Args:
            error: Current error value
            dt: Time delta since last update (auto-calculated if None)
            
        Returns:
            Controller output, clamped to [output_min, output_max]
        """
        import time

        if dt is None:
            dt = 0.02  # Default 50Hz update rate

        # Proportional
        p_term = self.kp * error

        # Integral with anti-windup
        self._integral += error * dt
        self._integral = max(-50, min(50, self._integral))  # Anti-windup
        i_term = self.ki * self._integral

        # Derivative
        if self._first_update:
            d_term = 0.0
            self._first_update = False
        else:
            d_term = self.kd * (error - self._prev_error) / dt

        self._prev_error = error

        output = p_term + i_term + d_term
        return max(self.output_min, min(self.output_max, output))

    def reset(self):
        """Reset the controller state."""
        self._integral = 0.0
        self._prev_error = 0.0
        self._first_update = True

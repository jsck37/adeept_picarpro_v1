#!/bin/bash
# Start PiCar Pro desktop client
# Usage: ./run_client.sh [SERVER_IP]

IP=${1:-"192.168.4.1"}
python3 "$(dirname "$0")/gui.py"

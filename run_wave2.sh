#!/usr/bin/env bash
cd "$(dirname "$0")"
nohup python3 run_pilot.py --arms in-band-judge --replicates 3 --budget 6 --model sonnet > logs/wave2.out 2>&1 &
echo "wave2 PID: $!"

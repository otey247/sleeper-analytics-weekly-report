#!/bin/bash

echo "Running Fantasy Football Metrics Weekly Report application from within virtual environment..."

source ~/.bashrc

cd ~/Projects/sleeper-analytics-weekly-report

workon sleeper-analytics-weekly-report

python main.py

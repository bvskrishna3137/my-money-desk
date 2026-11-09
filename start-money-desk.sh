#!/data/data/com.termux/files/usr/bin/sh
# For Termux:Boot. Save as ~/.termux/boot/start-money-desk.sh and run: chmod +x ~/.termux/boot/start-money-desk.sh
T=~/storage/shared/Documents/MyMoneyDesk
termux-wake-lock 2>/dev/null
until [ -d "$T" ]; do sleep 10; done
cd "$T" && python3 server.py > /dev/null 2>&1 &

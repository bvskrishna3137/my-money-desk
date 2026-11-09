#!/data/data/com.termux/files/usr/bin/sh
# My Money Desk updater. Usage:  sh update.sh
# Moves the newest downloaded index.html / server.py / sw.js from Downloads into the app folder and restarts the server.
D=~/storage/downloads
T=~/storage/shared/Documents/MyMoneyDesk
[ -d "$T" ] || { echo "App folder not found: $T"; exit 1; }
pkill -f server.py 2>/dev/null
for f in index.html server.py sw.js; do
  base="${f%.*}"; ext="${f##*.}"
  new=$(ls -t "$D"/"$base".$ext "$D"/"$base"\ \(*\).$ext 2>/dev/null | head -1)
  if [ -n "$new" ]; then cp "$new" "$T/$f" && rm -f "$D"/"$base".$ext "$D"/"$base"\ \(*\).$ext && echo "updated $f"; else echo "no new $f (kept the old one)"; fi
done
cd "$T" || exit 1
wc -c index.html
python3 server.py > /dev/null 2>&1 &
sleep 1
echo "Running. Open http://127.0.0.1:8080"

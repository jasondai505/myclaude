import datetime, os
log = os.path.join(os.path.dirname(__file__), "_hook_debug.log")
with open(log, "a", encoding="utf-8") as f:
    f.write(f"{datetime.datetime.now().strftime('%H:%M:%S')} PostToolUse fired\n")

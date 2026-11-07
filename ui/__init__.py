# The dashboard package (Day 6). Nothing here is imported at Max's startup:
# main.py only reaches for it with --ui, and every module keeps its heavy
# libraries (CustomTkinter, pystray, pynput) out of the import path.

# The dashboard package (Day 6). Nothing here is imported at Max's startup:
# main.py reaches for it with --ui, and every module keeps its
# heavy libraries (pystray, pynput, pywebview) out of the import
# path.

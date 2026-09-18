import gc
import sys
gc.threshold(4096)
gc.enable()

# Settings
EXIT_TO_REPL = False
file = 'mqtt_app.py'

if not EXIT_TO_REPL:
    try:
        fn, _ = file.split('.', 1)
        mod = __import__(fn)
        if hasattr(mod, 'start'):
            mod.start()
    except ImportError as e:
        print(f'Cannot import file {file} in main.')
        print(str(e))
else:
    print('Exit to REPL.')

try:
    from config import DEBUG
except:
    DEBUG = False
    
def dprint(*args):
    if DEBUG:
        print("[DEBUG]", *args)

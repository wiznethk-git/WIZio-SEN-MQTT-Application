def validate_ipv4(ip):
    print('Validating: ', ip)
    if not isinstance(ip, str):
        msg = b'The given ip is not a string.'
        return (False, msg)
    seg = ip.split('.')
    if len(seg) != 4:
        msg = b'Ensure address are all in ipv4. (x.x.x.x)'
        return (False, msg)
    try:
        for i in seg:
            octet = int(i)
            if octet > 255 or octet < 0:
                msg = b'Ensure input is an ipv4 address.'
                return (False, msg)
    except (ValueError, TypeError):
        return (False, b'Ensure each digit is a number')
    return (True, None)
    
# Can
def is_valid_can_id(can_id):
    if not isinstance(can_id, int):
        print(f'Can ID {can_id} not an integer')
        return False
    
    if (can_id < 0 or can_id > 0x1FFFFFFF):
        print(f"can_id {can_id} out of range (0x0 to 0x1FFFFFFF)")
        return False
    return True
    
def is_valid_can_data(data):
    if not isinstance(data, (bytes, bytearray)):
        print(f'Bytes/Bytearray data required. :{data}')
        return False
    
    if len(data) > 8:
        print(f'Max length 8 bytes exceeded.\n{data}:{len(data)}')
        return False
    return True

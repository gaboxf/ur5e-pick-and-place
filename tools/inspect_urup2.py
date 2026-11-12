import os
import re

filepath = r'E:\UPRO\UPrO\Desafio_Final\robot-urup2-g5-cb5.6-6.0.1200.urup'

if not os.path.exists(filepath):
    print('File NOT found:', filepath)
    exit(1)

file_size = os.path.getsize(filepath)
print('File size:', file_size, 'bytes (', round(file_size/1024/1024), 'MB)')
print('First 500 bytes hex:')
with open(filepath, 'rb') as f:
    header = f.read(500)
for i in range(0, min(500, len(header)), 16):
    hex_part = ' '.join(f'{b:02x}' for b in header[i:i+16])
    ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in header[i:i+16])
    print(f'{i:04x}: {hex_part:<48} {ascii_part}')

# Try to find URScript code - look for common patterns
print('\n--- Buscando URScript patterns ---')
patterns_urscript = [
    b'moveL', b'moveJ', b'set IOWrite', b'set IRead', b'stopj', 
    b'pause', b'reset', b'Boolean', b'Number', b'String',
    b'True', b'False', b'if', b'else', b'for', b'while'
]
with open(filepath, 'rb') as f:
    content = f.read()
    
for pat in patterns_urscript:
    positions = [m.start() for m in re.finditer(pat, content)]
    if positions:
        print(f'  "{pat.decode()}": {len(positions)} ocurrencias (primeras pos: {positions[:3]})')

# Look for numeric positions data - UR usually stores positions as 6 floats
print('\n--- Buscando secuencias de 6 números (posiciones) ---')
# Pattern: 6 floating point numbers separated by commas or spaces
pos_pattern = re.findall(b'[-]?\\d+[.,]?\\d*\\s[-]?\\d+[.,]?\\d*\\s[-]?\\d+[.,]?\\d*\\s[-]?\\d+[.,]?\\d*\\s[-]?\\d+[.,]?\\d*\\s[-]?\\d+[.,]?\\d*', content[:100000])
print(f'Found {len(pos_pattern)} potential position sequences in first 100KB')

# Check for URBackup format magic
print('\n--- Verificando formato URBackup ---')
if content[:4] == b'URBU':
    print('  Es formato URBackup')
elif content[:4] == b'URSP':
    print('  Es formato URScript puro')
else:
    # Check for RAUC header
    if b'raucb' in content[:20].lower() or b'boot-' in content[:20].lower():
        print('  Formato RAUC (Roll Your Own Update Container) - típico de UR 6.0+')
    else:
        print('  Formato binario desconocido (probablemente URBackup comprimido)')
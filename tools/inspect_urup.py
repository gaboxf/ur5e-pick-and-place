import os
import re

filepath = r'E:\UPRO\UPrO\Desafio_Final\robot-urup2-g5-cb5.6-6.0.1200.urup'

print('File exists:', os.path.exists(filepath))
if os.path.exists(filepath):
    file_size = os.path.getsize(filepath)
    print('Size:', file_size, 'bytes')
    with open(filepath, 'rb') as f:
        content = f.read()
    strings = re.findall(b'[ -~]{4,}', content)
    print('--- Stringes encontradas ---')
    for i, s in enumerate(strings[:30]):
        try:
            print(' [' + str(i) + '] ' + s.decode('ascii'))
        except:
            print(' [' + str(i) + '] <binary>')
    patterns = [b'moveL', b'moveJ', b'pick', b'place', b'home', b'position', b'zone']
    print('--- Patrones UR ---')
    for pat in patterns:
        positions = [m.start() for m in re.finditer(pat, content)]
        if positions:
            print(' ' + pat.decode() + ' en ' + str(len(positions)) + ' lugares')
else:
    print('File NOT found at:', filepath)
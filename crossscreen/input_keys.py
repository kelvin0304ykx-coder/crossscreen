"""Physical key positions shared by the Win32 and Quartz adapters.

No keyboard text is serialized. The receiving system performs layout/IME work.
Windows values are scan codes; bit 8 denotes the E0 prefix.
"""

# name, PC/AT scan code, Apple virtual key code
_ROWS = '''
a 1e 00
s 1f 01
d 20 02
f 21 03
h 23 04
g 22 05
z 2c 06
x 2d 07
c 2e 08
v 2f 09
b 30 0b
q 10 0c
w 11 0d
e 12 0e
r 13 0f
y 15 10
t 14 11
1 02 12
2 03 13
3 04 14
4 05 15
6 07 16
5 06 17
equal 0d 18
9 0a 19
7 08 1a
minus 0c 1b
8 09 1c
0 0b 1d
bracket_r 1b 1e
o 18 1f
u 16 20
bracket_l 1a 21
i 17 22
p 19 23
enter 1c 24
l 26 25
j 24 26
quote 28 27
k 25 28
semicolon 27 29
backslash 2b 2a
comma 33 2b
slash 35 2c
n 31 2d
m 32 2e
period 34 2f
tab 0f 30
space 39 31
grave 29 32
backspace 0e 33
escape 01 35
meta_r 15c 36
meta_l 15b 37
shift_l 2a 38
caps_lock 3a 39
alt_l 38 3a
ctrl_l 1d 3b
shift_r 36 3c
alt_r 138 3d
ctrl_r 11d 3e
f17 068 40
kp_decimal 53 41
kp_multiply 37 43
kp_plus 4e 45
num_lock 45 47
kp_divide 135 4b
kp_enter 11c 4c
kp_minus 4a 4e
f18 069 4f
f19 06a 50
kp_equal 59 51
kp_0 52 52
kp_1 4f 53
kp_2 50 54
kp_3 51 55
kp_4 4b 56
kp_5 4c 57
kp_6 4d 58
kp_7 47 59
f20 06b 5a
kp_8 48 5b
kp_9 49 5c
f5 3f 60
f6 40 61
f7 41 62
f3 3d 63
f8 42 64
f9 43 65
f11 57 67
f13 64 69
f16 67 6a
f14 65 6b
f10 44 6d
f12 58 6f
f15 66 71
insert 152 72
home 147 73
page_up 149 74
delete 153 75
f4 3e 76
end 14f 77
f2 3c 78
page_down 151 79
f1 3b 7a
left 14b 7b
right 14d 7c
down 150 7d
up 148 7e
'''
WINDOWS = {}
MACOS = {}
for _row in _ROWS.splitlines():
    if _row.strip():
        _name, _win, _mac = _row.split()
        WINDOWS[_name] = int(_win, 16)
        MACOS[_name] = int(_mac, 16)
WIN_NAMES = {value: name for name, value in WINDOWS.items()}
MAC_NAMES = {value: name for name, value in MACOS.items()}
KEYS = frozenset(WINDOWS)
MODIFIERS = frozenset(('shift_l', 'shift_r', 'ctrl_l', 'ctrl_r', 'alt_l', 'alt_r', 'meta_l', 'meta_r'))


def emergency(keys):
    return ('f12' in keys and any(k.startswith('ctrl_') for k in keys)
            and any(k.startswith('alt_') for k in keys) and any(k.startswith('shift_') for k in keys))

# TCS Wire Format

The Henrietta instrument computer sends guide-offset commands to the Telescope
Control System (TCS) over a TCP/IP socket. The instrument computer acts as a
server; commands are sent over the established connection.

## Command Format

```
G <xx> <yy> <CR> <LF>
```

A 7-byte ASCII record:

| Bytes | Field | Meaning                                    |
|-------|-------|--------------------------------------------|
| 1     | `'G'` | Command identifier (guide offset)          |
| 2-3   | `xx`  | Two-digit signed offset, axis 1            |
| 4-5   | `yy`  | Two-digit signed offset, axis 2            |
| 6     | `\r`  | Carriage return (0x0D)                     |
| 7     | `\n`  | Line feed (0x0A)                           |

## Encoding of the 2-digit offset

Each axis is encoded as a two-character decimal string in the range `00`–`99`.

| Encoded value (n) | Signed offset (units) | Arcseconds (units × 0.05") |
|-------------------|-----------------------|----------------------------|
| `00` … `50`       |  0  … +50             |  0.00" … +2.50"            |
| `51` … `99`       | -1  … -49             | -0.05" … -2.45"            |

Decoding rules:

- `n` in 0–50  → signed value =  `n`
- `n` in 51–99 → signed value =  `50 - n`   (so 51 → -1, 52 → -2, …, 99 → -49)
- arcsec offset = signed_value × 0.05

Encoding rules (signed integer s in [-49, +50]):

- `s ≥ 0`  → `n = s`
- `s < 0`  → `n = 50 - s`     (so -1 → 51, -2 → 52, …, -49 → 99)

### Examples

| Wire bytes (after `G`) | xx → axis 1            | yy → axis 2            |
|------------------------|------------------------|------------------------|
| `0000`                 |  0   → +0.00"          |  0   → +0.00"          |
| `1020`                 | 10   → +0.50"          | 20   → +1.00"          |
| `5099`                 | 50   → +2.50"          | -49  → -2.45"          |
| `9951`                 | -49  → -2.45"          | -1   → -0.05"          |
| `5151`                 | -1   → -0.05"          | -1   → -0.05"          |

## Implications

- **Resolution:** 0.05 arcsec per step.
- **Range per command:** -2.45" to +2.50" on each axis.
- **Larger corrections** must be sent as multiple successive commands (subject to
  TCS slew/settle behavior between commands — TBD).

## Open questions

- Acknowledgment / handshake from TCS: TBD.
- Axis frame (detector X/Y vs sky RA/Dec): TBD. The instrument PA is provided at
  the start of the observation, suggesting the instrument computer performs the
  rotation itself before sending sky-frame offsets — to be confirmed.
- Behavior when an offset is sent while a previous offset is still being applied:
  TBD.

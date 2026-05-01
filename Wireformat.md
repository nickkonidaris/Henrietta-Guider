# TCS Wire Format

The Henrietta instrument computer sends guide-offset commands to the Telescope
Control System (TCS) over a TCP/IP socket. The instrument computer acts as a
server; commands are sent over the established connection.

The format below was reverse-engineered from the TCS-side parser
(`if (xpos > 50) xpos -= 100;` … `ra_guide = xpos * 0.05;`).

## Command Format

```
G <xx> <yy> <CR>
```

A **6-byte** ASCII record (no LF):

| Byte | Field | Meaning                                |
|------|-------|----------------------------------------|
| 0    | `'G'` | Command identifier (guide offset)      |
| 1-2  | `xx`  | Two-digit signed offset, **RA axis**   |
| 3-4  | `yy`  | Two-digit signed offset, **Dec axis**  |
| 5    | `\r`  | Carriage return (0x0D)                 |

The TCS parser checks length ≥ 6 and verifies byte 5 is exactly `0x0D`. A
trailing `\n` is *not* required and *not* validated. (If sent, the parser will
leave it in its receive buffer; benign.)

## Encoding of the 2-digit offset

Each axis is encoded as a two-character decimal string in the range `00`–`99`.
Decoding (matching the TCS parser):

```
n = atoi(xx)         # 0 … 99
if n > 50:
    n -= 100         # n now in -49 … -1
arcsec = n * 0.05    # -2.45" … +2.50"
```

| Encoded value (n) | Signed offset (units) | Arcseconds (units × 0.05") |
|-------------------|-----------------------|----------------------------|
| `00` … `50`       |   0  … +50            |  0.00" … +2.50"            |
| `51` … `99`       | -49  … -1             | -2.45" … -0.05"            |

Encoding (signed integer s in [-49, +50]):

- `s ≥ 0` → `n = s`
- `s < 0` → `n = s + 100`     (so -1 → 99, -2 → 98, …, -49 → 51)

### Examples

| Wire bytes (after `G`) | xx → RA                | yy → Dec               |
|------------------------|------------------------|------------------------|
| `0000`                 |  0   →  +0.00"         |  0   →  +0.00"         |
| `1020`                 | 10   →  +0.50"         | 20   →  +1.00"         |
| `5099`                 | 50   →  +2.50"         | -1   →  -0.05"         |
| `5199`                 | -49  →  -2.45"         | -1   →  -0.05"         |
| `9951`                 | -1   →  -0.05"         | -49  →  -2.45"         |

## Axis convention

The TCS uses **sky-frame** offsets:

```
ra_guide  = xpos * 0.05;   // arcsec
dec_guide = ypos * 0.05;   // arcsec
```

Because the instrument computer measures trace motion in *detector* pixels, it
must convert detector → sky coordinates using the instrument PA before encoding
the command. PA is provided at the start of the observation (and will be
queryable from the instrument later).

## Acknowledgment and pacing

The visible parser code is **fire-and-forget**:

- No ACK / NACK on success.
- A corrupted command (missing CR) is logged on the TCS side but *not* reported
  back to the instrument computer.
- A command is silently ignored unless `!guiding_ra && !guiding_dec &&
  guider_cmd_processing` — i.e., the TCS is not currently slewing in either
  axis and command processing is enabled.

Implications for the autoguider:

- Treat the link as one-way; don't wait for replies.
- After sending a command, allow the TCS time to settle before issuing the
  next one (settle time TBD — must be characterized).
- Some commands may be lost; the closed loop must be tolerant of that.

## Resolution and range

- **Resolution:** 0.05 arcsec per step.
- **Range per command:** -2.45" to +2.50" on each axis.
- Larger corrections must be split across multiple commands (subject to the
  pacing constraint above).

## Open questions

- Settle time between successive `G` commands.
- Is `guider_cmd_processing` toggled by the observer, by an external command,
  or by some other state machine?
- Does the TCS expose status (current pointing, slewing flags) over a
  separate channel that we can poll?

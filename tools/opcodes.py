"""65816 opcode table.

Each entry: (mnemonic, mode). Modes and their operand sizes:
  imp      implied / accumulator        0
  immm     immediate, size follows M     1 or 2
  immx     immediate, size follows X     1 or 2
  imm8     8-bit immediate (REP/SEP/BRK/COP/WDM)  1
  dp dpx dpy dpi dpxi dpiy dpil dpily   1
  sr sriy                                1
  abs absx absy absi absxi absil         2
  long longx                             3
  rel                                    1
  rell                                   2
  mv  (MVN/MVP: dst,src)                 2
"""

_T = """
00 brk imm8|01 ora dpxi|02 cop imm8|03 ora sr|04 tsb dp|05 ora dp|06 asl dp|07 ora dpil|08 php imp|09 ora immm|0A asl acc|0B phd imp|0C tsb abs|0D ora abs|0E asl abs|0F ora long
10 bpl rel|11 ora dpiy|12 ora dpi|13 ora sriy|14 trb dp|15 ora dpx|16 asl dpx|17 ora dpily|18 clc imp|19 ora absy|1A inc acc|1B tcs imp|1C trb abs|1D ora absx|1E asl absx|1F ora longx
20 jsr abs|21 and dpxi|22 jsl long|23 and sr|24 bit dp|25 and dp|26 rol dp|27 and dpil|28 plp imp|29 and immm|2A rol acc|2B pld imp|2C bit abs|2D and abs|2E rol abs|2F and long
30 bmi rel|31 and dpiy|32 and dpi|33 and sriy|34 bit dpx|35 and dpx|36 rol dpx|37 and dpily|38 sec imp|39 and absy|3A dec acc|3B tsc imp|3C bit absx|3D and absx|3E rol absx|3F and longx
40 rti imp|41 eor dpxi|42 wdm imm8|43 eor sr|44 mvp mv|45 eor dp|46 lsr dp|47 eor dpil|48 pha imp|49 eor immm|4A lsr acc|4B phk imp|4C jmp abs|4D eor abs|4E lsr abs|4F eor long
50 bvc rel|51 eor dpiy|52 eor dpi|53 eor sriy|54 mvn mv|55 eor dpx|56 lsr dpx|57 eor dpily|58 cli imp|59 eor absy|5A phy imp|5B tcd imp|5C jml long|5D eor absx|5E lsr absx|5F eor longx
60 rts imp|61 adc dpxi|62 per rell|63 adc sr|64 stz dp|65 adc dp|66 ror dp|67 adc dpil|68 pla imp|69 adc immm|6A ror acc|6B rtl imp|6C jmp absi|6D adc abs|6E ror abs|6F adc long
70 bvs rel|71 adc dpiy|72 adc dpi|73 adc sriy|74 stz dpx|75 adc dpx|76 ror dpx|77 adc dpily|78 sei imp|79 adc absy|7A ply imp|7B tdc imp|7C jmp absxi|7D adc absx|7E ror absx|7F adc longx
80 bra rel|81 sta dpxi|82 brl rell|83 sta sr|84 sty dp|85 sta dp|86 stx dp|87 sta dpil|88 dey imp|89 bit immm|8A txa imp|8B phb imp|8C sty abs|8D sta abs|8E stx abs|8F sta long
90 bcc rel|91 sta dpiy|92 sta dpi|93 sta sriy|94 sty dpx|95 sta dpx|96 stx dpy|97 sta dpily|98 tya imp|99 sta absy|9A txs imp|9B txy imp|9C stz abs|9D sta absx|9E stz absx|9F sta longx
A0 ldy immx|A1 lda dpxi|A2 ldx immx|A3 lda sr|A4 ldy dp|A5 lda dp|A6 ldx dp|A7 lda dpil|A8 tay imp|A9 lda immm|AA tax imp|AB plb imp|AC ldy abs|AD lda abs|AE ldx abs|AF lda long
B0 bcs rel|B1 lda dpiy|B2 lda dpi|B3 lda sriy|B4 ldy dpx|B5 lda dpx|B6 ldx dpy|B7 lda dpily|B8 clv imp|B9 lda absy|BA tsx imp|BB tyx imp|BC ldy absx|BD lda absx|BE ldx absy|BF lda longx
C0 cpy immx|C1 cmp dpxi|C2 rep imm8|C3 cmp sr|C4 cpy dp|C5 cmp dp|C6 dec dp|C7 cmp dpil|C8 iny imp|C9 cmp immm|CA dex imp|CB wai imp|CC cpy abs|CD cmp abs|CE dec abs|CF cmp long
D0 bne rel|D1 cmp dpiy|D2 cmp dpi|D3 cmp sriy|D4 pei dpi|D5 cmp dpx|D6 dec dpx|D7 cmp dpily|D8 cld imp|D9 cmp absy|DA phx imp|DB stp imp|DC jml absil|DD cmp absx|DE dec absx|DF cmp longx
E0 cpx immx|E1 sbc dpxi|E2 sep imm8|E3 sbc sr|E4 cpx dp|E5 sbc dp|E6 inc dp|E7 sbc dpil|E8 inx imp|E9 sbc immm|EA nop imp|EB xba imp|EC cpx abs|ED sbc abs|EE inc abs|EF sbc long
F0 beq rel|F1 sbc dpiy|F2 sbc dpi|F3 sbc sriy|F4 pea abs|F5 sbc dpx|F6 inc dpx|F7 sbc dpily|F8 sed imp|F9 sbc absy|FA plx imp|FB xce imp|FC jsr absxi|FD sbc absx|FE inc absx|FF sbc longx
"""

OPS = [None] * 256
for line in _T.strip().splitlines():
    for ent in line.split("|"):
        code, mn, mode = ent.split()
        OPS[int(code, 16)] = (mn, mode)
assert all(OPS)

FIXED = {"imp": 0, "acc": 0, "imm8": 1, "dp": 1, "dpx": 1, "dpy": 1, "dpi": 1,
         "dpxi": 1, "dpiy": 1, "dpil": 1, "dpily": 1, "sr": 1, "sriy": 1,
         "abs": 2, "absx": 2, "absy": 2, "absi": 2, "absxi": 2, "absil": 2,
         "long": 3, "longx": 3, "rel": 1, "rell": 2, "mv": 2}


def operand_size(mode, m8, x8):
    if mode == "immm":
        return 1 if m8 else 2
    if mode == "immx":
        return 1 if x8 else 2
    return FIXED[mode]


BRANCHES = {"bpl", "bmi", "bvc", "bvs", "bcc", "bcs", "bne", "beq", "bra", "brl"}
TERMINATORS = {"rts", "rtl", "rti", "jmp", "jml", "bra", "brl", "stp"}

; ==============================================================================
; SNESMOD 65816-side driver, ported from snesmod_dev.asm (ca65) to asar.
; SNESMOD (C) 2009 Mukunda Johnson, see tools/snesmod-main/LICENSING.txt.
;
; Differences from the original:
;   * LoROM only. The sample streaming ("digi") part is not included.
;   * Direct-page state lives at $E8-$F8, which GEL leaves unallocated (DP_END).
;   * The command fifo lives at WRAM $0500-$05FF, which GEL leaves unallocated.
;   * The soundbank bank is passed to spcSetBank (A = bank, 8-bit A).
;
; Entry conditions for every routine: A 8-bit, X/Y 16-bit, DB = a bank that
; mirrors the I/O registers, DP = $0000.
; ==============================================================================

!spc_ptr        = $E8       ; 3 bytes: long pointer into the soundbank
!spc_v          = $EB
!spc_bank       = $EC
!spc1           = $ED       ; 2 bytes
!spc2           = $EF       ; 2 bytes
!spc_fread      = $F1
!spc_fwrite     = $F2
!spc_pr         = $F3       ; 4 bytes: last message written to the ports
!spc_q          = $F7
!spc_sfx_next   = $F8
!spc_fifo       = $0500     ; 256 bytes

!REG_APUIO0     = $2140
!REG_APUIO1     = $2141
!REG_APUIO2     = $2142
!REG_APUIO3     = $2143
!REG_SLHV       = $2137
!REG_OPVCT      = $213D

!SB_SAMPCOUNT   = $8000
!SB_MODCOUNT    = $8002
!SB_MODTABLE    = $8004
!SB_SRCTABLE    = $8184

!CMD_LOAD       = $00
!CMD_LOADE      = $01
!CMD_VOL        = $02
!CMD_PLAY       = $03
!CMD_STOP       = $04
!CMD_MVOL       = $05
!CMD_FADE       = $06
!CMD_RES        = $07
!CMD_FX         = $08

!PROCESS_TIME   = 5         ; scanlines spent per spcProcess call
!SPC_BOOT       = $0400     ; SPC entry / load address
; !SPC_LEN (size of snesmod_spc.bin) is defined by the build before this file is included.

; Advance the soundbank pointer by 2 (Y = offset within the bank).
macro incptr()
    INY
    INY
    BMI ?ok
    INC !spc_ptr+2
    LDY.w #$8000
?ok:
endmacro

; ------------------------------------------------------------------------------
; Upload the SPC driver. Call once at startup with the screen blanked.
; ------------------------------------------------------------------------------
spcBoot:
-   LDX !REG_APUIO0         ; wait for the SPC ready signal $BBAA
    CPX.w #$BBAA
    BNE -
    STX !REG_APUIO1         ; start the transfer
    LDX.w #!SPC_BOOT
    STX !REG_APUIO2
    LDA.b #$CC
    STA !REG_APUIO0
-   CMP !REG_APUIO0
    BNE -
    LDA.l snesmod_spc       ; first byte
    XBA
    LDA.b #$00
    LDX.w #$0001
    BRA .start
.send:
    XBA
    LDA.l snesmod_spc,x
    INX
    XBA
-   CMP !REG_APUIO0
    BNE -
    INC A
.start:
    REP #$20
    STA !REG_APUIO0
    SEP #$20
    CPX.w #!SPC_LEN
    BCC .send
-   CMP !REG_APUIO0
    BNE -
    INC A
    INC A
    STZ !REG_APUIO1
    LDX.w #!SPC_BOOT        ; entry point
    STX !REG_APUIO2
    STA !REG_APUIO0
-   CMP !REG_APUIO0
    BNE -
    STZ !REG_APUIO0

    STZ !spc_v
    STZ !spc_q
    STZ !spc_fwrite
    STZ !spc_fread
    STZ !spc_sfx_next
    STZ !spc_pr+0
    STZ !spc_pr+1
    STZ !spc_pr+2
    STZ !spc_pr+3
    RTS

; A = bank number of the soundbank
spcSetBank:
    STA !spc_bank
    RTS

; ------------------------------------------------------------------------------
; Upload a module and its samples. X = module id. Slow: do it while blanked.
; ------------------------------------------------------------------------------
spcLoad:
    PHX                     ; flush the fifo first
    JSR spcFlush
    PLX

    PHX
    LDY.w #!SB_MODTABLE
    STY !spc2
    JSR get_address
    REP #$20
    LDA [!spc_ptr],y        ; X = module size
    TAX
    %incptr()
    LDA [!spc_ptr],y        ; source list size
    %incptr()
    STY !spc1               ; pointer += listsize*2
    ASL A
    ADC !spc1
    BMI +
    ORA.w #$8000
    INC !spc_ptr+2
+   TAY

    SEP #$20
    LDA !spc_v              ; wait for the SPC
    PHA
-   CMP !REG_APUIO1
    BNE -
    LDA.b #!CMD_LOAD
    STA !REG_APUIO0
    PLA
    EOR.b #$80
    ORA.b #$01
    STA !spc_v
    STA !REG_APUIO1
-   CMP !REG_APUIO1
    BNE -
    JSR do_transfer

    ; transfer the module's sources
    PLX
    LDY.w #!SB_MODTABLE
    STY !spc2
    JSR get_address
    %incptr()

    REP #$20
    LDA [!spc_ptr],y        ; X = number of sources
    TAX
    %incptr()

.next_source:
    LDA [!spc_ptr],y        ; source index
    STA !spc1
    %incptr()

    PHY
    SEP #$20
    LDA !spc_ptr+2
    PHA
    PHX
    JSR transfer_source
    PLX
    PLA
    STA !spc_ptr+2
    PLY

    DEX
    BNE .next_source

    STZ !REG_APUIO0         ; end of transfers
    LDA !spc_v
    EOR.b #$80
    STA !spc_v
    STA !REG_APUIO1
-   CMP !REG_APUIO1
    BNE -
    STA !spc_pr+1
    STZ !spc_sfx_next       ; reset the sfx slot counter
    RTS

; !spc1 = source index
transfer_source:
    LDX !spc1
    LDY.w #!SB_SRCTABLE
    STY !spc2
    JSR get_address

    LDA.b #$01
    STA !REG_APUIO0
    REP #$20
    LDA [!spc_ptr],y        ; length: bytes -> words
    %incptr()
    INC A
    LSR A
    TAX
    LDA [!spc_ptr],y        ; loop point
    STA !REG_APUIO2
    %incptr()
    SEP #$20

    LDA !spc_v
    EOR.b #$80
    ORA.b #$01
    STA !spc_v
    STA !REG_APUIO1
-   CMP !REG_APUIO1
    BNE -
    CPX.w #$0000
    BEQ end_transfer
    BRA do_transfer

; spc_ptr + Y = source address, X = length in words
transfer_again:
    EOR.b #$80
    STA !REG_APUIO1
    STA !spc_v
    %incptr()
-   CMP !REG_APUIO1
    BNE -
do_transfer:
    REP #$20
    LDA [!spc_ptr],y
    STA !REG_APUIO2
    SEP #$20
    LDA !spc_v
    DEX
    BNE transfer_again
    %incptr()

end_transfer:
    LDA.b #$00
    STA !REG_APUIO1
    STA !spc_v
-   CMP !REG_APUIO1
    BNE -
    STA !spc_pr+1
    RTS

; !spc2 = table offset, X = index
; Returns spc_ptr = bank:0000 of the entry, Y = address within the bank.
get_address:
    LDA !spc_bank
    STA !spc_ptr+2
    REP #$20
    STX !spc1
    TXA
    ASL A
    ADC !spc1
    ADC !spc2
    STA !spc_ptr
    LDA [!spc_ptr]
    PHA
    SEP #$20
    LDY.w #$0002
    LDA [!spc_ptr],y
    CLC
    ADC !spc_bank
    STA !spc_ptr+2
    PLY
    STZ !spc_ptr
    STZ !spc_ptr+1
    RTS

; ------------------------------------------------------------------------------
; Load a sound effect sample. X = source id. Returns its effect slot in A.
; Slow: do it while blanked.
; ------------------------------------------------------------------------------
spcLoadEffect:
    LDY.w #!SB_SRCTABLE
    STY !spc2
    JSR get_address
    LDA !spc_v
-   CMP !REG_APUIO1
    BNE -
    LDA.b #!CMD_LOADE
    STA !REG_APUIO0
    LDA !spc_v
    EOR.b #$80
    ORA.b #$01
    STA !spc_v
    STA !REG_APUIO1
-   CMP !REG_APUIO1
    BNE -
    REP #$20
    LDA [!spc_ptr],y        ; length: bytes -> words
    INC A
    LSR A
    %incptr()
    TAX
    %incptr()               ; skip the loop point
    SEP #$20
    JSR do_transfer
    LDA !spc_sfx_next
    INC !spc_sfx_next
    RTS

; A = command, !spc1 = parameters
QueueMessage:
    PHP
    SEI
    SEP #$10                ; queue in the fifo with an 8-bit index
    LDX !spc_fwrite
    STA !spc_fifo,x
    INX
    LDA !spc1
    STA !spc_fifo,x
    INX
    LDA !spc1+1
    STA !spc_fifo,x
    INX
    STX !spc_fwrite
    PLP
    RTS

; Wait until every queued message has been sent.
spcFlush:
    LDA !spc_fread
    CMP !spc_fwrite
    BEQ .done
    JSR spcProcessMessages
    BRA spcFlush
.done:
    RTS

; ------------------------------------------------------------------------------
; Send queued messages for up to PROCESS_TIME scanlines. Call once per frame.
; ------------------------------------------------------------------------------
spcProcess:
spcProcessMessages:
    SEP #$10                ; 8-bit index inside this routine
    LDA !spc_fwrite
    CMP !spc_fread
    BEQ .exit
    LDY.b #!PROCESS_TIME
.again:
    LDA !spc_v              ; is the SPC ready?
    CMP !REG_APUIO1
    BNE .next
    LDX !spc_fread
    LDA !spc_fifo,x
    STA !REG_APUIO0
    STA !spc_pr+0
    INX
    LDA !spc_fifo,x
    STA !REG_APUIO2
    STA !spc_pr+2
    INX
    LDA !spc_fifo,x
    STA !REG_APUIO3
    STA !spc_pr+3
    INX
    STX !spc_fread
    LDA !spc_v              ; dispatch the message
    EOR.b #$80
    STA !spc_v
    STA !REG_APUIO1
    STA !spc_pr+1
    LDA !spc_fread
    CMP !spc_fwrite
    BEQ .exit
.next:
    LDA !REG_SLHV           ; latch the counters and wait for the scanline to change
    LDA !REG_OPVCT
    CMP !spc1
    BEQ .again
    STA !spc1
    DEY
    BNE .again
.exit:
    REP #$10
    RTS

; X = starting pattern position
spcPlay:
    TXA
    STA !spc1+1
    LDA.b #!CMD_PLAY
    JMP QueueMessage

spcStop:
    LDA.b #!CMD_STOP
    JMP QueueMessage

; Module status (port 2).
spcReadStatus:
    LDX.w #$0005
    LDA !REG_APUIO2
.loop:
    CMP !REG_APUIO2
    BNE spcReadStatus
    DEX
    BNE .loop
    RTS

; Module position (port 3).
spcReadPosition:
    LDX.w #$0005
    LDA !REG_APUIO3
.loop:
    CMP !REG_APUIO3
    BNE spcReadPosition
    DEX
    BNE .loop
    RTS

; Returns the number of music cues since the last call.
spcGetCues:
    LDA !spc_q
    STA !spc1
    JSR spcReadStatus
    AND.b #$0F
    STA !spc_q
    SEC
    SBC !spc1
    BCS +
    ADC.b #$10
+   RTS

; X = module volume (0-255)
spcSetModuleVolume:
    TXA
    STA !spc1+1
    LDA.b #!CMD_MVOL
    JMP QueueMessage

; X = target volume, Y = speed
spcFadeModuleVolume:
    TXA
    STA !spc1+1
    TYA
    STA !spc1
    LDA.b #!CMD_FADE
    JMP QueueMessage

; A = volume*16 + panning, X = effect slot (from spcLoadEffect), Y = pitch (0-15, 8 = 32 kHz)
spcEffect:
    STA !spc1
    STY !spc2
    TXA
    ASL A
    ASL A
    ASL A
    ASL A
    ORA !spc2
    STA !spc1+1
    LDA.b #!CMD_FX
    JMP QueueMessage

snesmod_code_end_driver:

; ------------------------------------------------------------------------------
; GEL entry points (stdlib/sound.gel). GEL code runs with a 16-bit accumulator and calls
; these with arguments in the mailbox (direct page $F9-$FE); they return with a 16-bit A.
; ------------------------------------------------------------------------------
!snd_a = $F9
!snd_x = $FA
!snd_y = $FC

gel_snd_init:
    SEP #$20
    JSR spcBoot
    LDA !snd_a
    JSR spcSetBank
    REP #$20
    RTS

gel_snd_update:
    SEP #$20
    JSR spcProcess
    REP #$20
    RTS

gel_snd_sfx_load:
    SEP #$20
    LDX !snd_x
    JSR spcLoadEffect
    REP #$20
    AND.w #$00FF
    RTS

gel_snd_sfx_play:
    SEP #$20
    LDA !snd_a
    LDX !snd_x
    LDY !snd_y
    JSR spcEffect
    REP #$20
    RTS

gel_snd_music_load:
    SEP #$20
    LDX !snd_x
    JSR spcLoad
    REP #$20
    RTS

gel_snd_music_play:
    SEP #$20
    LDX !snd_x
    JSR spcPlay
    REP #$20
    RTS

gel_snd_music_stop:
    SEP #$20
    JSR spcStop
    REP #$20
    RTS

gel_snd_music_volume:
    SEP #$20
    LDX !snd_x
    JSR spcSetModuleVolume
    REP #$20
    RTS

gel_snd_music_fade:
    SEP #$20
    LDX !snd_x
    LDY !snd_y
    JSR spcFadeModuleVolume
    REP #$20
    RTS

gel_snd_music_cues:
    SEP #$20
    JSR spcGetCues
    REP #$20
    AND.w #$00FF
    RTS

snesmod_code_end:

"""Host side of the opt-in SHARC peripheral model (sharc_core/periph.py).

spi2_exchange() is one full-duplex SPI2 slave frame on a Python State: the
master's bytes land in the RX DMA work unit and the TX work unit's bytes go
back, then both channels complete (SEC sources 69 and 70). Each 16-bit SPI
word is MSB first on the wire (SPI_CTL.LSBF=0) and little-endian in memory
(16-bit MSIZE), so every 16-bit unit is byte-swapped in both directions.
"""

from __future__ import annotations

from tools.sharc_dev.sharc_core.periph import (
    SID_SPORT4A_DMA,
    SID_SPORT4B_DMA,
    SPORT4A_DMA,
    SPORT4B_DMA,
    _dma_done,
    _dma_start,
    _mmr,
    _ram_word,
    _sport_running,
)
from tools.sharc_dev.sharc_core.state import State

SPI2_TX_DMA = 0x3102D200
SPI2_RX_DMA = 0x3102D280
SID_SPI2_TXDMA = 69
SID_SPI2_RXDMA = 70


def swap16(data: bytes) -> bytes:
    out = bytearray(len(data))
    out[0::2] = data[1::2]
    out[1::2] = data[0::2]
    return bytes(out)


def spi2_exchange(state: State, frame: bytes) -> bytes:
    """Deliver FRAME (wire order) and return the DSP's reply (wire order)."""
    rx = _dma_start(state, SPI2_RX_DMA)
    tx = _dma_start(state, SPI2_TX_DMA)
    for base in (SPI2_RX_DMA, SPI2_TX_DMA):
        if _mmr(state, base + 0x10) != 2:
            raise ValueError("SPI2 DMA X modify is not 2 bytes")
        if 2 * _mmr(state, base + 0x0C) != len(frame):
            raise ValueError("SPI2 frame size does not match the work unit")
    if len(frame) % 4:
        raise ValueError("SPI2 frame is not a whole number of words")
    reply = bytearray()
    for offset in range(0, len(frame), 4):
        reply += _ram_word(state, tx + offset).to_bytes(4, "little")
    for offset, byte in enumerate(swap16(frame)):
        state.overlay[rx + offset] = byte
    _dma_done(state, SPI2_TX_DMA, SID_SPI2_TXDMA)
    _dma_done(state, SPI2_RX_DMA, SID_SPI2_RXDMA)
    return swap16(bytes(reply))


# DMA10 (SPORT4A) transmits (memory read), DMA11 (SPORT4B) receives.
DMA_WNR = 0x2  # DMA_CFG.WNR: the channel writes memory (an input port)


def _sport_unit(state: State, base: int, writes: bool) -> int:
    """Validate a SPORT4 work unit; its size in bytes (contiguous words)."""
    cfg = _mmr(state, base + 0x08)
    size = 1 << ((cfg >> 4) & 7)
    if bool(cfg & DMA_WNR) != writes:
        raise ValueError("SPORT4 DMA direction is not the expected one")
    if _mmr(state, base + 0x10) != size or size != 4:
        raise ValueError("SPORT4 DMA is not contiguous 32-bit words")
    return _mmr(state, base + 0x0C) * size


def sport_block(state: State, block: bytes | None = None) -> bytes | None:
    """One audio block through SPORT4A (output) and SPORT4B (input).

    Returns None, changing nothing, while the SPORTs are not running
    (DAI1_GBL_SP_EN.GBL_SP_EN and both SPORT_CTL.SPEN set). Otherwise the
    DMA10 (TX) work unit's bytes come back unconverted (little-endian 32-bit
    words as in memory), BLOCK (default zeros, the DMA11 work unit's size)
    lands in the DMA11 (RX) work unit, and both channels complete: IRQDONE
    is set and, with the DAI1 group enabled, the group source (SID 191) is
    raised once both are done. Rejected cases raise ValueError.
    """
    if not _sport_running(state):
        return None
    tx = _dma_start(state, SPORT4A_DMA)
    rx = _dma_start(state, SPORT4B_DMA)
    out_size = _sport_unit(state, SPORT4A_DMA, False)
    in_size = _sport_unit(state, SPORT4B_DMA, True)
    if block is None:
        block = bytes(in_size)
    if len(block) != in_size:
        raise ValueError("input block does not match the DMA11 work unit")
    reply = bytearray()
    for offset in range(0, out_size, 4):
        reply += _ram_word(state, tx + offset).to_bytes(4, "little")
    _dma_done(state, SPORT4A_DMA, SID_SPORT4A_DMA)
    _dma_done(state, SPORT4B_DMA, SID_SPORT4B_DMA)
    for offset, byte in enumerate(block):
        state.overlay[rx + offset] = byte
    return bytes(reply)

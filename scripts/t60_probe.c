/* T6.0 proof of concept, DSP side: new DSP code reads the extra parameter words the ColdFire
   put in the SPI frame's tail (bytes 0xa80..0xabb, 30 big-endian 16-bit words on the wire,
   which land as 16-bit words at the same byte offsets of the DSP's receive buffer).
   `rx` is the receive buffer the block handler gave the render (0x2c29d0 or 0x2c39d0).
   Each word becomes a float in 0..1 with the stock scale (1/32512 = 1/0x7f00, T3.4) and is
   stored in `out`, as a new effect's parameter struct would be filled. Built with selcc
   for the ADSP-21569 and placed in free L1 by scripts/t60_probe.ldf. */
#define TAIL_OFFSET 0xa80
#define TAIL_WORDS 30

void t60_probe(const unsigned short *rx, float *out)
{
    const unsigned short *tail = rx + TAIL_OFFSET / 2;
    for (int i = 0; i < TAIL_WORDS; i++)
        out[i] = (float)tail[i] * (1.0f / 32512.0f);
}

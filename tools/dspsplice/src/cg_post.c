/* cg_post.c - T3.8 milestone 1, the first DSP patch: after the stock chorus has run, halve its
 * wet output (-6.02 dB) in place. Called from the hook stub (tools/dspsplice/stub.py) with
 * R4 = the FX bus's I/O descriptor. A multiply by 0.5 is exact in float, so the patched chorus
 * output must be exactly half the stock one, sample for sample (the proof in verify.py). */
#include "fxio.h"

void cg_post(const FxIo *io)
{
    float *l = io->out[0];
    float *r = io->out[1];
    int n = io->n;
    int i;
    for (i = 0; i < n; i++) {
        l[i] = l[i] * 0.5f;
        r[i] = r[i] * 0.5f;
    }
}

/* sharc_svf_block.c - selcc experiment for T3.11 (not part of the effects): one SVF written as a
   32-sample loop with its state in locals, the shape the stock code uses. Compare the loop body
   with fx_svf_tick in out/T3.11/sharc/mbcomp.s. Build: make sharc. */
typedef struct { float ic1, ic2; } FxSvf;
typedef struct { float a1, a2, a3, k; } FxSvfCoef;
/* macro version, state in locals for the whole block */
void svf_block(FxSvf *st, const FxSvfCoef *c, const float *x, float *y, int n)
{
    float ic1 = st->ic1, ic2 = st->ic2, a1 = c->a1, a2 = c->a2, a3 = c->a3;
    int i;
    for (i = 0; i < n; i++) {
        float v3 = x[i] - ic2;
        float v1 = a1 * ic1 + a2 * v3;
        float v2 = ic2 + a2 * ic1 + a3 * v3;
        ic1 = 2.0f * v1 - ic1;
        ic2 = 2.0f * v2 - ic2;
        y[i] = v2;
    }
    st->ic1 = ic1; st->ic2 = ic2;
}

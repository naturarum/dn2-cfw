/* wav.h - minimal WAV reader/writer for the Mac tools (not for the DSP). */
#ifndef WAV_H
#define WAV_H

typedef struct {
    int rate;
    long frames;
    float *l, *r;   /* mono files give l == r contents (two buffers) */
} Wav;

/* reads PCM 16/24/32 and float 32, mono or stereo (more channels: first two). 0 on success */
int wav_read(const char *path, Wav *w);
/* bits: 16 (TPDF-dithered), 24, or 32 (float). 0 on success */
int wav_write(const char *path, const float *l, const float *r, long frames, int rate, int bits);
int wav_alloc(Wav *w, long frames, int rate);
void wav_free(Wav *w);

#endif

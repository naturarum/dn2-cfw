# SPDX-License-Identifier: GPL-2.0-or-later
import wave, math, array, sys
def rd(p):
    with wave.open(p) as w:
        n=w.getnframes(); ch=w.getnchannels(); sw=w.getsampwidth(); b=w.readframes(n)
    if sw==2:
        a=array.array('h'); a.frombytes(b); x=[v/32768 for v in a]
    elif sw==3:
        x=[]
        for i in range(0,len(b),3):
            v=b[i]|(b[i+1]<<8)|(b[i+2]<<16)
            if v>=1<<23: v-=1<<24
            x.append(v/8388608)
    else:
        a=array.array('i'); a.frombytes(b); x=[v/2**31 for v in a]
    if ch==1: return x,x
    return x[0::2],x[1::2]
def bq(x,b,a):
    y=[0.0]*len(x); x1=x2=y1=y2=0.0
    b0,b1,b2=b; _,a1,a2=a
    for i,v in enumerate(x):
        o=b0*v+b1*x1+b2*x2-a1*y1-a2*y2; x2=x1;x1=v;y2=y1;y1=o; y[i]=o
    return y
K1=([1.53512485958697,-2.69169618940638,1.19839281085285],[1,-1.69065929318241,0.73248077421585])
K2=([1,-2,1],[1,-1.99004745483398,0.99007225036621])
def lufs(L,R):
    s=0
    for c in (L,R):
        y=bq(bq(c,*K1),*K2); s+=sum(v*v for v in y)/len(y)
    return -0.691+10*math.log10(s+1e-30)
def lp1(x,fc):
    k=1-math.exp(-2*math.pi*fc/48000); y=[];s=0
    for v in x: s+=k*(v-s); y.append(s)
    return y
def main():
    base="out/T3.11/"
    for name,dry in [(a,b) for a,b in [tuple(t.split(':')) for t in sys.argv[1:]]]:
        dL,dR=rd(base+'dry/'+dry+'.wav'); g=10**(-12/20)
        for pre in ['','stock_']:
            L,R=rd(base+'wav/'+pre+name+'.wav')
            n=len(L); wL=[(L[i]-(dL[i]*g if i<len(dL) else 0))*127/64 for i in range(n)]; wR=[(R[i]-(dR[i]*g if i<len(dR) else 0))*127/64 for i in range(n)]
            e=sum(v*v for v in wL)+sum(v*v for v in wR)
            rms=10*math.log10(e/(2*n)+1e-30); pk=20*math.log10(max(max(map(abs,wL)),max(map(abs,wR)))+1e-30)
            m=[(a+b)*0.5 for a,b in zip(wL,wR)]
            l3=lp1(lp1(m,300),300); l3k=lp1(lp1(m,3000),3000)
            E=sum(v*v for v in m)+1e-30; Elo=sum(v*v for v in l3)/E; Emid=sum(v*v for v in l3k)/E-Elo
            print(f"{pre+name:16s} wet RMS {rms:6.1f} dBFS  peak {pk:6.1f}  LUFS {lufs(wL,wR):6.1f}  <300 {Elo:4.0%}  300-3k {Emid:4.0%}  >3k {1-Elo-Emid:4.0%}",flush=True)
        print(f"{'dry -12 dB':16s} LUFS {lufs([v*g for v in dL],[v*g for v in dR]):6.1f}",flush=True)
if __name__=='__main__': main()

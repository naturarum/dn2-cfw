# SPDX-License-Identifier: GPL-2.0-or-later
# echo-train model of three springs: where do their echoes pile up? (lags 150..600 ms of the log envelope)
import math, itertools
FS=12000.0
def mode_map(mode):
    A=[(30,110,0.85),(64,64,1.0),(127,16,1.2)]
    if mode>64: f=(127-mode)/63; a,b=A[2],A[1]
    else: f=mode/64; a,b=A[0],A[1]
    return [a[i]+(b[i]-a[i])*f for i in range(3)]
def score(r2,r3,mode,ret=False):
    drip,tens,spr=mode_map(mode)
    half=50.4e-3*2**(-1.8*tens/127)*FS
    q=(drip/127)*(2-drip/127)
    D=[2*round((1+(r-1)*spr)*half) for r in (1.0,r2,r3)]
    dec=3*math.log2(10)/(2.2*FS)
    H=4e-3; n=int(2.0/H); env=[1e-9]*n
    for k,d in enumerate(D):
        g=2**(-dec*(d+32)); a=1.0; t=d/2
        s1=0.6e-3+6.0e-3*q   # smear added per trip (s), grows with DRIP
        for m in range(60):
            c=t/FS; sig=1e-3+s1*math.sqrt(m+1)
            i0=max(0,int((c-3*sig)/H)); i1=min(n,int((c+3*sig)/H)+1)
            for i in range(i0,i1):
                x=(i*H-c)/sig; env[i]+=a*a*math.exp(-0.5*x*x)/sig
            t+=d; a*=g
    e=[10*math.log10(v) for v in env][25:475]
    N=len(e); mx=(N-1)/2; my=sum(e)/N
    b=sum((x-mx)*(y-my) for x,y in enumerate(e))/sum((x-mx)**2 for x in range(N))
    det=[y-(my+b*(x-mx)) for x,y in enumerate(e)]; var=sum(v*v for v in det)/N
    best=max(sum(det[i]*det[i+L] for i in range(N-L))/((N-L)*var) for L in range(38,151))
    return best
modes=[0,16,32,48,64,80,96,112,127]
def total(r2,r3): 
    s=[score(r2,r3,m) for m in modes]; return max(s), sum(s)/len(s)
print("current 1.173 1.387:", [round(score(1.173,1.387,m),2) for m in modes])
cands=[]
for r2 in [1.10+0.01*i for i in range(16)]:
    for r3 in [1.28+0.01*i for i in range(22)]:
        if r3-r2<0.1: continue
        mx,av=total(r2,r3); cands.append((mx,av,r2,r3))
cands.sort()
for c in cands[:8]: print("max %.2f mean %.2f  r2 %.3f r3 %.3f"%c)

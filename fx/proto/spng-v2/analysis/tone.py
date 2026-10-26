# SPDX-License-Identifier: GPL-2.0-or-later
import sys, math
sys.path.insert(0, sys.argv[1]); from splevel import rd
SR=48000
def bp(x,lo,hi):
    # two one-pole high-passes at lo, two low-passes at hi
    def lp(x,fc):
        k=1-math.exp(-2*math.pi*fc/SR); s=0; y=[]
        for v in x: s+=k*(v-s); y.append(s)
        return y
    h=lp(lp(x,hi),hi); l=lp(lp(h,lo),lo)
    return [a-b for a,b in zip(h,l)]
def slope(y,t0):
    H=int(0.02*SR); pts=[]
    for i in range(int(0.15*SR),int(1.5*SR),H):
        e=sum(v*v for v in y[t0+i:t0+i+H])/H; pts.append((i/SR,10*math.log10(e+1e-20)))
    n=len(pts); mx=sum(p[0] for p in pts)/n; my=sum(p[1] for p in pts)/n
    b=sum((p[0]-mx)*(p[1]-my) for p in pts)/sum((p[0]-mx)**2 for p in pts)
    return b, -60/b
dL,_=rd(sys.argv[2]); t0=next(i for i,v in enumerate(dL) if abs(v)>0.05)
for p in sys.argv[3:]:
    L,R=rd(p); w=[(a+b)*0.5 for a,b in zip(L[:t0+2*SR],R[:t0+2*SR])]
    out=[]
    for lo,hi in [(150,500),(500,1500),(1500,4000)]:
        y=bp(w,lo,hi); b,rt=slope(y,t0); e=10*math.log10(sum(v*v for v in y[t0:t0+SR])+1e-20)
        out.append(f"{lo}-{hi} Hz: RT60 {rt:4.2f} s, level {e:6.1f}")
    print(f"{p.split('/')[-1]:14s} "+" | ".join(out), flush=True)

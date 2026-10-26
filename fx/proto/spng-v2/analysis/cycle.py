# SPDX-License-Identifier: GPL-2.0-or-later
import sys, math
sys.path.insert(0, sys.argv[1]); from splevel import rd
SR=48000
def hits(d):
    on=[];last=-10**9
    for i,v in enumerate(d):
        if abs(v)>0.05 and i-last>SR: on.append(i); last=i
    return on
def env_db(seg,H):
    return [10*math.log10(sum(v*v for v in seg[i:i+H])/H+1e-20) for i in range(0,len(seg)-H,H)]
def acf(det,lags):
    n=len(det); m=sum(det)/n; det=[x-m for x in det]; var=sum(x*x for x in det)/n
    return [(L,sum(det[i]*det[i+L] for i in range(n-L))/((n-L)*var)) for L in lags]
def analyse(w,on):
    # 4 ms envelope, 100 ms .. 1.9 s, detrended by a straight-line fit in dB (the decay)
    H=192; res=[]
    for t in on:
        e=env_db(w[t:t+int(2.0*SR)],H)[25:475]
        n=len(e); xs=range(n); mx=(n-1)/2; my=sum(e)/n
        b=sum((x-mx)*(y-my) for x,y in zip(xs,e))/sum((x-mx)**2 for x in xs)
        res.append([y-(my+b*(x-mx)) for x,y in zip(xs,e)])
    lags=range(4,151)   # 16 .. 600 ms
    acs=[acf(r,lags) for r in res]
    av=[(L*4, sum(a[i][1] for a in acs)/len(acs)) for i,L in enumerate(lags)]
    sd=sum(math.sqrt(sum(x*x for x in r)/len(r)) for r in res)/len(res)
    # local maxima, top 3
    pk=[av[i] for i in range(1,len(av)-1) if av[i][1]>av[i-1][1] and av[i][1]>=av[i+1][1]]
    pk.sort(key=lambda t:-t[1])
    slow=max(c for L,c in av if L>=150)
    return sd, pk[:3], slow
dL,dR=rd(sys.argv[2]); on=hits(dL)[:3]
for a in sys.argv[3:]:
    path,mode=a.split('@'); L,R=rd(path)
    if mode=='send':
        g=10**(-12/20); w=[((L[i]+R[i])*0.5-(dL[i]*g if i<len(dL) else 0)) for i in range(len(L))]
    else: w=[(L[i]+R[i])*0.5 for i in range(len(L))]
    sd,pk,slow=analyse(w,on)
    print(f"{path.split('/')[-1]:20s} swing {sd:4.2f} dB  pulse >=150 ms: {slow:4.2f}   peaks: "+"  ".join(f"{L} ms {c:.2f}" for L,c in pk), flush=True)

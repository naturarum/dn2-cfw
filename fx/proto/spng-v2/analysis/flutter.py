# SPDX-License-Identifier: GPL-2.0-or-later
import sys, math, wave, array
sys.path.insert(0, sys.argv[1]); from splevel import rd
SR=48000
def hits(d):
    on=[];last=-10**9
    for i,v in enumerate(d):
        if abs(v)>0.05 and i-last>SR: on.append(i); last=i
    return on
def metrics(w, t0):
    seg=w[t0:t0+int(1.6*SR)]
    # 1 ms RMS envelope (dB)
    H=48; env=[]
    for i in range(0,len(seg)-H,H):
        e=sum(v*v for v in seg[i:i+H])/H; env.append(10*math.log10(e+1e-20))
    # detrend with a 60 ms centred moving average, from 60 ms to 900 ms
    W=60; lo,hi=60,900
    det=[env[i]-sum(env[i-W//2:i+W//2])/W for i in range(lo,hi)]
    m=sum(det)/len(det); det=[x-m for x in det]; var=sum(x*x for x in det)/len(det)
    ac=[(L,sum(det[i]*det[i+L] for i in range(len(det)-L))/((len(det)-L)*var)) for L in range(15,151)]
    Lb,ab=max(ac,key=lambda t:t[1])
    # normalised echo density (Abel-Huang), 20 ms windows, mean over 50..500 ms
    N=960; ned=[]
    for c in range(int(0.05*SR), int(0.5*SR), N//2):
        x=seg[c-N//2:c+N//2]; wts=[0.5-0.5*math.cos(2*math.pi*(k+0.5)/N) for k in range(N)]; sw=sum(wts)
        sd=math.sqrt(sum(wv*v*v for wv,v in zip(wts,x))/sw)
        out=sum(wv for wv,v in zip(wts,x) if abs(v)>sd)/sw
        ned.append(out/0.3173)
    return math.sqrt(var), Lb, ab, sum(ned)/len(ned)
dL,dR=rd(sys.argv[2]); on=hits(dL)[:3]
for path,mode in [(a.split('@')[0],a.split('@')[1]) for a in sys.argv[3:]]:
    L,R=rd(path)
    if mode=='send':
        g=10**(-12/20); w=[((L[i]+R[i])*0.5-(dL[i]*g if i<len(dL) else 0))*127/64 for i in range(len(L))]
    else: w=[(L[i]+R[i])*0.5 for i in range(len(L))]
    res=[metrics(w,t) for t in on]
    f=sum(r[0] for r in res)/len(res); ned=sum(r[3] for r in res)/len(res)
    print(f"{path.split('/')[-1]:22s} flutter {f:5.2f} dB   strongest repeat {res[0][1]:3d} ms (corr {res[0][2]:.2f})   echo density {ned:.2f} (1 = smooth)")
